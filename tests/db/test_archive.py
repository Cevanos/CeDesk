"""The archive tier (md O3), with RLS on: old resolved tickets move out of the ticket list
with their messages, files' names and rating; they are found by number and customer and
open read only; the archive age setting; imported tickets count from their import;
several workers never move a ticket twice; retention deletes from the archive with the
files; privacy export and erase cover it; another workspace or team can't see it."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
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

    # A team-only role for its own team opens it, with its history.
    ok(await client.put(f"/api/v1/roles/{role['id']}", headers=w["h"], json={
        "name": "Billing only", "scope": "team_only", "team_id": billing}))  # fmt: skip
    got = ok(await client.get(f"{A}/tickets/{old['id']}", headers=neha))
    assert got["team_name"] == "Billing" and got["related"]["cevanos_chat"] is None


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

    assert (made["counts"]["tickets"], data["customer"]["email"]) == (1, PRIYA)

    # Its events and webhook deliveries name it (and its subject): erased as for live ones.
    await sql(sm, w["ws"],
        "INSERT INTO hd_webhook (workspace_id, url, events, secret_sealed, secret_hint) "
        "VALUES (:ws, 'https://hooks.example.com/x', ARRAY['ticket.created'], '\\x00', "
        "'…abcd')", ws=w["ws"])  # fmt: skip
    await sql(sm, w["ws"],
        "INSERT INTO hd_webhook_delivery (workspace_id, webhook_id, event, body, state) "
        "SELECT :ws, id, 'ticket.created', jsonb_build_object('ticket', jsonb_build_object("
        "'id', CAST(:t AS text), 'subject', 'Old parcel')), 'pending' FROM hd_webhook",
        ws=w["ws"], t=old["id"])  # fmt: skip
    events = "SELECT payload FROM hd_outbox WHERE ticket_id = :t"
    assert await sql(sm, w["ws"], events, t=UUID(old["id"]))  # some, naming it

    erase = {"contact_id": old["contact"]["id"], "confirm_email": PRIYA}
    done = ok(await client.post(f"{P}/erase", headers=w["h"], json=erase), 201)
    assert done["counts"]["archived_tickets"] == 1
    payloads = [e["payload"] for e in await sql(sm, w["ws"], events, t=UUID(old["id"]))]
    assert payloads and all(p == {"erased": True} for p in payloads)
    [hook] = await sql(sm, w["ws"], "SELECT body, state FROM hd_webhook_delivery")
    assert (hook["body"], hook["state"]) == ({"erased": True}, "failed")
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


async def member_id(sm: Sm, ws: UUID, email: str) -> UUID:
    [row] = await sql(sm, ws, "SELECT id FROM member WHERE email = :e", e=email)
    return UUID(str(row["id"]))


async def test_everything_on_the_ticket_moves_with_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Nothing is lost: every message column (an email's headers too), time logged, tasks,
    approvals, links, side conversations with their messages, Jira issues, the emails
    sent and the Cevanos chat are in the archive and in its read (`related`, one list per
    kind); the type's, team's and assignee's names are as they were, even after the type,
    the team and the member are gone. An email that carried a secret has no body."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    ws = w["ws"]
    admin, neha = await member_id(sm, ws, "admin@example.com"), UUID(w["neha"])
    team = ok(await client.post("/api/v1/teams", headers=w["h"],
                                json={"name": "Returns", "auto_assign": False}), 201)  # fmt: skip
    old = await an_old_ticket(client, sm, w)
    other = await ticket(client, w, "Related, resolved lately")
    await resolved_ago(sm, ws, other["id"], 10)
    tid = UUID(old["id"])
    [kind] = await sql(sm, ws, "INSERT INTO hd_ticket_type (workspace_id, name) "
                       "VALUES (:ws, 'Lost parcel') RETURNING id", ws=ws)  # fmt: skip
    handover = {"reason": "Customer asked for a person", "channel_kind": "web"}
    await sql(sm, ws,
        "UPDATE hd_ticket SET team_id = :team, assignee_id = :neha, type_id = :kind, "
        "conversation_id = 'conv-77', handover = CAST(:h AS jsonb) WHERE id = :id",
        team=UUID(team["id"]), neha=neha, kind=kind["id"], h=json.dumps(handover), id=tid,
    )  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_cevanos_chat (workspace_id, ticket_id, conversation_id, agent_id, "
        "channel_kind, last_message_id, poll_failures, taken_over_at, closed_at) VALUES "
        "(:ws, :t, 'conv-77', 'agent-3', 'web', 'msg-poll-9', 2, now(), now())",
        ws=ws, t=tid)  # fmt: skip
    email_head = {"message_id": "<late-1@mail.example.org>", "from": PRIYA, "to": ["s@x.test"]}
    await sql(sm, ws,
        "INSERT INTO hd_message (workspace_id, ticket_id, kind, author_name, body, email, "
        "channel_message_id) VALUES (:ws, :t, 'customer', 'Priya', 'By email', "
        "CAST(:email AS jsonb), '<late-1@mail.example.org>')",
        ws=ws, t=tid, email=json.dumps(email_head))  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_time_log (workspace_id, ticket_id, member_id, minutes, note, billable) "
        "VALUES (:ws, :t, :m, 25, 'Called the courier', true)", ws=ws, t=tid, m=neha)  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_task (workspace_id, ticket_id, owner_id, text, created_by) "
        "VALUES (:ws, :t, :m, 'Refund the shipping', :a)",
        ws=ws, t=tid, m=neha, a=admin)  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_approval (workspace_id, ticket_id, requester_id, approver_id, what, "
        "amount, currency, state, decided_by, decided_at) VALUES (:ws, :t, :n, :a, "
        "'Refund 499', 499, 'INR', 'approved', :a, now())",
        ws=ws, t=tid, n=neha, a=admin)  # fmt: skip
    a, b = sorted([tid, UUID(other["id"])])
    await sql(sm, ws, "INSERT INTO hd_link (workspace_id, ticket_a, ticket_b, created_by) "
              "VALUES (:ws, :a, :b, :m)", ws=ws, a=a, b=b, m=admin)  # fmt: skip
    [side] = await sql(sm, ws,
        "INSERT INTO hd_side_conversation (workspace_id, ticket_id, subject, to_addresses, "
        "from_address, created_by) VALUES (:ws, :t, 'Courier claim', ARRAY['ops@courier.test'], "
        "'support@x.test', :m) RETURNING id", ws=ws, t=tid, m=neha)  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_side_message (workspace_id, side_id, direction, body, author_name, "
        "from_address, header_message_id) VALUES (:ws, :s, 'in', 'Claim accepted', 'Courier', "
        "'ops@courier.test', '<side-1@courier.test>')", ws=ws, s=side["id"])  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_jira_issue (workspace_id, ticket_id, issue_key, url, made_here) "
        "VALUES (:ws, :t, 'SHIP-42', 'https://acme.atlassian.net/browse/SHIP-42', true)",
        ws=ws, t=tid)  # fmt: skip
    await sql(sm, ws,
        "INSERT INTO hd_email_out (workspace_id, ticket_id, from_address, from_name, "
        "to_addresses, subject, body_text, status, sent_at) VALUES (:ws, :t, 's@x.test', "
        "'Acme', ARRAY[:to], 'Re: Old parcel', 'Found it, sending today.', 'sent', now())",
        ws=ws, t=tid, to=PRIYA)  # fmt: skip
    # A reset link that failed to send: it still holds its secret until archived.
    await sql(sm, ws,
        "INSERT INTO hd_email_out (workspace_id, ticket_id, from_address, from_name, "
        "to_addresses, subject, body_text, status, sensitive) VALUES (:ws, :t, 's@x.test', "
        "'Acme', ARRAY[:to], 'Reset your password', 'https://x.test/reset/SECRET-77', "
        "'failed', true)", ws=ws, t=tid, to=PRIYA)  # fmt: skip

    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 1
    ok(await client.get(f"{T}/{other['id']}", headers=w["h"]))  # the linked one stays live
    # The type, the team and the member go: the archive keeps their names from then.
    await sql(sm, ws, "DELETE FROM hd_ticket_type WHERE id = :id", id=kind["id"])
    gone = await client.delete(f"/api/v1/teams/{team['id']}", headers=w["h"])
    assert gone.status_code == 204
    await sql(sm, ws, "UPDATE member SET name = 'Neha (left)' WHERE id = :m", m=neha)

    [row] = await sql(sm, ws, "SELECT messages, related, email_ids FROM hd_ticket_archive")
    by_email = next(m for m in row["messages"] if m["body"] == "By email")
    assert by_email["email"] == email_head
    assert by_email["channel_message_id"] == "<late-1@mail.example.org>"
    assert row["email_ids"] == ["<late-1@mail.example.org>"]
    # Stored without the secret's body, not only hidden on the way out.
    assert [e["body_text"] for e in row["related"]["emails"] if e["sensitive"]] == [None]
    assert "SECRET-77" not in json.dumps(row["related"])

    got = ok(await client.get(f"{A}/tickets/{old['id']}", headers=w["agent"]))
    assert (got["type_name"], got["team_name"], got["assignee_name"]) == (
        "Lost parcel", "Returns", "Neha")  # fmt: skip
    assert next(m for m in got["messages"] if m["body"] == "By email")["email"] == email_head
    related = got["related"]
    lists = ("time_log", "tasks", "approvals", "links", "side_conversations", "jira_issues",
             "emails")  # fmt: skip
    assert set(related) == {*lists, "cevanos_chat"}
    assert all(related[k] for k in lists)  # every kind shows up
    assert [(t["minutes"], t["note"], t["billable"], t["member_name"])
            for t in related["time_log"]] == [(25, "Called the courier", True, "Neha")]  # fmt: skip
    assert [(t["text"], t["owner_name"]) for t in related["tasks"]] == [
        ("Refund the shipping", "Neha")]  # fmt: skip
    [approval] = related["approvals"]
    assert (approval["what"], approval["state"], approval["requester_name"],
            approval["approver_name"]) == ("Refund 499", "approved", "Neha", "Admin")  # fmt: skip
    assert Decimal(approval["amount"]) == 499 and approval["currency"] == "INR"
    assert [(x["ticket_id"], x["number"], x["label"]) for x in related["links"]] == [
        (other["id"], other["number"], f"T-{other['number']}")]  # fmt: skip
    [side_out] = related["side_conversations"]
    assert (side_out["subject"], side_out["via"], side_out["to"]) == (
        "Courier claim", "email", ["ops@courier.test"])  # fmt: skip
    assert [(m["direction"], m["author_name"], m["body"]) for m in side_out["messages"]] == [
        ("in", "Courier", "Claim accepted")]  # fmt: skip
    assert [(j["key"], j["made_here"]) for j in related["jira_issues"]] == [("SHIP-42", True)]
    sent, secret = related["emails"]
    assert (sent["to_addresses"], sent["subject"], sent["status"], sent["body_text"]) == (
        [PRIYA], "Re: Old parcel", "sent", "Found it, sending today.")  # fmt: skip
    assert (secret["subject"], secret["sensitive"], secret["body_text"]) == (
        "Reset your password", True, None)  # fmt: skip
    chat = related["cevanos_chat"]
    assert (chat["conversation_id"], chat["agent_id"], chat["channel_kind"]) == (
        "conv-77", "agent-3", "web")  # fmt: skip
    assert chat["handover"] == handover and chat["taken_over_at"] and chat["closed_at"]
    shown = json.dumps(got)
    for internal in ("object_key", "msg-poll-9", "poll_failures", "next_poll_at",
                     "header_message_id", "<side-1@courier.test>", "SECRET-77"):  # fmt: skip
        assert internal not in shown


async def test_a_ticket_a_live_one_still_needs_waits(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """A parent with a live child, a ticket merged into by a live one, or one linked to an
    open ticket isn't archived (the live one would lose it); it moves once they have."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    ws = w["ws"]
    parent = await ticket(client, w, "Parent")
    child = await ticket(client, w, "Child, still open")
    merged_into = await ticket(client, w, "Kept after a merge")
    merged = await ticket(client, w, "Merged duplicate")
    linked = await ticket(client, w, "Linked to an open one")
    still_open = await ticket(client, w, "Open, linked")
    for t in (parent, merged_into, merged, linked):
        await resolved_ago(sm, ws, t["id"], 200)
    await sql(sm, ws, "UPDATE hd_ticket SET parent_id = :p WHERE id = :c",
              p=UUID(parent["id"]), c=UUID(child["id"]))  # fmt: skip
    await sql(sm, ws, "UPDATE hd_ticket SET merged_into_id = :k WHERE id = :m",
              k=UUID(merged_into["id"]), m=UUID(merged["id"]))  # fmt: skip
    a, b = sorted([UUID(linked["id"]), UUID(still_open["id"])])
    await sql(sm, ws, "INSERT INTO hd_link (workspace_id, ticket_a, ticket_b) "
              "VALUES (:ws, :a, :b)", ws=ws, a=a, b=b)  # fmt: skip

    async def archived() -> set[str]:
        return {str(r["id"]) for r in await sql(sm, ws, "SELECT id FROM hd_ticket_archive")}

    # Only the merged duplicate moves: nothing live names it. What it names stays.
    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 1
    assert await archived() == {merged["id"]}
    [row] = await sql(sm, ws, "SELECT ticket FROM hd_ticket_archive")
    assert row["ticket"]["merged_into_id"] == merged_into["id"]  # kept, not set to null
    # Its merge target is named by no live ticket now: it moves on the next run.
    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 1
    assert await archived() == {merged["id"], merged_into["id"]}
    [child_now] = await sql(sm, ws, "SELECT parent_id FROM hd_ticket WHERE id = :c",
                            c=UUID(child["id"]))  # fmt: skip
    assert str(child_now["parent_id"]) == parent["id"]  # the live child keeps its parent
    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 0

    # The child and the open linked ticket are resolved long ago too: they all move, the
    # child first, then its parent.
    for t in (child, still_open):
        await resolved_ago(sm, ws, t["id"], 200)
    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 3
    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 1
    assert await sql(sm, ws, "SELECT id FROM hd_ticket") == []
    [row] = await sql(sm, ws, "SELECT ticket FROM hd_ticket_archive WHERE id = :c",
                      c=UUID(child["id"]))  # fmt: skip
    assert row["ticket"]["parent_id"] == parent["id"]


