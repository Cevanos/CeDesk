"""Bulk ticket changes (feature-gap plan 1.3) with RLS on: one action on many tickets,
each through the single-edit path and checks, with a result per ticket; partial failure
never stops or undoes the rest; capped, rate-limited, audited, and blind to other
workspaces."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, events, lines, soon, ticket

pytestmark = pytest.mark.db

B = f"{T}/bulk"


async def test_fifty_tickets_assigned_in_one_request(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ids = [await ticket(client, w, f"T{n}", "Billing", None) for n in range(50)]
    got = ok(await client.post(B, headers=w["h"], json={
        "ticket_ids": ids, "action": "assign", "assignee_id": w["ids"]["Neha"]}))  # fmt: skip
    assert (got["succeeded"], got["failed"]) == (50, 0)
    assert [r["ticket_id"] for r in got["results"]] == ids
    page = ok(await client.get(f"{T}?scope=everyone&limit=100", headers=w["h"]))
    assert {t["assignee_name"] for t in page["tickets"]} == {"Neha"}
    # The same history line and event as a single edit.
    assert "Assigned to Neha by Admin" in await lines(client, w, ids[0])
    kinds = [e["event"] for e in await events(app_sessionmaker, w["ws"], ids[0])]
    assert "assignment.changed" in kinds
    entry = (await audit(client, w, "tickets"))[0]
    assert entry["action"] == "bulk_changed"
    assert entry["detail"]["succeeded"] == 50 and entry["detail"]["failed"] == 0
    assert entry["detail"]["value"] == w["ids"]["Neha"]


async def test_partial_failure_reports_each_ticket_and_keeps_the_rest(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    good = await ticket(client, w, "Good", "Billing", "Neha")
    done = await ticket(client, w, "Done", "Billing", "Neha")
    ok(await client.post(f"{T}/{done}/resolve", headers=w["h"]))
    theirs = await ticket(client, them, "Theirs", "Billing", "Neha")
    missing = str(uuid4())
    got = ok(await client.post(B, headers=w["h"], json={
        "ticket_ids": [good, done, theirs, missing, good], "action": "snooze",
        "until": soon(hours=2)}))  # fmt: skip
    by_id = {r["ticket_id"]: r for r in got["results"]}
    assert len(got["results"]) == 4  # the repeat counts once
    assert by_id[good]["ok"] is True and by_id[good]["error"] is None
    assert by_id[done]["error"]["code"] == "Conflict"
    assert by_id[theirs]["error"]["code"] == "NotFound"
    assert by_id[missing]["error"]["code"] == "NotFound"
    assert (got["succeeded"], got["failed"]) == (1, 3)
    assert ok(await client.get(f"{T}/{good}", headers=w["h"]))["status"] == "pending"
    # The other workspace's ticket is untouched.
    assert ok(await client.get(f"{T}/{theirs}", headers=them["h"]))["status"] == "open"


async def test_each_ticket_keeps_the_single_edit_rights(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    mine = await ticket(client, w, "Mine", "Billing", "Neha")
    ravis = await ticket(client, w, "Ravis", "Shipping", "Ravi")
    neha = await as_(client, w, "Neha")
    # An agent may set priority on any ticket, but taking Ravi's ticket needs assign_others.
    got = ok(await client.post(B, headers=neha, json={
        "ticket_ids": [mine, ravis], "action": "set_priority", "priority": "high"}))  # fmt: skip
    assert got["succeeded"] == 2
    got = ok(await client.post(B, headers=neha, json={
        "ticket_ids": [mine, ravis], "action": "assign", "assignee_id": None}))  # fmt: skip
    results = {r["ticket_id"]: r for r in got["results"]}
    assert results[mine]["ok"] and results[ravis]["error"]["code"] == "Forbidden"
    # A light agent can't make bulk changes at all.
    meena = await as_(client, w, "Meena")
    r = await client.post(B, headers=meena, json={
        "ticket_ids": [mine], "action": "add_tags", "tags": ["vip"]})  # fmt: skip
    assert r.status_code == 403
    # Tags, status and resolve go through the same path.
    got = ok(
        await client.post(
            B,
            headers=w["h"],
            json={"ticket_ids": [mine, ravis], "action": "add_tags", "tags": ["Late", "refund"]},
        )
    )
    assert got["succeeded"] == 2
    assert ok(await client.get(f"{T}/{mine}", headers=w["h"]))["tags"] == ["late", "refund"]
    ok(await client.post(B, headers=w["h"], json={
        "ticket_ids": [mine], "action": "remove_tags", "tags": ["late"]}))  # fmt: skip
    ok(await client.post(B, headers=w["h"], json={
        "ticket_ids": [mine], "action": "set_status", "status": "resolved"}))  # fmt: skip
    detail = ok(await client.get(f"{T}/{mine}", headers=w["h"]))
    assert (detail["tags"], detail["status"]) == (["refund"], "resolved")
    kinds = [e["event"] for e in await events(app_sessionmaker, w["ws"], mine)]
    assert "ticket.resolved" in kinds


async def test_bad_requests_and_the_rate_limit(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "One", "Billing", "Neha")
    bad = [
        {"ticket_ids": [], "action": "unsnooze"},
        {"ticket_ids": [str(uuid4()) for _ in range(101)], "action": "unsnooze"},
        {"ticket_ids": [t], "action": "assign"},  # no assignee_id
        {"ticket_ids": [t], "action": "set_status", "status": "open", "priority": "low"},
        {"ticket_ids": [t], "action": "set_status", "status": None},
        {"ticket_ids": [t], "action": "add_tags", "tags": []},
        {"ticket_ids": [t], "action": "explode"},
    ]
    for body in bad:
        assert (await client.post(B, headers=w["h"], json=body)).status_code == 422, body
    r = await client.post(B, headers=w["h"], json={
        "ticket_ids": [t], "action": "snooze", "until": soon(minutes=-1)})  # fmt: skip
    assert r.status_code == 422  # checked once, before any ticket
    sent = [
        (await client.post(B, headers=w["h"], json={"ticket_ids": [t], "action": "unsnooze"}))
        for _ in range(12)
    ]
    codes = [r.status_code for r in sent]
    assert 429 in codes and "Retry-After" in sent[codes.index(429)].headers
