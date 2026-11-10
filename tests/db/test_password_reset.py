"""Forgot password (md A3) with RLS on: the same answer whatever happens, a one-time
30-minute link by email, the new password signs out everywhere and unlocks, and the
link's text is wiped from the queue once sent."""

import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.email import send_due
from cedesk.mail import OutgoingEmail
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

F = "/api/v1/auth/forgot-password"
R = "/api/v1/auth/reset-password"
NEW = "a-brand-new-password"


async def desk(client: AsyncClient, sm: Sm, *, address: bool = True) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    if address:
        body = {"address": f"support@{slug}.test", "name": "Acme Support"}
        ok(await client.post("/api/v1/email-addresses", headers=bearer(token), json=body), 201)
    return {"slug": slug, "admin": bearer(token), "neha": neha, "ws": UUID(token.split(".")[0])}


async def outbox(sm: Sm, ws: UUID) -> list[Any]:
    async with workspace_session(sm, ws) as s:
        result = await s.execute(text(
            "SELECT * FROM hd_email_out WHERE subject = 'Reset your CeDesk password' "
            "ORDER BY created_at"))  # fmt: skip
        return list(result.mappings().all())


def link_token(body: str) -> str:
    found = re.search(r"/reset-password#token=(\S+)", body)
    assert found, body
    return found.group(1)


async def forgot(client: AsyncClient, w: dict[str, Any], email: str = "Neha@Example.com") -> None:
    r = await client.post(F, json={"workspace": w["slug"], "email": email})
    assert r.status_code == 202, r.text


async def test_the_link_sets_a_new_password_and_signs_out_everywhere(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    old_session = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    await forgot(client, w)

    [email] = await outbox(app_sessionmaker, w["ws"])
    assert email["to_addresses"] == ["neha@example.com"]
    assert email["from_address"] == f"support@{w['slug']}.test"  # the workspace's default
    assert email["sensitive"] is True and email["ticket_id"] is None
    assert email["body_text"].startswith("Hi Neha,")
    assert "http://localhost:3000/reset-password#token=" in email["body_text"]
    token = link_token(email["body_text"])

    assert (await client.post(R, json={"token": token, "new_password": "short"})).status_code == 422
    assert (await client.post(R, json={"token": token, "new_password": NEW})).status_code == 204

    assert (await client.get("/api/v1/me", headers=old_session)).status_code == 401
    r = await client.post("/api/v1/auth/sign-in",
                          json={"workspace": w["slug"], "email": "neha@example.com",
                                "password": PASSWORD})  # fmt: skip
    assert r.status_code == 401
    await sign_in(client, w["slug"], "neha@example.com", NEW)
    # Used once only.
    r = await client.post(R, json={"token": token, "new_password": "another-new-password"})
    assert r.status_code == 410


async def test_every_case_answers_the_same_and_sends_nothing(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.patch(f"/api/v1/members/{w['neha']}", headers=w["admin"],
                          json={"is_active": False}))  # fmt: skip
    await forgot(client, w)  # inactive
    await forgot(client, w, "nobody@example.com")
    r = await client.post(F, json={"workspace": "no-such-place", "email": "neha@example.com"})
    assert r.status_code == 202
    assert await outbox(app_sessionmaker, w["ws"]) == []


async def test_at_most_three_links_an_hour(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for _ in range(4):
        await forgot(client, w)
    assert len(await outbox(app_sessionmaker, w["ws"])) == 3


async def test_expired_bad_and_other_workspace_links_are_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await forgot(client, w)
    token = link_token((await outbox(app_sessionmaker, w["ws"]))[0]["body_text"])

    other = await desk(client, app_sessionmaker)
    moved = f"{other['ws']}.{token.split('.', 1)[1]}"  # same secret, another workspace
    for bad in ["nonsense", f"{w['ws']}.wrong", moved]:
        r = await client.post(R, json={"token": bad, "new_password": NEW})
        assert r.status_code == 410, bad

    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE password_reset SET expires_at = now() - interval '1 s'"))
    assert (await client.post(R, json={"token": token, "new_password": NEW})).status_code == 410


async def test_a_reset_unlocks_a_locked_member(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for _ in range(12):  # past the lockout limit
        wrong = {"workspace": w["slug"], "email": "neha@example.com", "password": "wrong-pw!"}
        await client.post("/api/v1/auth/sign-in", json=wrong)
    right = {"workspace": w["slug"], "email": "neha@example.com", "password": PASSWORD}
    locked = await client.post("/api/v1/auth/sign-in", json=right)
    assert locked.status_code == 401  # locked: even the right password is refused
    await forgot(client, w)
    token = link_token((await outbox(app_sessionmaker, w["ws"]))[0]["body_text"])
    assert (await client.post(R, json={"token": token, "new_password": NEW})).status_code == 204
    await sign_in(client, w["slug"], "neha@example.com", NEW)


async def test_the_link_is_wiped_from_the_queue_once_sent(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await forgot(client, w)
    sent: list[OutgoingEmail] = []

    class Catch:
        async def send(self, email: OutgoingEmail) -> None:
            if email.from_address == f"support@{w['slug']}.test":  # not other tests' emails
                sent.append(email)

    await send_due(app_sessionmaker, Catch())
    assert len(sent) == 1 and "#token=" in sent[0].text
    [row] = await outbox(app_sessionmaker, w["ws"])
    assert (row["status"], row["body_text"]) == ("sent", "(removed after sending)")


@pytest.fixture
async def no_reply_client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(sign_in_limit=100_000, mail_from_address="no-reply@cedesk.test",
                              app_url="https://app.cedesk.test/"))  # fmt: skip
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_without_a_support_address_the_server_sender_is_used(
    client: AsyncClient, no_reply_client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker, address=False)
    await forgot(client, w)  # no support address, no server sender: nothing to send from
    assert await outbox(app_sessionmaker, w["ws"]) == []
    await forgot(no_reply_client, w)
    [email] = await outbox(app_sessionmaker, w["ws"])
    assert (email["from_address"], email["from_name"]) == ("no-reply@cedesk.test", "CeDesk")
    assert "https://app.cedesk.test/reset-password#token=" in email["body_text"]