async def test_retention_deletes_an_archived_tickets_events(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    """As for a live ticket: its events and webhook deliveries (which name it) go too."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    ws = w["ws"]
    old = await an_old_ticket(client, sm, w)
    keep = await ticket(client, w, "Kept")
    await sql(sm, ws,
        "INSERT INTO hd_webhook (workspace_id, url, events, secret_sealed, secret_hint) "
        "VALUES (:ws, 'https://hooks.example.com/x', ARRAY['ticket.created'], '\\x00', "
        "'…abcd')", ws=ws)  # fmt: skip
    for t in (old, keep):
        await sql(sm, ws,
            "INSERT INTO hd_webhook_delivery (workspace_id, webhook_id, event, body, state) "
            "SELECT :ws, id, 'ticket.created', jsonb_build_object('ticket', "
            "jsonb_build_object('id', CAST(:t AS text), 'subject', 'x')), 'delivered' "
            "FROM hd_webhook", ws=ws, t=t["id"])  # fmt: skip
    assert await archive.archive_workspace(sm, ws, datetime.now(UTC)) == 1
    count = "SELECT count(*) AS n FROM hd_outbox WHERE ticket_id = :t"
    assert (await sql(sm, ws, count, t=UUID(old["id"])))[0]["n"] >= 1
    ok(await client.put("/api/v1/retention", headers=w["h"], json={"closed_ticket_days": 180}))
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    assert await limits.run_retention(sm, store) == 1
    assert (await sql(sm, ws, count, t=UUID(old["id"])))[0]["n"] == 0
    assert (await sql(sm, ws, count, t=UUID(keep["id"])))[0]["n"] >= 1
    hooks = await sql(sm, ws, "SELECT body->'ticket'->>'id' AS t FROM hd_webhook_delivery")
    assert [h["t"] for h in hooks] == [keep["id"]]
    assert stored_files(files) == 0


async def test_archived_files_count_as_storage(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    old = await an_old_ticket(client, sm, w)  # one file of 5 bytes
    assert ok(await client.get("/api/v1/usage", headers=w["h"]))["storage_bytes"] == 5
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1
    assert ok(await client.get("/api/v1/usage", headers=w["h"]))["storage_bytes"] == 5
    erase = {"contact_id": old["contact"]["id"], "confirm_email": PRIYA}
    ok(await client.post("/api/v1/privacy/erase", headers=w["h"], json=erase), 201)
    assert ok(await client.get("/api/v1/usage", headers=w["h"]))["storage_bytes"] == 0


async def test_a_late_email_answer_to_an_archived_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The archived ticket is read only: the answer (by its reply headers or '[T-1234]'
    from its customer) is a new ticket that says which one it follows. The same email
    delivered again is still a duplicate."""
    from cedesk.helpdesk.service.email_in import Outcome, receive

    sm = app_sessionmaker
    w = await desk(client, sm)
    support = f"support@{w['slug']}.test"
    ok(await client.post("/api/v1/email-addresses", headers=w["h"],
                         json={"address": support, "name": "Acme"}), 201)  # fmt: skip

    def mail(subject: str, message_id: str, in_reply_to: str | None = None) -> bytes:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = f"Priya <{PRIYA}>", support, subject
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = msg["References"] = in_reply_to
        msg.set_content("Any news?")
        return msg.as_bytes()

    first = await receive(sm, mail("Lost parcel", "<first@mail.example.org>"))
    assert first.outcome is Outcome.created
    [made] = await sql(sm, w["ws"], "SELECT number FROM hd_ticket WHERE id = :id",
                       id=first.ticket_id)  # fmt: skip
    label = f"T-{made['number']}"
    await resolved_ago(sm, w["ws"], str(first.ticket_id), 200)
    assert await archive.archive_workspace(sm, w["ws"], datetime.now(UTC)) == 1

    again = await receive(sm, mail("Lost parcel", "<first@mail.example.org>"))
    assert again.outcome is Outcome.duplicate
    for raw in (mail("Re: Lost parcel", "<late-2@mail.example.org>", "<first@mail.example.org>"),
                mail(f"Still waiting [{label}]", "<late-3@mail.example.org>")):  # fmt: skip
        got = await receive(sm, raw)
        assert got.outcome is Outcome.created and got.ticket_id != first.ticket_id
        t = ok(await client.get(f"{T}/{got.ticket_id}", headers=w["h"]))
        lines = [m["body"] for m in t["messages"] if m["kind"] == "system"]
        assert f"Follows archived ticket {label}" in lines
        assert "archived-follow-up" in t["tags"]
    # '[T-…]' from someone else is not theirs to follow: an ordinary new ticket.
    other = EmailMessage()
    other["From"], other["To"], other["Subject"] = "x@example.net", support, f"Re [{label}]"
    other["Message-ID"] = "<other@example.net>"
    other.set_content("Hi")
    got = await receive(sm, other.as_bytes())
    t = ok(await client.get(f"{T}/{got.ticket_id}", headers=w["h"]))
    assert "archived-follow-up" not in t["tags"]
