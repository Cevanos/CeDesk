"""The archive tier (md O3), with RLS on: old resolved tickets move out of the ticket list
with their messages, files' names and rating; they are found by number and customer and
open read only; the archive age setting; imported tickets count from their import;
several workers never move a ticket twice; retention deletes from the archive with the
files; privacy export and erase cover it; another workspace or team can't see it."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import archive as rules
from cedesk.helpdesk.service import archive, limits
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
A = "/api/v1/archive"
PRIYA = "priya@example.org"


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


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"slug": slug, "h": bearer(token), "ws": UUID(me["workspace"]["id"]), "neha": neha,
            "agent": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def ticket(client: AsyncClient, w: dict[str, Any], subject: str, email: str = PRIYA) -> Any:
    body = {"subject": subject, "description": f"About {subject}",
            "contact": {"email": email, "name": "Priya"}}  # fmt: skip
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def sql(sm: Sm, workspace: UUID, query: str, **params: Any) -> list[Any]:
    async with workspace_session(sm, workspace) as s:
        result = await s.execute(text(query), params)
        return list(result.mappings().all()) if result.returns_rows else []


async def resolved_ago(sm: Sm, ws: UUID, ticket_id: str, days: int) -> None:
    await sql(sm, ws, "UPDATE hd_ticket SET status = 'resolved', resolved_at = :at WHERE id = :id",
              at=datetime.now(UTC) - timedelta(days=days), id=UUID(ticket_id))  # fmt: skip


async def an_old_ticket(client: AsyncClient, sm: Sm, w: dict[str, Any]) -> Any:
    """Resolved 200 days ago, with a reply, a note, a file and a rating."""
    old = await ticket(client, w, "Old parcel")
    url = f"{T}/{old['id']}"
    for kind, body in (("reply", "Found it, sending today."), ("note", "Courier was late")):
        r = await client.post(f"{url}/messages", headers=w["h"], json={"kind": kind, "body": body})
        ok(r, 201)
    ok(await client.post(f"{url}/attachments", params={"name": "label.txt"}, content=b"label",
                         headers={**w["h"], "Content-Type": "text/plain"}), 201)  # fmt: skip
    await resolved_ago(sm, w["ws"], old["id"], 200)
    await sql(sm, w["ws"],
              "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, comment, source) "
              "SELECT workspace_id, id, contact_id, 4, 'Quick, thanks Priya here', 'portal' "
              "FROM hd_ticket WHERE id = :id", id=UUID(old["id"]))  # fmt: skip
    return old


async def test_old_resolved_tickets_move_to_the_archive(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    old = await an_old_ticket(client, sm, w)
    recent = await ticket(client, w, "Recent")
    await resolved_ago(sm, w["ws"], recent["id"], 30)
    still_open = await ticket(client, w, "Open for long")
    await sql(sm, w["ws"], "UPDATE hd_ticket SET created_at = now() - interval '400 days' "
              "WHERE id = :id", id=UUID(still_open["id"]))  # fmt: skip

    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1
    assert (await client.get(f"{T}/{old['id']}", headers=w["h"])).status_code == 404
    listed = json.dumps(ok(await client.get(T, headers=w["h"])))
    assert old["id"] not in listed and recent["id"] in listed and still_open["id"] in listed
    assert stored_files(files) == 1  # the file stays (retention deletes it later)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 0  # moved once

    # Found by number (1042, T-1042) and by customer, newest first.
    label = f"T-{old['number']}"
    searches = [{"number": label}, {"number": str(old["number"])},
                {"email": "PRIYA@example.org"}, {"contact_id": old["contact"]["id"]}]  # fmt: skip
    for params in searches:
        found = ok(await client.get(f"{A}/tickets", headers=w["agent"], params=params))
        assert [f["id"] for f in found] == [old["id"]], params
    assert found[0]["label"] == label and found[0]["contact_email"] == PRIYA
    for bad in ({}, {"number": "abc"}):
        assert (await client.get(f"{A}/tickets", headers=w["h"], params=bad)).status_code == 422
    assert ok(await client.get(f"{A}/tickets", headers=w["h"], params={"number": "99999"})) == []

    # Opens read only, with everything that was on it.
    got = ok(await client.get(f"{A}/tickets/{old['id']}", headers=w["agent"]))
    bodies = [(m["kind"], m["body"]) for m in got["messages"]]
    assert ("customer", "About Old parcel") in bodies
    assert ("agent", "Found it, sending today.") in bodies
    assert ("note", "Courier was late") in bodies
    assert got["files"] == [{"name": "label.txt", "size": 5, "type": "text/plain"}]
    assert "object_key" not in json.dumps(got)  # never the storage key
    assert (got["rating"]["score"], got["subject"]) == (4, "Old parcel")
    assert (await client.get(f"{A}/tickets/{UUID(int=5)}", headers=w["h"])).status_code == 404

    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "archive"}))
    [entry] = page["entries"]
    assert (entry["action"], entry["detail"]) == ("archived", {"tickets": 1, "days": 120})


async def test_the_archive_age(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    default = ok(await client.get(f"{A}/settings", headers=w["h"]))
    assert (default["after_days"], default["effective_days"]) == (None, rules.DEFAULT_DAYS)
    for bad in (10, 5000):
        r = await client.put(f"{A}/settings", headers=w["h"], json={"after_days": bad})
        assert r.status_code == 422
    r = await client.put(f"{A}/settings", headers=w["agent"], json={"after_days": 200})
    assert r.status_code == 403
    got = ok(await client.put(f"{A}/settings", headers=w["h"], json={"after_days": 200}))
    assert (got["after_days"], got["effective_days"]) == (200, 200)

    t = await ticket(client, w, "Middle aged")
    await resolved_ago(sm, w["ws"], t["id"], 150)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 0  # 150 < 200
    await resolved_ago(sm, w["ws"], t["id"], 201)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1

    # An imported ticket counts from its import: resolved 2 years ago, imported 5 days ago.
    imported = await ticket(client, w, "From Zendesk")
    conn = (
        await sql(
            sm,
            w["ws"],
            "INSERT INTO hd_import_connection (workspace_id, source, "
            "label) VALUES (:ws, 'zendesk', 'acme') RETURNING id",
            ws=w["ws"],
        )
    )[0]["id"]
    await sql(
        sm,
        w["ws"],
        "INSERT INTO hd_import_run (workspace_id, connection_id, source, "
        "state, started_at, finished_at) VALUES (:ws, :c, 'zendesk', 'done', "
        "now() - interval '5 days', now() - interval '5 days')",
        ws=w["ws"],
        c=conn,
    )
    await sql(sm, w["ws"], "UPDATE hd_ticket SET source = 'zendesk', source_id = 'z-1' "
              "WHERE id = :id", id=UUID(imported["id"]))  # fmt: skip
    await resolved_ago(sm, w["ws"], imported["id"], 730)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 0

    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "archive"}))
    actions = [e["action"] for e in page["entries"]]
    assert actions == ["archived", "changed"]  # newest first


async def test_several_workers_never_move_a_ticket_twice(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    monkeypatch.setattr(rules, "BATCH", 2)
    made = [await ticket(client, w, f"Old {n}") for n in range(7)]
    for t in made:
        await resolved_ago(sm, w["ws"], t["id"], 200)
    now = datetime.now(UTC)
    moved = await asyncio.gather(*(archive.archive_workspace(sm, w["ws"], now) for _ in range(3)))
    assert sum(moved) == 7
    rows = await sql(sm, w["ws"], "SELECT id FROM hd_ticket_archive")
    assert sorted(str(r["id"]) for r in rows) == sorted(t["id"] for t in made)
    assert await sql(sm, w["ws"], "SELECT id FROM hd_ticket") == []


async def test_retention_deletes_from_the_archive_too(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    old = await an_old_ticket(client, sm, w)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1
    ok(await client.put("/api/v1/retention", headers=w["h"], json={"closed_ticket_days": 365}))
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    assert await limits.run_retention(sm, store) == 0  # 200 days: kept
    await sql(sm, w["ws"], "UPDATE hd_ticket_archive SET resolved_at = now() - interval "
              "'400 days' WHERE id = :id", id=UUID(old["id"]))  # fmt: skip
    assert stored_files(files) == 1
    assert await limits.run_retention(sm, store) == 1
    assert await sql(sm, w["ws"], "SELECT id FROM hd_ticket_archive") == []
    assert stored_files(files) == 0  # its file went too
    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "retention"}))
    assert page["entries"][0]["detail"] == {"tickets": 0, "days": 365, "archived": 1}


async def test_another_workspace_or_team_cant_see_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    other = await desk(client, sm)
    teams = {}
    for name in ("Billing", "Shipping"):
        made = {"name": name, "auto_assign": False}
        teams[name] = ok(await client.post("/api/v1/teams", headers=w["h"], json=made), 201)["id"]
    billing, shipping = teams["Billing"], teams["Shipping"]
    old = await an_old_ticket(client, sm, w)
    ok(await client.patch(f"{T}/{old['id']}", headers=w["h"], json={"team_id": billing}))
    await resolved_ago(sm, w["ws"], old["id"], 200)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1

    # Another workspace: not found by number, can't open it, and RLS hides the row.
    by_number = {"number": str(old["number"])}
    assert ok(await client.get(f"{A}/tickets", headers=other["h"], params=by_number)) == []
    assert (await client.get(f"{A}/tickets/{old['id']}", headers=other["h"])).status_code == 404
    assert await sql(sm, other["ws"], "SELECT id FROM hd_ticket_archive") == []
    assert len(await sql(sm, w["ws"], "SELECT id FROM hd_ticket_archive")) == 1

    # A team-only role for another team: same as for a live ticket.
    role = ok(await client.post("/api/v1/roles", headers=w["h"], json={
        "name": "Shipping only", "scope": "team_only", "team_id": shipping}), 201)  # fmt: skip
    ok(await client.patch(f"/api/v1/members/{w['neha']}", headers=w["h"],
                          json={"role": "custom", "role_id": role["id"]}))  # fmt: skip
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert ok(await client.get(f"{A}/tickets", headers=neha, params=by_number)) == []
    assert (await client.get(f"{A}/tickets/{old['id']}", headers=neha)).status_code == 404
    assert len(ok(await client.get(f"{A}/tickets", headers=w["h"], params=by_number))) == 1


async def test_privacy_export_and_erase_cover_the_archive(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    old = await an_old_ticket(client, sm, w)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1
    P = "/api/v1/privacy"

    made = ok(await client.post(f"{P}/export", headers=w["h"], json={"email": PRIYA}), 201)
    data = json.loads((await client.get(f"{P}/requests/{made['id']}/file", headers=w["h"])).content)
    [archived] = [t for t in data["tickets"] if t.get("archived")]
    assert archived["subject"] == "Old parcel" and archived["rating"]["score"] == 4
    assert "Found it, sending today." in [m["text"] for m in archived["messages"]]

    erase = {"contact_id": old["contact"]["id"], "confirm_email": PRIYA}
    done = ok(await client.post(f"{P}/erase", headers=w["h"], json=erase), 201)
    assert done["counts"]["archived_tickets"] == 1
    [row] = await sql(sm, w["ws"], "SELECT subject, ticket, messages, files, rating "
                      "FROM hd_ticket_archive")  # fmt: skip
    assert row["subject"] == "(erased)" and row["ticket"]["subject"] == "(erased)"
    assert {m["body"] for m in row["messages"]} == {"(erased)"}
    assert {m["author_name"] for m in row["messages"] if m["kind"] == "customer"} == {
        "Erased customer"
    }
    assert [f["name"] for f in row["files"]] == ["erased"]
    assert row["rating"]["comment"] is None and row["rating"]["score"] == 4
    assert stored_files(files) == 0  # its file is deleted from storage
    assert "Priya" not in json.dumps([dict(row)], default=str).replace("Erased customer", "")
