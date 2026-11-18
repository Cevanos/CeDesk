"""Customer portal sign-in (md P1, S7) with RLS on: a 6-digit code by email, the same
answer for unknown emails and workspaces, codes stored only as an HMAC, 5 wrong tries
burn a code, a new code replaces the old, the domain allowlist, and portal tokens that
never work on the agent API (and the other way round)."""

import re
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

P = "/api/v1/portal"


async def desk(client: AsyncClient, sm: Sm, *, address: bool = True) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    if address:
        body = {"address": f"support@{slug}.test", "name": "Acme Support"}
        ok(await client.post("/api/v1/email-addresses", headers=bearer(token), json=body), 201)
    return {"slug": slug, "admin": bearer(token), "ws": UUID(token.split(".")[0])}


async def codes_sent(sm: Sm, ws: UUID, to: str) -> list[dict[str, Any]]:
    async with workspace_session(sm, ws) as s:
        result = await s.execute(text(
            "SELECT * FROM hd_email_out WHERE subject LIKE 'Your sign-in code%' "
            "AND to_addresses = ARRAY[:to] ORDER BY created_at"), {"to": to})  # fmt: skip
        return [dict(r) for r in result.mappings().all()]


def code_in(email: dict[str, Any]) -> str:
    found = re.search(r"\b(\d{6})\b", email["body_text"])
    assert found, email["body_text"]
    return found.group(1)


async def ask(client: AsyncClient, slug: str, email: str, status: int = 202) -> None:
    r = await client.post(f"{P}/{slug}/code", json={"email": email})
    assert r.status_code == status, r.text


async def enter(client: AsyncClient, slug: str, email: str, code: str) -> Any:
    return await client.post(f"{P}/{slug}/session", json={"email": email, "code": code})


async def signed_in(client: AsyncClient, sm: Sm, w: dict[str, Any], email: str) -> str:
    await ask(client, w["slug"], email)
    code = code_in((await codes_sent(sm, w["ws"], email.lower()))[-1])
    r = await enter(client, w["slug"], email, code)
    assert r.status_code == 200, r.text
    token: str = r.json()["token"]
    return token


