"""`Idempotency-Key` (md 2B) through the real API with RLS on: a repeat returns what the
first request made, even when the repeats arrive at the same moment; the same key with a
different request is refused; a request that failed doesn't use up its key."""

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.idempotency import sweep_everywhere
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
TICKET = {"subject": "Where is my order?", "contact": {"email": "ana@shop.test", "name": "Ana"}}


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    await add(client, token, "neha@example.com", "agent", "Neha")
    return {"admin": bearer(token), "ws": UUID(token.split(".")[0]),
            "neha": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


def keyed(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


async def count(sm: Sm, ws: UUID, table: str) -> int:
    sql = {"ticket": "SELECT count(*) FROM hd_ticket",
           "reply": "SELECT count(*) FROM hd_message WHERE kind = 'agent'",
           "file": "SELECT count(*) FROM hd_attachment"}[table]  # fmt: skip
    async with workspace_session(sm, ws) as s:
        return int(await s.scalar(text(sql)) or 0)


async def test_a_repeated_create_returns_the_same_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    first = await client.post(T, headers=keyed(w["admin"], "create-1"), json=TICKET)
    again = await client.post(T, headers=keyed(w["admin"], "create-1"), json=TICKET)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json()["id"] == first.json()["id"]
    assert "Idempotent-Replayed" not in first.headers
    assert again.headers["Idempotent-Replayed"] == "true"
    assert await count(app_sessionmaker, w["ws"], "ticket") == 1

    # Keys are per member: Neha's same key is her own request.
    ok(await client.post(T, headers=keyed(w["neha"], "create-1"), json=TICKET), 201)
    # No key: always a new ticket.
    ok(await client.post(T, headers=w["admin"], json=TICKET), 201)
    assert await count(app_sessionmaker, w["ws"], "ticket") == 3


async def test_repeats_at_the_same_moment_make_one_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    answers = await asyncio.gather(*[
        client.post(T, headers=keyed(w["admin"], "burst"), json=TICKET) for _ in range(6)
    ])  # fmt: skip
    assert {r.status_code for r in answers} == {201}
    assert len({r.json()["id"] for r in answers}) == 1
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in answers) == 5
    assert await count(app_sessionmaker, w["ws"], "ticket") == 1


async def test_the_same_key_for_another_request_is_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.post(T, headers=keyed(w["admin"], "k"), json=TICKET), 201)
    r = await client.post(T, headers=keyed(w["admin"], "k"), json={**TICKET, "subject": "Other"})
    assert r.status_code == 422 and "different request" in r.text
    for bad in ("has space", "x" * 256, ""):
        r = await client.post(T, headers=keyed(w["admin"], bad), json=TICKET)
        assert r.status_code == 422, bad


async def test_a_failed_request_does_not_use_up_its_key(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    missing = {**TICKET, "team_id": "00000000-0000-0000-0000-000000000000"}
    r = await client.post(T, headers=keyed(w["admin"], "retry-me"), json=missing)
    assert r.status_code in (404, 422)
    # The failed attempt left no key, so the corrected request goes through with it.
    ok(await client.post(T, headers=keyed(w["admin"], "retry-me"), json=TICKET), 201)


async def test_a_repeated_reply_is_saved_and_emailed_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    domain = "acme-" + str(w["ws"])[:8] + ".test"
    ok(await client.post("/api/v1/email-addresses", headers=w["admin"],
                         json={"address": f"support@{domain}", "name": "Acme"}), 201)  # fmt: skip
    t = ok(await client.post(T, headers=w["admin"], json={**TICKET, "channel": "email"}), 201)
    url, body = f"{T}/{t['id']}/messages", {"kind": "reply", "body": "Shipped today"}
    answers = await asyncio.gather(*[
        client.post(url, headers=keyed(w["admin"], "reply-1"), json=body) for _ in range(3)
    ])  # fmt: skip
    assert {r.status_code for r in answers} == {201}
    assert len({r.json()["id"] for r in answers}) == 1
    assert await count(app_sessionmaker, w["ws"], "reply") == 1
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        assert await s.scalar(text("SELECT count(*) FROM hd_email_out")) == 1
    # The same key on another ticket's messages is another request.
    t2 = ok(await client.post(T, headers=w["admin"], json=TICKET), 201)
    r = await client.post(f"{T}/{t2['id']}/messages", headers=keyed(w["admin"], "reply-1"),
                          json=body)  # fmt: skip
    assert r.status_code == 422


async def test_a_repeated_upload_stores_one_file(
    client: AsyncClient, app_sessionmaker: Sm, tmp_path: Path
) -> None:
    w = await desk(client, app_sessionmaker)
    t = ok(await client.post(T, headers=w["admin"], json=TICKET), 201)
    url = f"{T}/{t['id']}/attachments"
    headers = {**keyed(w["admin"], "up-1"), "Content-Type": "image/png"}
    first = ok(await client.post(url, params={"name": "a.png"}, content=b"png", headers=headers),
               201)  # fmt: skip
    again = await client.post(url, params={"name": "a.png"}, content=b"png", headers=headers)
    assert again.status_code == 201 and again.json()["id"] == first["id"]
    other = await client.post(url, params={"name": "a.png"}, content=b"PNG2", headers=headers)
    assert other.status_code == 422  # same key, other bytes
    assert await count(app_sessionmaker, w["ws"], "file") == 1


async def test_keys_expire_after_a_day(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    first = ok(await client.post(T, headers=keyed(w["admin"], "old"), json=TICKET), 201)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_idempotency SET created_at = now() - interval '25 h'"))
    second = ok(await client.post(T, headers=keyed(w["admin"], "old"), json=TICKET), 201)
    assert second["id"] != first["id"]  # an expired key is new again

    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_idempotency SET created_at = now() - interval '25 h'"))
    assert await sweep_everywhere(app_sessionmaker) >= 1
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        assert await s.scalar(text("SELECT count(*) FROM hd_idempotency")) == 0
