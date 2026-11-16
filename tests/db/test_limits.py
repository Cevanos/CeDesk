"""Cloud plan limits, the usage meter and retention (md O2, O4, O5), with RLS on: no plan
means no limits; a plan's agents, tickets a month and storage give 402 (light agents and
customers' own requests are never refused); self-hosted has no limits; retention deletes
resolved tickets and their files after N days, capped by the plan, and audits it."""

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import limits as rules
from cedesk.helpdesk.domain.limits import Plan
from cedesk.helpdesk.service import limits
from cedesk.helpdesk.service.tickets import NewTicket, create_from_channel
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
TINY = Plan("tiny", agents=2, tickets_month=2, storage_bytes=20, history_days=60)


def stored_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


@pytest.fixture
def files(tmp_path: Path) -> Path:
    return tmp_path / "files"


@pytest.fixture
async def client(app_sessionmaker: Sm, files: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(storage_local_path=str(files)))
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture
def tiny_plans() -> Iterator[None]:
    limits.configure(cloud=True, plans={"tiny": TINY, **rules.DEFAULT_PLANS})
    yield
    limits.configure(cloud=True, plans=dict(rules.DEFAULT_PLANS))


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"slug": slug, "token": token, "h": bearer(token), "ws": UUID(me["workspace"]["id"])}


async def ticket(client: AsyncClient, w: dict[str, Any], subject: str = "Hi") -> Any:
    body = {"subject": subject, "contact": {"email": "priya@example.org", "name": "Priya"}}
    return await client.post(T, headers=w["h"], json=body)


async def member(client: AsyncClient, w: dict[str, Any], email: str, role: str) -> Any:
    body = {"email": email, "name": email, "role": role, "password": PASSWORD}
    return await client.post("/api/v1/members", headers=w["h"], json=body)


async def test_no_plan_means_no_limits(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for n in range(4):
        assert (await member(client, w, f"a{n}@example.com", "agent")).status_code == 201
    ok(await ticket(client, w), 201)
    usage = ok(await client.get("/api/v1/usage", headers=w["h"]))
    assert usage["plan"] is None and set(usage["limits"].values()) == {None}
    assert (usage["agents"], usage["tickets_this_month"], usage["storage_bytes"]) == (5, 1, 0)
    assert usage["retention_days"] is None


async def test_a_plans_limits(client: AsyncClient, app_sessionmaker: Sm, tiny_plans: None) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await limits.set_plan(sm, w["slug"], "tiny")
    usage = ok(await client.get("/api/v1/usage", headers=w["h"]))
    assert usage["plan"] == "tiny" and usage["limits"] == {
        "agents": 2, "tickets_month": 2, "storage_bytes": 20}  # fmt: skip

    assert (await member(client, w, "neha@example.com", "agent")).status_code == 201  # 2 of 2
    over = await member(client, w, "ravi@example.com", "supervisor")
    assert (
        over.status_code == 402 and "Tiny plan allows 2 agents" in over.json()["error"]["message"]
    )
    light = await member(client, w, "meena@example.com", "light_agent")
    assert light.status_code == 201  # a free seat
    promote = await client.patch(f"/api/v1/members/{light.json()['id']}", headers=w["h"],
                                 json={"role": "agent"})  # fmt: skip
    assert promote.status_code == 402  # becoming an agent takes a seat

    first = ok(await ticket(client, w, "One"), 201)
    ok(await ticket(client, w, "Two"), 201)
    assert (await ticket(client, w, "Three")).status_code == 402
    # A customer's own request is never refused.
    view = await create_from_channel(sm, w["ws"], NewTicket(
        subject="From email", contact_email="priya@example.org", contact_name="Priya",
        description="Hello"))  # fmt: skip
    assert view.ticket["id"] is not None

    url = f"{T}/{first['id']}/attachments"
    h = {**w["h"], "Content-Type": "text/plain"}
    ok(await client.post(url, params={"name": "a.txt"}, content=b"x" * 15, headers=h), 201)
    big = await client.post(url, params={"name": "b.txt"}, content=b"x" * 10, headers=h)
    assert big.status_code == 402  # 15 + 10 > 20 bytes

    limits.configure(cloud=False, plans={"tiny": TINY})  # self-hosted: no limits at all
    ok(await ticket(client, w, "Self-hosted"), 201)
    assert ok(await client.get("/api/v1/usage", headers=w["h"]))["plan"] is None

    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "plan"}))
    assert page["entries"][0]["detail"]["changes"]["plan"] == [None, "tiny"]