async def test_an_emailed_code_signs_a_new_customer_in(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ask(client, w["slug"], " Priya.Shah@Example.org ")

    [email] = await codes_sent(app_sessionmaker, w["ws"], "priya.shah@example.org")
    assert email["from_address"] == f"support@{w['slug']}.test"
    assert email["sensitive"] is True and email["ticket_id"] is None  # wiped once sent
    assert email["subject"] == "Your sign-in code for Test support"
    code = code_in(email)

    r = await enter(client, w["slug"], "priya.shah@example.org", f"{code[:3]} {code[3:]}")
    assert r.status_code == 200, r.text
    body = r.json()
    # A name, not "priya.shah": a reply's {{customer.first_name}} says "Hi Priya".
    assert body["customer"] == {
        "email": "priya.shah@example.org", "name": "Priya Shah", "workspace_name": "Test",
    }  # fmt: skip
    me = ok(await client.get(f"{P}/me", headers=bearer(body["token"])))
    assert me["email"] == "priya.shah@example.org"

    # The code works once.
    r = await enter(client, w["slug"], "priya.shah@example.org", code)
    assert r.status_code == 401
    assert (
        r.json()["error"]["message"] == "That code didn't work or has expired. Ask for a new one."
    )


async def test_a_known_customer_keeps_their_name(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    body = {"subject": "Hi", "contact": {"email": "ravi@example.org", "name": "Ravi Kumar"}}
    ok(await client.post("/api/v1/tickets", headers=w["admin"], json=body), 201)
    token = await signed_in(client, app_sessionmaker, w, "Ravi@Example.org")
    assert ok(await client.get(f"{P}/me", headers=bearer(token)))["name"] == "Ravi Kumar"


async def test_the_answer_is_the_same_whatever_happens(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ask(client, "no-such-workspace", "a@example.org")  # 202, nothing sent
    await ask(client, w["slug"], "not-an-email", 422)
    r = await enter(client, "no-such-workspace", "a@example.org", "123456")
    assert r.status_code == 401
    r = await enter(client, w["slug"], "a@example.org", "12345")  # not 6 digits
    assert r.status_code == 401
    r = await enter(client, w["slug"], "nobody@example.org", "123456")  # no code asked
    assert r.status_code == 401


async def test_codes_are_stored_only_as_a_keyed_hash(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ask(client, w["slug"], "a@example.org")
    code = code_in((await codes_sent(app_sessionmaker, w["ws"], "a@example.org"))[0])
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        row = (await s.execute(text("SELECT * FROM hd_portal_code"))).mappings().one()
    assert code.encode() not in bytes(row["code_hash"]) and len(row["code_hash"]) == 32
    assert code not in str(dict(row))


async def test_five_wrong_tries_burn_the_code(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await ask(client, w["slug"], "a@example.org")
    code = code_in((await codes_sent(app_sessionmaker, w["ws"], "a@example.org"))[0])
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(4):
        assert (await enter(client, w["slug"], "a@example.org", wrong)).status_code == 401
    ok(await enter(client, w["slug"], "a@example.org", code))  # 4 wrong: still works

    await ask(client, w["slug"], "a@example.org")
    code = code_in((await codes_sent(app_sessionmaker, w["ws"], "a@example.org"))[-1])
    for _ in range(5):
        assert (await enter(client, w["slug"], "a@example.org", wrong)).status_code == 401
    assert (await enter(client, w["slug"], "a@example.org", code)).status_code == 401


async def test_a_new_code_replaces_the_old_and_codes_expire(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ask(client, w["slug"], "a@example.org")
    await ask(client, w["slug"], "a@example.org")
    first, second = (
        code_in(e) for e in await codes_sent(app_sessionmaker, w["ws"], "a@example.org")
    )
    if first != second:
        assert (await enter(client, w["slug"], "a@example.org", first)).status_code == 401
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_portal_code SET expires_at = now() - interval '1 second'"))
    assert (await enter(client, w["slug"], "a@example.org", second)).status_code == 401


async def test_at_most_five_codes_an_hour(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for _ in range(7):
        await ask(client, w["slug"], "a@example.org")  # always 202
    assert len(await codes_sent(app_sessionmaker, w["ws"], "a@example.org")) == 5


async def test_no_sender_means_no_code(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker, address=False)
    await ask(client, w["slug"], "a@example.org")
    assert await codes_sent(app_sessionmaker, w["ws"], "a@example.org") == []


async def test_the_portal_can_be_limited_to_some_domains(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await add(client, w["admin"]["Authorization"][7:], "neha@example.com", "agent")
    neha = await sign_in(client, w["slug"], "neha@example.com")
    url = "/api/v1/portal-settings"
    assert ok(await client.get(url, headers=w["admin"]))["allowed_domains"] == []
    r = await client.put(url, headers=bearer(neha), json={"allowed_domains": ["acme.com"]})
    assert r.status_code == 403
    r = await client.put(url, headers=w["admin"], json={"allowed_domains": ["not a domain"]})
    assert r.status_code == 422
    domains = {"allowed_domains": ["@Acme.com", "acme.com", "acme.in"]}
    saved = ok(await client.put(url, headers=w["admin"], json=domains))
    assert saved["allowed_domains"] == ["acme.com", "acme.in"]
    assert saved["updated_by_name"] == "Admin"

    r = await client.post(f"{P}/{w['slug']}/code", json={"email": "a@gmail.com"})
    assert r.status_code == 403
    assert r.json()["error"]["message"] == "Only acme.com, acme.in emails can open tickets here."
    await signed_in(client, app_sessionmaker, w, "a@acme.com")
    await signed_in(client, app_sessionmaker, w, "b@mail.acme.in")  # a subdomain

    # Asked while allowed, then the Admin narrowed the list: the code no longer signs in.
    await ask(client, w["slug"], "c@acme.in")
    code = code_in((await codes_sent(app_sessionmaker, w["ws"], "c@acme.in"))[-1])
    ok(await client.put(url, headers=w["admin"], json={"allowed_domains": ["acme.com"]}))
    assert (await enter(client, w["slug"], "c@acme.in", code)).status_code == 403
    ok(await client.put(url, headers=w["admin"], json={"allowed_domains": []}))
    await signed_in(client, app_sessionmaker, w, "d@gmail.com")  # empty: anyone


async def test_portal_and_agent_tokens_never_cross(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    token = await signed_in(client, app_sessionmaker, w, "a@example.org")

    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 401
    assert (await client.get("/api/v1/tickets", headers=bearer(token))).status_code == 401
    assert (await client.get(f"{P}/me", headers=w["admin"])).status_code == 401
    # Pointing the token at another workspace finds nothing there (RLS + hash).
    moved = f"{other['ws']}.{token.split('.', 1)[1]}"
    assert (await client.get(f"{P}/me", headers=bearer(moved))).status_code == 401
    assert (await client.get(f"{P}/me")).status_code == 401

    r = await client.post(f"{P}/sign-out", headers=bearer(token))
    assert r.status_code == 204
    assert (await client.get(f"{P}/me", headers=bearer(token))).status_code == 401
