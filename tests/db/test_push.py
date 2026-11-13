"""Push banners to the mobile app (md P8), with RLS on and a fake push service: devices
(register, list without the token, move only after sign-out, keep 10, remove your own
only), ending a sign-in removes its phones, which notifications get a banner and what it
says (the ticket's number, never its subject), sent once even with two workers, old ones
dropped, push off, a gone token deleted, a refused message keeps the device, an inactive
member skipped, failures retried (network, a refused key, the pass's time), and tenancy;
migration 0051 down and up."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import push as push_repo
from cedesk.helpdesk.service import push
from cedesk.push_client import PushError, Sent
from cedesk.settings import Settings
from tests.conftest import alembic_config
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

D = "/api/v1/me/devices"
T = "/api/v1/tickets"


def token(n: int) -> str:
    """A different end for each, so their hints (the last 6 characters) differ."""
    return "fcm-token-" + "x" * 30 + f"-{n:04d}"


class FakeFcm:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, dict[str, str]]] = []
        self.gone: set[str] = set()
        self.invalid: set[str] = set()  # INVALID_ARGUMENT: refused, the device stays
        self.down: set[str] = set()  # the push service can't be reached for these
        self.refuse = False
        self.calls = 0
        self.on_send: Any = None
        self.opened_count = 0

    @asynccontextmanager
    async def opened(self) -> AsyncIterator[None]:
        self.opened_count += 1
        yield

    async def send(self, device_token: str, title: str, body: str, data: dict[str, str]) -> Sent:
        self.calls += 1
        if self.on_send is not None:
            self.on_send()
        if self.refuse:
            raise PushError("Firebase refused the push service account key.", config=True)
        await asyncio.sleep(0)  # let another worker run in between
        if device_token in self.down:
            raise PushError("fcm.googleapis.com can't be reached (ConnectError).", config=False)
        if device_token in self.gone:
            return Sent(ok=False, gone=True)
        if device_token in self.invalid:
            return Sent(ok=False, gone=False)
        self.sent.append((device_token, title, body, data))
        return Sent(ok=True, gone=False)


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    ids = {n: await add(client, admin, f"{n.lower()}@example.com", "agent", n)
           for n in ("Neha", "Ravi")}  # fmt: skip
    me = ok(await client.get("/api/v1/me", headers=bearer(admin)))
    return {"slug": slug, "h": bearer(admin), "ids": ids, "ws": UUID(me["workspace"]["id"]),
            "neha": bearer(await sign_in(client, slug, "neha@example.com")),
            "ravi": bearer(await sign_in(client, slug, "ravi@example.com"))}  # fmt: skip


async def ticket(client: AsyncClient, w: dict[str, Any], subject: str = "Secret parcel") -> Any:
    body = {"subject": subject, "description": "About it",
            "contact": {"email": "priya@example.org", "name": "Priya"}}  # fmt: skip
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def signed_out(client: AsyncClient, headers: dict[str, str]) -> None:
    assert (await client.post("/api/v1/auth/sign-out", headers=headers)).status_code == 204


async def notify(sm: Sm, w: dict[str, Any], who: str, ticket_id: str, kind: str) -> None:
    async with workspace_session(sm, w["ws"]) as s:
        await message_repo.notify(s, workspace_id=w["ws"], member_ids=[UUID(w["ids"][who])],
                                  ticket_id=UUID(ticket_id), kind=kind, body="x")  # fmt: skip


async def test_devices(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for bad in ({"platform": "windows", "token": token(1)}, {"platform": "ios", "token": "short"},
                {"platform": "ios", "token": "has space " + "x" * 30}):  # fmt: skip
        assert (await client.post(D, headers=w["neha"], json=bad)).status_code == 422
    made = ok(await client.post(D, headers=w["neha"], json={"platform": "ios",
                                                            "token": token(1)}), 201)  # fmt: skip
    assert made["token_hint"] == "…" + token(1)[-6:] and "token" not in made
    listed = ok(await client.get(D, headers=w["neha"]))
    assert listed["push_on"] is False  # this test server has no Firebase settings
    assert [d["id"] for d in listed["devices"]] == [made["id"]]
    assert token(1) not in str(listed)

    # The same phone signed in as Ravi while Neha is still signed in on it: refused.
    phone = {"platform": "ios", "token": token(1)}
    r = await client.post(D, headers=w["ravi"], json=phone)
    assert r.status_code == 409 and token(1) not in r.text
    assert len(ok(await client.get(D, headers=w["neha"]))["devices"]) == 1
    # Neha signs in again on it (a new session): still hers, under the new sign-in.
    neha2 = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert ok(await client.post(D, headers=neha2, json=phone), 201)["id"] == made["id"]
    await signed_out(client, w["neha"])
    assert len(ok(await client.get(D, headers=neha2))["devices"]) == 1  # not that sign-in's
    # Once that sign-in has ended (here: expired, so the row is still there), Ravi takes
    # the phone over.
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE auth_session SET expires_at = now() - interval '1 second' "
                             "WHERE member_id = :m"), {"m": UUID(w["ids"]["Neha"])})  # fmt: skip
    w["neha"] = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    again = ok(await client.post(D, headers=w["ravi"], json=phone), 201)
    assert again["id"] == made["id"]
    assert ok(await client.get(D, headers=w["neha"]))["devices"] == []
    # Neha can't remove Ravi's phone; Ravi can.
    assert (await client.delete(f"{D}/{made['id']}", headers=w["neha"])).status_code == 404
    assert (await client.delete(f"{D}/{made['id']}", headers=w["ravi"])).status_code == 204
    assert ok(await client.get(D, headers=w["ravi"]))["devices"] == []

    # The 10 newest stay.
    for n in range(12):
        phone = {"platform": "android", "token": token(100 + n)}
        ok(await client.post(D, headers=w["neha"], json=phone), 201)
    kept = ok(await client.get(D, headers=w["neha"]))["devices"]
    assert len(kept) == 10 and {d["token_hint"] for d in kept} == {
        "…" + token(100 + n)[-6:] for n in range(2, 12)}  # fmt: skip


async def test_banners(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    fcm = FakeFcm()
    await push.push_due(sm, fcm)  # anything left by other tests is handled first
    fcm.sent.clear()
    for n, who in ((1, "neha"), (2, "neha"), (3, "ravi")):
        ok(await client.post(D, headers=w[who], json={"platform": "android",
                                                      "token": token(n)}), 201)  # fmt: skip
    t = await ticket(client, w)
    # A real mention through the API, and the other kinds.
    note = {"kind": "note", "body": "@Neha look", "mention_ids": [w["ids"]["Neha"]]}
    ok(await client.post(f"{T}/{t['id']}/messages", headers=w["h"], json=note), 201)
    await notify(sm, w, "Ravi", t["id"], "assigned")
    await notify(sm, w, "Neha", t["id"], "customer_reply")
    await notify(sm, w, "Neha", t["id"], "task")  # bell only

    # Two workers at once: each banner goes once.
    results = await asyncio.gather(push.push_due(sm, fcm), push.push_due(sm, fcm))
    assert sum(results) == len(fcm.sent) == 5  # Neha: 2 kinds x 2 phones; Ravi: 1
    label = f"T-{t['number']}"
    by_token: dict[str, set[str]] = {}
    for tok, title, body, data in fcm.sent:
        by_token.setdefault(tok, set()).add(title)
        assert body == label and "Secret parcel" not in title + body  # never the subject
        assert data["ticket_id"] == t["id"] and set(data) == {"ticket_id", "notification_id",
                                                              "kind"}  # fmt: skip
    assert by_token == {token(1): {"You were mentioned", "The customer replied"},
                        token(2): {"You were mentioned", "The customer replied"},
                        token(3): {"New ticket for you"}}  # fmt: skip
    assert await push.push_due(sm, fcm) == 0  # nothing twice

    # Older than 10 minutes: dropped (it is in the bell).
    fcm.sent.clear()
    await notify(sm, w, "Neha", t["id"], "mention")
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE hd_notification SET created_at = now() - interval '11 "
                             "minutes' WHERE push_handled_at IS NULL"))  # fmt: skip
    assert await push.push_due(sm, fcm) == 0 and fcm.sent == []

    # Push off: marked handled, never sent later.
    await notify(sm, w, "Neha", t["id"], "mention")
    assert await push.push_due(sm, None) == 0
    assert await push.push_due(sm, fcm) == 0 and fcm.sent == []

    # A gone token is deleted; an inactive member gets nothing.
    fcm.gone = {token(1)}
    await notify(sm, w, "Neha", t["id"], "mention")
    assert await push.push_due(sm, fcm) == 1
    hints = {d["token_hint"] for d in ok(await client.get(D, headers=w["neha"]))["devices"]}
    assert hints == {"…" + token(2)[-6:]}
    ok(await client.patch(f"/api/v1/members/{w['ids']['Ravi']}", headers=w["h"],
                          json={"is_active": False}))  # fmt: skip
    fcm.sent.clear()
    await notify(sm, w, "Ravi", t["id"], "assigned")
    assert await push.push_due(sm, fcm) == 0 and fcm.sent == []

    # A refused key stops the pass after one try; the banner waits for the next pass.
    fcm.refuse, fcm.calls = True, 0
    await notify(sm, w, "Neha", t["id"], "mention")
    assert await push.push_due(sm, fcm) == 0 and fcm.calls == 1
    fcm.refuse = False
    assert await push.push_due(sm, fcm) == 1 and fcm.sent[-1][0] == token(2)


async def test_a_second_worker_skips_what_the_first_holds(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Two workers at the same moment: while the first holds its claim (not committed),
    the second gets nothing, at once, instead of waiting for it or taking it too."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    await push.push_due(sm, FakeFcm())
    t = await ticket(client, w)
    await notify(sm, w, "Neha", t["id"], "mention")
    async with workspace_session(sm, w["ws"]) as first:
        held = await push_repo.claim(first, 100)
        async with workspace_session(sm, w["ws"]) as second:
            await second.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert await push_repo.claim(second, 100) == []
        assert [n["kind"] for n in held] == ["mention"]


async def test_another_workspace(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    a, b = await desk(client, sm), await desk(client, sm)
    mine = ok(await client.post(D, headers=a["neha"], json={"platform": "ios",
                                                            "token": token(7)}), 201)  # fmt: skip
    assert (await client.delete(f"{D}/{mine['id']}", headers=b["neha"])).status_code == 404
    assert ok(await client.get(D, headers=b["neha"]))["devices"] == []
    async with workspace_session(sm, b["ws"]) as s:
        assert (await s.execute(text("SELECT count(*) FROM hd_push_device"))).scalar_one() == 0
    # The same token in another workspace is another device; neither sees the other.
    ok(await client.post(D, headers=b["neha"], json={"platform": "ios", "token": token(7)}), 201)
    assert len(ok(await client.get(D, headers=a["neha"]))["devices"]) == 1

    fcm = FakeFcm()
    await push.push_due(sm, fcm)
    fcm.sent.clear()
    t = await ticket(client, b)
    await notify(sm, b, "Neha", t["id"], "mention")
    assert await push.push_due(sm, fcm) == 1  # B's Neha's phone, once


async def pending(sm: Sm, w: dict[str, Any]) -> list[dict[str, Any]]:
    async with workspace_session(sm, w["ws"]) as s:
        rows = await s.execute(text(
            "SELECT kind, push_attempts, push_claimed_until IS NOT NULL AS held "
            "FROM hd_notification WHERE push_handled_at IS NULL ORDER BY created_at"))  # fmt: skip
        return [dict(r) for r in rows.mappings().all()]


async def retry_now(sm: Sm, w: dict[str, Any]) -> None:
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE hd_notification SET push_claimed_until = NULL "
                             "WHERE push_handled_at IS NULL"))  # fmt: skip


async def settle(sm: Sm, w: dict[str, Any]) -> None:
    """Leaves nothing pending for the next test (the database is shared)."""
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE hd_notification SET push_handled_at = now() "
                             "WHERE push_handled_at IS NULL"))  # fmt: skip


async def test_ending_a_sign_in_stops_its_banners(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    fcm = FakeFcm()
    await push.push_due(sm, fcm)
    t = await ticket(client, w)

    async def phones(who: str) -> int:
        async with workspace_session(sm, w["ws"]) as s:
            return int((await s.execute(
                text("SELECT count(*) FROM hd_push_device WHERE member_id = :m"),
                {"m": UUID(w["ids"][who])})).scalar_one())  # fmt: skip

    async def banners(who: str) -> int:
        fcm.sent.clear()
        await notify(sm, w, who, t["id"], "mention")
        return await push.push_due(sm, fcm)

    # Sign-out removes the phone registered under that sign-in.
    ok(await client.post(D, headers=w["neha"], json={"platform": "ios", "token": token(1)}), 201)
    assert await banners("Neha") == 1
    await signed_out(client, w["neha"])
    assert await phones("Neha") == 0 and await banners("Neha") == 0

    # A password change ends the other sign-ins: their phones go, this one's stays.
    other = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    here = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    ok(await client.post(D, headers=other, json={"platform": "ios", "token": token(2)}), 201)
    ok(await client.post(D, headers=here, json={"platform": "android", "token": token(3)}), 201)
    new_password = "another-horse-battery"
    r = await client.post(
        "/api/v1/me/password",
        headers=here,
        json={"current_password": PASSWORD, "new_password": new_password},
    )
    assert r.status_code == 204, r.text
    hints = {d["token_hint"] for d in ok(await client.get(D, headers=here))["devices"]}
    assert hints == {"…" + token(3)[-6:]}
    assert await banners("Neha") == 1 and fcm.sent[0][0] == token(3)

    # An Admin's reset ends every sign-in: no phone left.
    r = await client.post(
        f"/api/v1/members/{w['ids']['Neha']}/password",
        headers=w["h"],
        json={"new_password": "third-horse-battery"},
    )
    assert r.status_code == 204, r.text
    assert await phones("Neha") == 0 and await banners("Neha") == 0

    # Deactivation too.
    ok(await client.post(D, headers=w["ravi"], json={"platform": "ios", "token": token(4)}), 201)
    ok(await client.patch(f"/api/v1/members/{w['ids']['Ravi']}", headers=w["h"],
                          json={"is_active": False}))  # fmt: skip
    assert await phones("Ravi") == 0

    # A sign-in that ended some other way (expired) is skipped when sending.
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com", "third-horse-battery"))
    ok(await client.post(D, headers=neha, json={"platform": "ios", "token": token(5)}), 201)
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE auth_session SET expires_at = now() - interval '1 second' "
                             "WHERE member_id = :m"), {"m": UUID(w["ids"]["Neha"])})  # fmt: skip
    assert await banners("Neha") == 0 and fcm.sent == []


async def test_failures_are_retried(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    fcm = FakeFcm()
    await push.push_due(sm, fcm)
    fcm.sent.clear()
    ok(await client.post(D, headers=w["neha"], json={"platform": "ios", "token": token(1)}), 201)
    ok(await client.post(D, headers=w["ravi"], json={"platform": "ios", "token": token(2)}), 201)
    t = await ticket(client, w)

    # The push service can't be reached for Neha's phone mid-batch: Ravi's banner goes,
    # Neha's waits (one failed try) and goes once the service is back.
    await notify(sm, w, "Neha", t["id"], "mention")
    await notify(sm, w, "Ravi", t["id"], "mention")
    fcm.down = {token(1)}
    assert await push.push_due(sm, fcm) == 1 and [x[0] for x in fcm.sent] == [token(2)]
    assert fcm.opened_count >= 1  # one connection pool for the pass
    assert await pending(sm, w) == [{"kind": "mention", "push_attempts": 1, "held": True}]
    assert await push.push_due(sm, fcm) == 0  # not before its retry time
    fcm.down = set()
    await retry_now(sm, w)
    assert await push.push_due(sm, fcm) == 1 and fcm.sent[-1][0] == token(1)
    assert await pending(sm, w) == []

    # Given up after 5 tries (it is still in the bell).
    fcm.down = {token(1)}
    await notify(sm, w, "Neha", t["id"], "mention")
    for _ in range(push.rules.MAX_ATTEMPTS):
        assert await push.push_due(sm, fcm) == 0
        await retry_now(sm, w)
    assert await pending(sm, w) == []
    fcm.down = set()

    # INVALID_ARGUMENT: refused for good, but the device stays.
    fcm.invalid = {token(1)}
    await notify(sm, w, "Neha", t["id"], "mention")
    assert await push.push_due(sm, fcm) == 0 and await pending(sm, w) == []
    assert len(ok(await client.get(D, headers=w["neha"]))["devices"]) == 1
    fcm.invalid = set()

    # The pass's time runs out after the first send: the rest wait for the next pass.
    clock = [0.0]

    def later() -> None:
        clock[0] += 100

    await notify(sm, w, "Neha", t["id"], "mention")
    await notify(sm, w, "Ravi", t["id"], "mention")
    fcm.sent.clear()
    fcm.on_send = later
    assert await push.push_due(sm, fcm, clock=lambda: clock[0]) == 1
    fcm.on_send = None
    assert await pending(sm, w) == [{"kind": "mention", "push_attempts": 0, "held": False}]
    assert await push.push_due(sm, fcm) == 1 and len(fcm.sent) == 2
    await settle(sm, w)


async def test_migration_0051_down_and_up(migrated_db: None, owner_engine: Any) -> None:
    cfg = alembic_config()
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0050")
        async with owner_engine.connect() as conn:
            gone = await conn.scalar(text("SELECT to_regclass('hd_push_device')"))
            assert gone is None
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        columns = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'hd_push_device'"))).scalars())  # fmt: skip
    assert "session_id" in columns


def test_statement_values_stay_out_of_logs() -> None:
    from cedesk.db import make_engine

    engine = make_engine("postgresql+asyncpg://u:p@localhost:1/x")
    assert engine.sync_engine.hide_parameters


def test_the_settings_go_together() -> None:
    def problems(**kw: Any) -> list[str]:
        return [p for p in Settings(_env_file=None, **kw).mail_problems() if "PUSH" in p]

    assert problems() == []
    assert problems(push_fcm_project_id="cedesk-mobile") != []
    assert problems(push_fcm_service_account="{}") != []
    on = Settings(_env_file=None, push_fcm_project_id="p", push_fcm_service_account="{}")
    assert on.push_on() and not Settings(_env_file=None).push_on()