async def test_set_plan_checks_its_input(app_sessionmaker: Sm, tiny_plans: None) -> None:
    slug = await new_workspace(app_sessionmaker)
    with pytest.raises(Exception, match="No plan"):
        await limits.set_plan(app_sessionmaker, slug, "platinum")
    with pytest.raises(Exception, match="No workspace"):
        await limits.set_plan(app_sessionmaker, "nobody-here", "tiny")


async def test_retention_deletes_old_resolved_tickets_and_their_files(
    client: AsyncClient, app_sessionmaker: Sm, files: Path, tiny_plans: None
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    for days in (10, 5000):
        r = await client.put("/api/v1/retention", headers=w["h"], json={"closed_ticket_days": days})
        assert r.status_code == 422, days
    neha = await add(client, w["token"], "neha@example.com", "agent", "Neha")
    agent = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert (
        await client.put("/api/v1/retention", headers=agent, json={"closed_ticket_days": 90})
    ).status_code == 403
    assert neha

    kept = ok(
        await client.put("/api/v1/retention", headers=w["h"], json={"closed_ticket_days": 90})
    )
    assert (kept["closed_ticket_days"], kept["effective_days"]) == (90, 90)

    made = [ok(await ticket(client, w, s), 201) for s in ("Old", "Recent", "Open")]
    old, recent, open_ = made
    h = {**w["h"], "Content-Type": "text/plain"}
    ok(await client.post(f"{T}/{old['id']}/attachments", params={"name": "o.txt"},
                         content=b"old file", headers=h), 201)  # fmt: skip
    assert stored_files(files) == 1
    now = datetime.now(UTC)
    async with workspace_session(sm, w["ws"]) as s:
        for t, ago in ((old, 100), (recent, 20)):
            await s.execute(text("UPDATE hd_ticket SET status = 'resolved', resolved_at = :at "
                                 "WHERE id = :id"), {"at": now - timedelta(days=ago),
                                                      "id": UUID(t["id"])})  # fmt: skip
        await s.execute(text("UPDATE hd_ticket SET created_at = :at WHERE id = :id"),
                        {"at": now - timedelta(days=400), "id": UUID(open_["id"])})  # fmt: skip

    store = client._transport.app.state.store  # type: ignore[attr-defined]
    assert await limits.run_retention(sm, store) == 1
    assert (await client.get(f"{T}/{old['id']}", headers=w["h"])).status_code == 404
    ok(await client.get(f"{T}/{recent['id']}", headers=w["h"]))
    ok(await client.get(f"{T}/{open_['id']}", headers=w["h"]))  # open: never deleted
    assert stored_files(files) == 0  # its file went too
    assert await limits.run_retention(sm, store) == 0  # nothing more

    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "retention"}))
    actions = [(e["action"], e["actor_name"]) for e in page["entries"]]
    assert actions[0][0] == "deleted" and actions[-1][0] == "changed"

    # A plan's history caps the Admin's setting: 60 days, so "Recent" (20) stays.
    await limits.set_plan(sm, w["slug"], "tiny")
    capped = ok(await client.get("/api/v1/retention", headers=w["h"]))
    assert (capped["plan_history_days"], capped["effective_days"]) == (60, 60)
    assert await limits.run_retention(sm, store) == 0
    ok(await client.put("/api/v1/retention", headers=w["h"], json={"closed_ticket_days": None}))
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET resolved_at = :at WHERE id = :id"),
                        {"at": now - timedelta(days=61), "id": UUID(recent["id"])})  # fmt: skip
    assert await limits.run_retention(sm, store) == 1  # the plan alone deletes after 60


