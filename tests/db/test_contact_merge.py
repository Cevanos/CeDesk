"""Contact merge (feature-gap plan 2.4) with RLS on: a preview first, then the merge with
an explicit confirm; tickets, ratings, notes, values and addresses move; the duplicate's
address then finds the survivor; erased customers and privacy requests block it; only
Admins and Supervisors merge; audited by id; another workspace can't touch it."""

import json
import os
from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, sql
from tests.db.test_contacts import C, customer, field

pytestmark = pytest.mark.db


async def pair(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Ana (survivor: a ticket, plan pro) and her duplicate Ana B (two tickets, a note, a
    rating, a phone, plan free and seats 3, a portal sign-in)."""
    w = await desk(client, sm)
    ok(await field(client, w["h"], "plan", "select", options=["free", "pro"]), 201)
    ok(await field(client, w["h"], "seats", "number"), 201)
    ana, ana_t = await customer(client, w, "Ana", "ana@shop.test")
    dup, dup_t = await customer(client, w, "Ana B", "ana.b@mail.test")
    await customer(client, w, "Ana B", "ana.b@mail.test", subject="Second")
    ok(
        await client.put(
            f"{C}/{ana}/attributes", headers=w["h"], json={"attributes": {"plan": "pro"}}
        )
    )
    ok(
        await client.put(
            f"{C}/{dup}/attributes",
            headers=w["h"],
            json={"attributes": {"plan": "free", "seats": 3}},
        )
    )
    ok(await client.patch(f"{C}/{dup}/profile", headers=w["h"], json={"vip": True}))
    ok(await client.post(f"{C}/{dup}/notes", headers=w["h"], json={"body": "Same person"}), 201)
    await sql(sm, w["ws"], "UPDATE hd_contact SET phone = '555-0100' WHERE id = :c", c=dup)
    await sql(
        sm,
        w["ws"],
        "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, "
        "source) VALUES (:w, :t, :c, 4, 'email')",
        w=w["ws"],
        t=dup_t,
        c=dup,
    )
    await sql(
        sm,
        w["ws"],
        "INSERT INTO hd_portal_session (workspace_id, contact_id, token_hash, "
        "expires_at) VALUES (:w, :c, :h, now() + interval '1 day')",
        w=w["ws"],
        c=dup,
        h=os.urandom(32),
    )
    return {**w, "ana": ana, "ana_t": ana_t, "dup": dup, "dup_t": dup_t}


async def test_preview_then_merge_moves_everything(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await pair(client, app_sessionmaker)
    ana, dup = w["ana"], w["dup"]
    body = {"duplicate_id": dup}
    p = ok(await client.post(f"{C}/{ana}/merge-preview", headers=w["h"], json=body))
    assert (p["survivor"]["id"], p["duplicate"]["id"], p["can_merge"]) == (ana, dup, True)
    m = p["moves"]
    assert (m["tickets"], m["ratings"], m["notes"], m["other_emails"], m["portal_sessions"]) == (
        2,
        1,
        1,
        1,
        1,
    )
    assert sorted(p["taken"]) == ["attributes.seats", "phone", "vip"]
    assert {c["field"]: (c["kept"], c["dropped"]) for c in p["conflicts"]} == {
        "name": ("Ana", "Ana B"),
        "attributes.plan": ("pro", "free"),
    }
    assert p["result"]["attributes"] == {"plan": "pro", "seats": 3} and p["result"]["vip"]
    assert any("signed in" in x for x in p["warnings"])
    # The preview changed nothing.
    assert ok(await client.get(f"{C}/{dup}", headers=w["h"]))["ticket_count"] == 2

    # Without confirm: 422, and nothing happens.
    r = await client.post(f"{C}/{ana}/merge", headers=w["h"], json=body)
    assert r.status_code == 422
    done = ok(await client.post(f"{C}/{ana}/merge", headers=w["h"], json={**body, "confirm": True}))
    c = done["contact"]
    assert (c["id"], c["name"], c["email"], c["phone"], c["vip"]) == (
        ana,
        "Ana",
        "ana@shop.test",
        "555-0100",
        True,
    )
    assert c["ticket_count"] == 3 and c["other_emails"] == ["ana.b@mail.test"]
    assert c["attributes"] == {"plan": "pro", "seats": 3}
    assert (done["moved"]["tickets"], done["moved"]["notes"], done["moved"]["ratings"]) == (2, 1, 1)
    assert (await client.get(f"{C}/{dup}", headers=w["h"])).status_code == 404
    detail = ok(await client.get(f"{C}/{ana}", headers=w["h"]))
    assert detail["note_count"] == 1 and detail["csat_count"] == 1
    ticket = ok(await client.get(f"{T}/{w['dup_t']}", headers=w["h"]))
    assert ticket["contact"]["id"] == ana
    # The portal sign-in of the duplicate ended with it.
    left = await sql(app_sessionmaker, w["ws"], "SELECT count(*) FROM hd_portal_session")
    assert left[0][0] == 0
    # The duplicate's address now finds the survivor: a new ticket from it, and a search.
    again = ok(
        await client.post(
            T,
            headers=w["h"],
            json={"subject": "Third", "contact": {"email": "Ana.B@mail.test", "name": "Ana B"}},
        ),
        201,
    )
    assert again["contact"]["id"] == ana
    found = ok(await client.get(C, headers=w["h"], params={"q": "ana.b@"}))["contacts"]
    assert [x["id"] for x in found] == [ana]
    # The timeline shows the merge; the audit names ids only.
    items = ok(await client.get(f"{C}/{ana}/timeline", headers=w["h"], params={"kind": "merge"}))[
        "items"
    ]
    assert [(i["text"], i["detail"]) for i in items] == [("Ana B", "ana.b@mail.test")]
    [entry] = [e for e in await audit(client, w, "contacts") if e["action"] == "merged"]
    assert entry["detail"]["merged_id"] == dup and entry["target"] == f"Customer {ana}"
    assert "mail.test" not in json.dumps(entry) and "Ana B" not in json.dumps(entry)
    # Merging again: the duplicate is gone.
    r = await client.post(f"{C}/{ana}/merge", headers=w["h"], json={**body, "confirm": True})
    assert r.status_code == 404


async def test_who_may_merge_and_what_blocks_it(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await pair(client, app_sessionmaker)
    ana, dup = w["ana"], w["dup"]
    body = {"duplicate_id": dup, "confirm": True}
    for who in ("Neha", "Meena"):  # agents and light agents can't
        h = await as_(client, w, who)
        assert (
            await client.post(f"{C}/{ana}/merge-preview", headers=h, json={"duplicate_id": dup})
        ).status_code == 403
        assert (await client.post(f"{C}/{ana}/merge", headers=h, json=body)).status_code == 403
    r = await client.post(
        f"{C}/{ana}/merge", headers=w["h"], json={"duplicate_id": ana, "confirm": True}
    )
    assert r.status_code == 422
    # A privacy request on the duplicate blocks it.
    await sql(
        app_sessionmaker,
        w["ws"],
        "INSERT INTO hd_privacy_request (workspace_id, kind, "
        "contact_id, expires_at) VALUES (:w, 'export', :c, now())",
        w=w["ws"],
        c=dup,
    )
    p = ok(
        await client.post(f"{C}/{ana}/merge-preview", headers=w["h"], json={"duplicate_id": dup})
    )
    assert not p["can_merge"] and "privacy" in p["blocked"]
    assert (await client.post(f"{C}/{ana}/merge", headers=w["h"], json=body)).status_code == 409
    await sql(app_sessionmaker, w["ws"], "DELETE FROM hd_privacy_request")
    # An erased customer can't be merged either way.
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_contact SET erased_at = now() WHERE id = :c", c=ana
    )
    assert (await client.post(f"{C}/{ana}/merge", headers=w["h"], json=body)).status_code == 409
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_contact SET erased_at = NULL")
    # A Supervisor may.
    sam = await as_(client, w, "Sam")
    ok(await client.post(f"{C}/{ana}/merge", headers=sam, json=body))


async def test_another_workspace_cant_merge(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await pair(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    theirs, _ = await customer(client, them, "Zed", "zed@else.test")
    body = {"duplicate_id": w["dup"], "confirm": True}
    preview = {"duplicate_id": w["dup"]}
    for path, sent in (
        (f"{C}/{w['ana']}/merge-preview", preview),
        (f"{C}/{w['ana']}/merge", body),
        (f"{C}/{theirs}/merge", body),
    ):
        r = await client.post(path, headers=them["h"], json=sent)
        assert r.status_code == 404, path
    r = await client.post(
        f"{C}/{w['ana']}/merge", headers=w["h"], json={"duplicate_id": theirs, "confirm": True}
    )
    assert r.status_code == 404
    assert ok(await client.get(f"{C}/{w['dup']}", headers=w["h"]))["ticket_count"] == 2