async def test_retention_counts_imported_tickets_from_their_import(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    """An imported ticket keeps its old dates: resolved 2 years ago in Zendesk, imported
    5 days ago, it is kept (counted from the import). Deleting a ticket also deletes its
    events and webhook deliveries, which name it."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    ok(await client.put("/api/v1/retention", headers=w["h"], json={"closed_ticket_days": 90}))
    imported, own = [ok(await ticket(client, w, s), 201) for s in ("From Zendesk", "Ours")]
    now = datetime.now(UTC)
    async with workspace_session(sm, w["ws"]) as s:
        conn = (
            await s.execute(
                text(
                    "INSERT INTO hd_import_connection (workspace_id, source, label) "
                    "VALUES (:ws, 'zendesk', 'acme') RETURNING id"
                ),
                {"ws": w["ws"]},
            )
        ).scalar_one()
        await s.execute(text(
            "INSERT INTO hd_import_run (workspace_id, connection_id, source, state, started_at, "
            "finished_at) VALUES (:ws, :c, 'zendesk', 'done', :at, :at)"),
            {"ws": w["ws"], "c": conn, "at": now - timedelta(days=5)})  # fmt: skip
        for t, source in ((imported, "zendesk"), (own, "web")):
            await s.execute(text(
                "UPDATE hd_ticket SET status = 'resolved', resolved_at = :at, source = :src, "
                "source_id = :sid WHERE id = :id"),
                {"at": now - timedelta(days=730), "src": source,
                 "sid": "z-1" if source == "zendesk" else None, "id": UUID(t["id"])})  # fmt: skip
        await s.execute(text(
            "INSERT INTO hd_webhook (workspace_id, url, events, secret_sealed, secret_hint) "
            "VALUES (:ws, 'https://hooks.example.com/x', ARRAY['ticket.created'], '\\x00', "
            "'…abcd')"), {"ws": w["ws"]})  # fmt: skip
        await s.execute(text(
            "INSERT INTO hd_webhook_delivery (workspace_id, webhook_id, event, body, state) "
            "SELECT :ws, id, 'ticket.created', jsonb_build_object('ticket', "
            "jsonb_build_object('id', CAST(:t AS text), 'subject', 'Ours')), 'delivered' "
            "FROM hd_webhook"), {"ws": w["ws"], "t": own["id"]})  # fmt: skip

    store = client._transport.app.state.store  # type: ignore[attr-defined]
    assert await limits.run_retention(sm, store) == 1
    ok(await client.get(f"{T}/{imported['id']}", headers=w["h"]))  # imported 5 days ago: kept
    assert (await client.get(f"{T}/{own['id']}", headers=w["h"])).status_code == 404
    async with workspace_session(sm, w["ws"]) as s:
        events = (await s.execute(text("SELECT count(*) FROM hd_outbox WHERE ticket_id = :t"),
                                  {"t": UUID(own["id"])})).scalar_one()  # fmt: skip
        hooks = (await s.execute(text("SELECT count(*) FROM hd_webhook_delivery"))).scalar_one()
        kept = (await s.execute(text("SELECT count(*) FROM hd_outbox WHERE ticket_id = :t"),
                                {"t": UUID(imported["id"])})).scalar_one()  # fmt: skip
    assert (events, hooks) == (0, 0) and kept >= 1


R = "/api/v1/retention"


class BrokenStore:
    """Writing fails: retention must then delete nothing."""

    def __init__(self, store: Any) -> None:
        self.store = store

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        raise OSError("disk full")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)


async def test_retention_exports_before_it_deletes(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    """O2: with export before delete on, the tickets retention deletes (live and archived)
    are first written to a JSON file an Admin downloads for 30 days; erasing a customer
    deletes the files that hold them; without storage, or when writing fails, nothing is
    deleted."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    on = ok(await client.put(R, headers=w["h"], json={"closed_ticket_days": 90,
                                                      "export_before_delete": True}))  # fmt: skip
    assert on["export_before_delete"] is True
    assert ok(await client.put(R, headers=w["h"], json={"closed_ticket_days": 90}))[
        "export_before_delete"] is True  # left out: kept  # fmt: skip

    old, archived = [ok(await ticket(client, w, s), 201) for s in ("Old parcel", "Older")]
    plain = {**w["h"], "Content-Type": "text/plain"}
    url = f"{T}/{old['id']}"
    label = ok(await client.post(f"{url}/attachments", params={"name": "label.txt"},
                                 content=b"label", headers=plain), 201)  # fmt: skip
    for kind, body, sent in (("reply", "Found it", [label["id"]]),
                             ("note", "Courier was late", [])):  # fmt: skip
        said = {"kind": kind, "body": body, "attachment_ids": sent}
        ok(await client.post(f"{url}/messages", headers=w["h"], json=said), 201)
    now = datetime.now(UTC)
    async with workspace_session(sm, w["ws"]) as s:
        for t, ago in ((old, 100), (archived, 200)):
            await s.execute(text("UPDATE hd_ticket SET status = 'resolved', resolved_at = :at "
                                 "WHERE id = :id"), {"at": now - timedelta(days=ago),
                                                      "id": UUID(t["id"])})  # fmt: skip
    from cedesk.helpdesk.service import archive

    assert await archive.archive_workspace(sm, w["ws"], now) == 1  # "Older" (200 days)
    store = client._transport.app.state.store  # type: ignore[attr-defined]

    # No storage, or writing fails: nothing is deleted (never without the export asked).
    assert await limits.run_retention(sm, None) == 0
    with pytest.raises(OSError):
        await limits.run_retention(sm, BrokenStore(store))
    ok(await client.get(f"{T}/{old['id']}", headers=w["h"]))
    assert stored_files(files) == 1

    assert await limits.run_retention(sm, store) == 2
    assert (await client.get(f"{T}/{old['id']}", headers=w["h"])).status_code == 404
    exports = ok(await client.get(f"{R}/exports", headers=w["h"]))
    assert sorted(e["tickets"] for e in exports) == [1, 1]
    assert all(e["expires_at"] > e["created_at"] for e in exports)
    assert stored_files(files) == 2  # the two exports; the ticket's file went
    got: list[dict[str, Any]] = []
    for e in exports:
        r = await client.get(f"{R}/exports/{e['id']}/file", headers=w["h"])
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        got += json.loads(r.content)["tickets"]
    by_subject = {t["subject"]: t for t in got}
    live = by_subject["Old parcel"]
    assert live["customer"]["email"] == "priya@example.org"
    assert {m["text"] for m in live["messages"]} >= {"Found it", "Courier was late"}
    assert [f["name"] for m in live["messages"] for f in m["files"]] == ["label.txt"]
    assert by_subject["Older"]["archived"] is True
    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "retention"}))
    assert {"deleted", "downloaded"} <= {e["action"] for e in page["entries"]}
    deleted = next(e for e in page["entries"] if e["action"] == "deleted")
    assert deleted["detail"]["exports"] == 2

    # Admins only (they hold customer data).
    await add(client, w["token"], "neha@example.com", "agent", "Neha")
    agent = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert (await client.get(f"{R}/exports", headers=agent)).status_code == 403
    first = exports[0]["id"]
    assert (await client.get(f"{R}/exports/{first}/file", headers=agent)).status_code == 403

    # Erasing the customer deletes every export that holds her tickets.
    erase = {"contact_id": old["contact"]["id"], "confirm_email": "priya@example.org"}
    done = ok(await client.post("/api/v1/privacy/erase", headers=w["h"], json=erase), 201)
    assert done["counts"]["retention_exports"] == 2
    assert ok(await client.get(f"{R}/exports", headers=w["h"])) == []
    assert stored_files(files) == 0


async def test_retention_exports_expire(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    ok(await client.put(R, headers=w["h"], json={"closed_ticket_days": 30,
                                                 "export_before_delete": True}))  # fmt: skip
    made = ok(await ticket(client, w, "Old"), 201)
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET status = 'resolved', resolved_at = :at "
                             "WHERE id = :id"), {"at": datetime.now(UTC) - timedelta(days=40),
                                                  "id": UUID(made["id"])})  # fmt: skip
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    assert await limits.run_retention(sm, store) == 1
    assert await limits.remove_expired_retention_exports(sm, store) == 0  # 30 days yet
    async with workspace_session(sm, w["ws"]) as s:
        expired = "UPDATE hd_retention_export SET expires_at = now() - interval '1 minute'"
        await s.execute(text(expired))
    assert await limits.remove_expired_retention_exports(sm, store) == 1
    assert ok(await client.get(f"{R}/exports", headers=w["h"])) == []
    assert stored_files(files) == 0
    # Turned off: deletes without a file.
    off = ok(await client.put(R, headers=w["h"], json={"closed_ticket_days": 30,
                                                       "export_before_delete": False}))  # fmt: skip
    assert off["export_before_delete"] is False
