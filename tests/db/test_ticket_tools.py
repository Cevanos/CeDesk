"""Ticket tools (md X1-X4, X6, X8, X9) through the real API with RLS on: merge, links,
child tickets, approvals, tasks and the time log, with the roles from md 6.6."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.service.task_alerts import alert_workspace
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


def lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """A seeded workspace: Admin, Adarsh (supervisor), Neha and Chetan (agents), Lata
    (light agent). Neha is in Billing, Chetan in Shipping."""
    slug = await new_workspace(sm, seed=True)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    ids = {
        "adarsh": await add(client, token, "adarsh@example.com", "supervisor", "Adarsh"),
        "neha": await add(client, token, "neha@example.com", "agent", "Neha"),
        "chetan": await add(client, token, "chetan@example.com", "agent", "Chetan"),
        "lata": await add(client, token, "lata@example.com", "light_agent", "Lata"),
    }
    teams = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/teams", headers=h))}
    types = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/ticket-types", headers=h))}
    ok(
        await client.patch(
            f"/api/v1/teams/{teams['Billing']}", headers=h, json={"member_ids": [ids["neha"]]}
        )
    )
    ok(
        await client.patch(
            f"/api/v1/teams/{teams['Shipping']}", headers=h, json={"member_ids": [ids["chetan"]]}
        )
    )
    who = {name: bearer(await sign_in(client, slug, f"{name}@example.com")) for name in ids}
    return {
        "slug": slug,
        "h": h,
        "ids": ids,
        "who": who,
        "teams": teams,
        "types": types,
        "ws": UUID(token.split(".")[0]),
    }


async def ticket(
    client: AsyncClient, w: dict[str, Any], subject: str, **extra: Any
) -> dict[str, Any]:
    body = {
        "subject": subject,
        "contact": {"email": "priya@example.com", "name": "Priya Sharma"},
        "description": f"Customer: {subject}",
        "assignee_id": w["ids"]["neha"],
        **extra,
    }
    return ok(await client.post(T, headers=w["h"], json=body), 201)  # type: ignore[no-any-return]


async def notes_for(client: AsyncClient, h: dict[str, str], kind: str) -> list[str]:
    inbox = ok(await client.get("/api/v1/notifications", headers=h))
    return [n["text"] for n in inbox["notifications"] if n["kind"] == kind]


# --- Merge (X1) ---


async def test_merge_copies_the_conversation_and_resolves_the_duplicate(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    keep = await ticket(client, w, "Torn T-shirt")
    dup = await ticket(client, w, "Torn T-shirt again")
    neha = w["who"]["neha"]
    ok(
        await client.post(
            f"{T}/{dup['id']}/messages",
            headers=neha,
            json={"kind": "note", "body": "Same order as the other one"},
        ),
        201,
    )

    merged = ok(
        await client.post(f"{T}/{dup['id']}/merge", headers=neha, json={"into": keep["id"]})
    )
    assert merged["id"] == keep["id"]
    bodies = [(m["kind"], m["body"]) for m in merged["messages"] if m["kind"] != "system"]
    assert bodies == [
        ("customer", "Customer: Torn T-shirt"),
        ("customer", "Customer: Torn T-shirt again"),
        ("note", "Same order as the other one"),
    ]
    assert lines(merged)[-1] == (
        f"{dup['label']} merged into this ticket by Neha · 2 messages copied"
    )

    source = ok(await client.get(f"{T}/{dup['id']}", headers=neha))
    assert source["status"] == "resolved"
    assert source["merged_into_id"] == keep["id"]
    assert lines(source)[-1] == f"Merged into {keep['label']} by Neha"

    related = ok(await client.get(f"{T}/{keep['id']}/related", headers=neha))
    assert [t["label"] for t in related["merged_from"]] == [dup["label"]]
    assert (
        ok(await client.get(f"{T}/{dup['id']}/related", headers=neha))["merged_into"]["id"]
        == keep["id"]
    )

    # No webhook from the merged ticket: no ticket.resolved event for it.
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        events = [r["payload"]["event"] for r in await outbox.for_ticket(s, UUID(dup["id"]))]
    assert "ticket.resolved" not in events


async def test_merge_refuses_what_it_should(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    a = await ticket(client, w, "A")
    b = await ticket(client, w, "B")
    neha, lata = w["who"]["neha"], w["who"]["lata"]

    assert (
        await client.post(f"{T}/{a['id']}/merge", headers=neha, json={"into": a["id"]})
    ).status_code == 422
    assert (
        await client.post(f"{T}/{a['id']}/merge", headers=lata, json={"into": b["id"]})
    ).status_code == 403
    ok(await client.post(f"{T}/{b['id']}/resolve", headers=neha))
    r = await client.post(f"{T}/{a['id']}/merge", headers=neha, json={"into": b["id"]})
    assert r.status_code == 409
    assert "open ticket" in r.json()["error"]["message"]

    c = await ticket(client, w, "C")
    ok(await client.post(f"{T}/{a['id']}/merge", headers=neha, json={"into": c["id"]}))
    again = await client.post(f"{T}/{a['id']}/merge", headers=neha, json={"into": c["id"]})
    assert again.status_code == 409


# --- Links (X2) ---


async def test_links_work_both_ways_once(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    a = await ticket(client, w, "Late parcel")
    b = await ticket(client, w, "Late parcel, other order")
    neha = w["who"]["neha"]

    card = ok(
        await client.post(f"{T}/{a['id']}/links", headers=neha, json={"ticket_id": b["id"]}), 201
    )
    assert [t["label"] for t in card["links"]] == [b["label"]]
    ok(
        await client.post(f"{T}/{b['id']}/links", headers=neha, json={"ticket_id": a["id"]}), 201
    )  # same pair: nothing new
    other_side = ok(await client.get(f"{T}/{b['id']}/related", headers=neha))
    assert [t["label"] for t in other_side["links"]] == [a["label"]]
    a_lines = lines(ok(await client.get(f"{T}/{a['id']}", headers=neha)))
    assert a_lines.count(f"Linked to {b['label']} by Neha") == 1

    assert (
        await client.post(f"{T}/{a['id']}/links", headers=neha, json={"ticket_id": a["id"]})
    ).status_code == 422
    assert (
        await client.post(
            f"{T}/{a['id']}/links", headers=w["who"]["lata"], json={"ticket_id": b["id"]}
        )
    ).status_code == 403

    assert (await client.delete(f"{T}/{b['id']}/links/{a['id']}", headers=neha)).status_code == 204
    assert ok(await client.get(f"{T}/{a['id']}/related", headers=neha))["links"] == []
    assert (await client.delete(f"{T}/{b['id']}/links/{a['id']}", headers=neha)).status_code == 404


# --- Child tickets (X3, X4) ---


async def test_a_child_ticket_copies_the_customer_type_and_fields_for_another_team(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    parent = await ticket(
        client,
        w,
        "Damaged parcel",
        type_id=w["types"]["Refund"],
        fields={"order_id": "#1042", "amount": 1500},
    )
    neha = w["who"]["neha"]
    child = ok(
        await client.post(
            f"{T}/{parent['id']}/children",
            headers=neha,
            json={"team_id": w["teams"]["Shipping"], "note": "Please check the courier photos"},
        ),
        201,
    )
    assert child["contact"]["email"] == "priya@example.com"
    assert child["type_id"] == w["types"]["Refund"]
    assert child["fields"]["order_id"] == "#1042"
    assert child["channel"] == "portal"  # the parent was not email
    assert child["parent_id"] == parent["id"]
    assert child["team_name"] == "Shipping"
    assert child["assignee_name"] == "Chetan"  # routing ran
    notes = [m for m in child["messages"] if m["kind"] == "note"]
    assert [(n["body"], n["author_name"]) for n in notes] == [
        ("Please check the courier photos", "Neha")
    ]

    p = ok(await client.get(f"{T}/{parent['id']}", headers=neha))
    assert f"Child ticket {child['label']} created for Shipping by Neha" in lines(p)
    assert p["child_count"] == 1
    card = ok(await client.get(f"{T}/{parent['id']}/related", headers=neha))
    assert [t["label"] for t in card["children"]] == [child["label"]]
    assert (
        ok(await client.get(f"{T}/{child['id']}/related", headers=neha))["parent"]["id"]
        == parent["id"]
    )


async def test_a_parent_waits_for_its_children_and_hears_when_one_is_resolved(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    parent = await ticket(client, w, "Wrong size delivered")
    neha, chetan = w["who"]["neha"], w["who"]["chetan"]
    child = ok(
        await client.post(
            f"{T}/{parent['id']}/children", headers=neha, json={"team_id": w["teams"]["Shipping"]}
        ),
        201,
    )
    assert child["subject"] == "Wrong size delivered"

    r = await client.post(f"{T}/{parent['id']}/resolve", headers=neha)
    assert r.status_code == 409
    assert child["label"] in r.json()["error"]["message"]
    # The status field and macros are held to the same rule.
    r = await client.patch(f"{T}/{parent['id']}", headers=neha, json={"status": "resolved"})
    assert r.status_code == 409

    ok(await client.post(f"{T}/{child['id']}/resolve", headers=chetan))
    p = ok(await client.get(f"{T}/{parent['id']}", headers=neha))
    assert f"Child ticket {child['label']} resolved by Chetan" in lines(p)
    told = await notes_for(client, neha, "child_resolved")
    assert told == [
        f"Child ticket {child['label']} resolved by Chetan (on {parent['label']}: "
        "Wrong size delivered)"
    ]
    ok(await client.post(f"{T}/{parent['id']}/resolve", headers=neha))


async def test_a_macro_cannot_resolve_a_parent_with_an_open_child(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    parent = await ticket(
        client,
        w,
        "Refund please",
        type_id=w["types"]["Refund"],
        fields={"order_id": "#7", "amount": 100},
    )
    neha = w["who"]["neha"]
    ok(
        await client.post(
            f"{T}/{parent['id']}/children", headers=neha, json={"team_id": w["teams"]["Shipping"]}
        ),
        201,
    )
    macros = {m["name"]: m["id"] for m in ok(await client.get("/api/v1/macros", headers=neha))}
    r = await client.post(f"{T}/{parent['id']}/macros/{macros['Refund approved']}", headers=neha)
    assert r.status_code == 409
    after = ok(await client.get(f"{T}/{parent['id']}", headers=neha))
    assert after["status"] == "open"
    assert not [m for m in after["messages"] if m["kind"] == "agent"]  # no step ran


# --- Approvals (X6) ---


async def test_an_agent_asks_a_supervisor_who_approves(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(
        client,
        w,
        "Refund ₹1,500",
        type_id=w["types"]["Refund"],
        fields={"order_id": "#1042", "amount": 1500},
    )
    neha, adarsh = w["who"]["neha"], w["who"]["adarsh"]
    asked = ok(
        await client.post(
            f"{T}/{t['id']}/approvals",
            headers=neha,
            json={"approver_id": w["ids"]["adarsh"], "amount": "1500"},
        ),
        201,
    )
    assert asked["what"] == "Refund of ₹1,500 for order #1042"
    assert asked["state"] == "pending"
    assert (asked["requester_name"], asked["approver_name"]) == ("Neha", "Adarsh")
    assert await notes_for(client, adarsh, "approval") == [
        f"Neha asks you to approve on {t['label']}: Refund of ₹1,500 for order #1042"
    ]
    waiting = ok(await client.get("/api/v1/approvals/waiting", headers=adarsh))
    assert [a["id"] for a in waiting] == [asked["id"]]

    url = f"/api/v1/approvals/{asked['id']}/decide"
    assert (await client.post(url, headers=neha, json={"decision": "approve"})).status_code == 403
    done = ok(
        await client.post(
            url, headers=adarsh, json={"decision": "approve", "note": "OK, photo checked"}
        )
    )
    assert done["state"] == "approved"
    assert done["decided_by_name"] == "Adarsh"
    assert await notes_for(client, neha, "approval") == [
        f"Approved by Adarsh: Refund of ₹1,500 for order #1042 (on {t['label']})"
    ]
    page = ok(await client.get(f"{T}/{t['id']}", headers=neha))
    assert "Approved by Adarsh: Refund of ₹1,500 for order #1042 · OK, photo checked" in lines(page)
    again = await client.post(url, headers=adarsh, json={"decision": "reject"})
    assert again.status_code == 409
    assert ok(await client.get("/api/v1/approvals/waiting", headers=adarsh)) == []


async def test_who_may_approve(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Goodwill voucher")
    neha, adarsh = w["who"]["neha"], w["who"]["adarsh"]
    path = f"{T}/{t['id']}/approvals"
    for approver in ("chetan", "lata"):  # an agent and a light agent can't approve
        r = await client.post(
            path, headers=neha, json={"approver_id": w["ids"][approver], "what": "₹200 voucher"}
        )
        assert r.status_code == 422
    r = await client.post(
        path, headers=adarsh, json={"approver_id": w["ids"]["adarsh"], "what": "x"}
    )
    assert r.status_code == 422  # never yourself
    r = await client.post(path, headers=neha, json={"approver_id": w["ids"]["adarsh"]})
    assert r.status_code == 422  # nothing to approve
    assert (
        await client.post(
            path, headers=w["who"]["lata"], json={"approver_id": w["ids"]["adarsh"], "what": "x"}
        )
    ).status_code == 403

    # Adarsh asks the Admin; Adarsh can't decide his own request, the Admin can.
    admin_id = ok(await client.get("/api/v1/me", headers=w["h"]))["member"]["id"]
    asked = ok(
        await client.post(
            path, headers=adarsh, json={"approver_id": admin_id, "what": "Refund over limit"}
        ),
        201,
    )
    url = f"/api/v1/approvals/{asked['id']}/decide"
    assert (await client.post(url, headers=adarsh, json={"decision": "approve"})).status_code == 403
    assert (
        ok(await client.post(url, headers=w["h"], json={"decision": "reject"}))["state"]
        == "rejected"
    )

    # An Admin may decide anyone's request, but never their own.
    mine = ok(
        await client.post(
            path, headers=w["h"], json={"approver_id": w["ids"]["adarsh"], "what": "Waive fee"}
        ),
        201,
    )
    own = await client.post(
        f"/api/v1/approvals/{mine['id']}/decide", headers=w["h"], json={"decision": "approve"}
    )
    assert own.status_code == 403


# --- Tasks (X8) ---


async def test_tasks_have_an_owner_a_due_time_and_a_done_line(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Call the courier")
    neha, chetan, adarsh = w["who"]["neha"], w["who"]["chetan"], w["who"]["adarsh"]

    task = ok(
        await client.post(
            f"{T}/{t['id']}/tasks",
            headers=neha,
            json={"text": "Ring BlueDart", "owner_id": w["ids"]["chetan"], "due_in": "1h"},
        ),
        201,
    )
    due = datetime.fromisoformat(task["due_at"])
    assert timedelta(minutes=59) < due - datetime.now(UTC) <= timedelta(hours=1)
    assert (task["owner_name"], task["overdue"], task["done_at"]) == ("Chetan", False, None)
    assert await notes_for(client, chetan, "task") == [
        f"Neha gave you a task on {t['label']}: Ring BlueDart"
    ]
    late = ok(
        await client.post(
            f"{T}/{t['id']}/tasks",
            headers=neha,
            json={"text": "Email the customer", "due_at": "2020-01-01T09:00:00+05:30"},
        ),
        201,
    )
    assert late["overdue"] is True
    mine = ok(await client.get("/api/v1/tasks/mine", headers=chetan))
    assert [x["text"] for x in mine] == ["Ring BlueDart"]

    # Someone else's task: an agent can't change it, a Supervisor can.
    r = await client.patch(f"/api/v1/tasks/{late['id']}", headers=chetan, json={"done": True})
    assert r.status_code == 403
    done = ok(
        await client.patch(f"/api/v1/tasks/{task['id']}", headers=chetan, json={"done": True})
    )
    assert done["done_by_name"] == "Chetan"
    assert done["overdue"] is False
    moved = ok(
        await client.patch(
            f"/api/v1/tasks/{late['id']}",
            headers=adarsh,
            json={"owner_id": w["ids"]["adarsh"], "due_at": None},
        )
    )
    assert (moved["owner_name"], moved["due_at"], moved["overdue"]) == ("Adarsh", None, False)

    page = lines(ok(await client.get(f"{T}/{t['id']}", headers=neha)))
    assert "Task for Chetan added by Neha: Ring BlueDart" in page
    assert "Task done by Chetan: Ring BlueDart" in page
    listed = ok(await client.get(f"{T}/{t['id']}/tasks", headers=w["who"]["lata"]))
    assert [x["text"] for x in listed] == ["Email the customer", "Ring BlueDart"]  # open first

    lata = w["who"]["lata"]
    assert (
        await client.post(f"{T}/{t['id']}/tasks", headers=lata, json={"text": "x"})
    ).status_code == 403
    assert (
        await client.post(
            f"{T}/{t['id']}/tasks", headers=neha, json={"text": "x", "owner_id": w["ids"]["lata"]}
        )
    ).status_code == 422
    assert (
        await client.post(
            f"{T}/{t['id']}/tasks",
            headers=neha,
            json={"text": "x", "due_at": "2026-11-13T09:00:00"},
        )
    ).status_code == 422


# --- Time log (X9) ---


async def test_the_time_log_adds_up_and_billable_is_counted_apart(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Bulk order setup")
    neha, chetan = w["who"]["neha"], w["who"]["chetan"]
    first = ok(
        await client.post(
            f"{T}/{t['id']}/time",
            headers=neha,
            json={"minutes": 30, "note": "Call", "billable": True},
        ),
        201,
    )
    ok(await client.post(f"{T}/{t['id']}/time", headers=chetan, json={"minutes": 15}), 201)
    log = ok(await client.get(f"{T}/{t['id']}/time", headers=w["who"]["lata"]))
    assert (log["total_minutes"], log["billable_minutes"]) == (45, 30)
    assert [e["member_name"] for e in log["entries"]] == ["Chetan", "Neha"]

    assert (
        await client.post(f"{T}/{t['id']}/time", headers=neha, json={"minutes": 0})
    ).status_code == 422
    assert (await client.delete(f"/api/v1/time/{first['id']}", headers=chetan)).status_code == 403
    assert (await client.delete(f"/api/v1/time/{first['id']}", headers=w["h"])).status_code == 204
    assert ok(await client.get(f"{T}/{t['id']}/time", headers=neha))["total_minutes"] == 15


# --- Tenancy ---


async def test_another_workspace_sees_none_of_it(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Private")
    other = bearer(
        await sign_in(client, await new_workspace(app_sessionmaker), "admin@example.com")
    )
    for path in ("related", "approvals", "tasks", "time"):
        assert (await client.get(f"{T}/{t['id']}/{path}", headers=other)).status_code == 404
    r = await client.post(f"{T}/{t['id']}/tasks", headers=other, json={"text": "x"})
    assert r.status_code == 404


async def test_a_rule_cannot_resolve_a_parent_but_can_resolve_a_child(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(
        await client.post(
            "/api/v1/rules",
            headers=w["h"],
            json={
                "name": "Thanks closes it",
                "event": "customer_replied",
                "conditions": [{"kind": "text_contains_any", "words": ["thanks"]}],
                "actions": [
                    {"kind": "set_status", "status": "resolved"},
                    {"kind": "set_priority", "priority": "low"},
                ],
            },
        ),
        201,
    )
    parent = await ticket(client, w, "Wrong colour")
    neha = w["who"]["neha"]
    child = ok(
        await client.post(
            f"{T}/{parent['id']}/children", headers=neha, json={"team_id": w["teams"]["Shipping"]}
        ),
        201,
    )

    p = ok(
        await client.post(
            f"{T}/{parent['id']}/simulate-customer-reply",
            headers=w["h"],
            json={"body": "ok thanks"},
        )
    )
    assert p["status"] == "open"
    assert p["priority"] == "low"  # the other steps still ran
    assert f"Status not changed: Resolve the child tickets first: {child['label']} still open." in (
        lines(p)
    )

    c = ok(
        await client.post(
            f"{T}/{child['id']}/simulate-customer-reply", headers=w["h"], json={"body": "thanks"}
        )
    )
    assert c["status"] == "resolved"
    p = ok(await client.get(f"{T}/{parent['id']}", headers=neha))
    assert f"Child ticket {child['label']} resolved by a rule" in lines(p)


async def test_merging_a_child_tells_its_parent(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    parent = await ticket(client, w, "Parcel lost")
    chetan = w["who"]["chetan"]
    child = ok(
        await client.post(
            f"{T}/{parent['id']}/children",
            headers=w["who"]["neha"],
            json={"team_id": w["teams"]["Shipping"]},
        ),
        201,
    )
    other = await ticket(client, w, "Parcel lost (courier claim)")
    ok(await client.post(f"{T}/{child['id']}/merge", headers=chetan, json={"into": other["id"]}))
    p = ok(await client.get(f"{T}/{parent['id']}", headers=chetan))
    assert f"Child ticket {child['label']} resolved by Chetan" in lines(p)
    ok(await client.post(f"{T}/{parent['id']}/resolve", headers=chetan))  # no open child left


async def test_merge_moves_open_tasks_and_waiting_approvals(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    keep = await ticket(client, w, "Refund not received")
    dup = await ticket(client, w, "Refund not received!!")
    neha = w["who"]["neha"]
    done = ok(await client.post(f"{T}/{dup['id']}/tasks", headers=neha, json={"text": "Old"}), 201)
    ok(await client.patch(f"/api/v1/tasks/{done['id']}", headers=neha, json={"done": True}))
    for text_ in ("Check the bank", "Call Priya"):
        ok(await client.post(f"{T}/{dup['id']}/tasks", headers=neha, json={"text": text_}), 201)
    ok(
        await client.post(
            f"{T}/{dup['id']}/approvals",
            headers=neha,
            json={"approver_id": w["ids"]["adarsh"], "what": "Refund ₹500"},
        ),
        201,
    )

    merged = ok(
        await client.post(f"{T}/{dup['id']}/merge", headers=neha, json={"into": keep["id"]})
    )
    assert f"Moved from {dup['label']}: 2 open tasks, 1 waiting approval" in lines(merged)
    moved = ok(await client.get(f"{T}/{keep['id']}/tasks", headers=neha))
    assert sorted(t["text"] for t in moved) == ["Call Priya", "Check the bank"]
    assert [
        a["what"] for a in ok(await client.get(f"{T}/{keep['id']}/approvals", headers=neha))
    ] == ["Refund ₹500"]
    # The done task stays on the merged ticket as history.
    assert [t["text"] for t in ok(await client.get(f"{T}/{dup['id']}/tasks", headers=neha))] == [
        "Old"
    ]
    waiting = ok(await client.get("/api/v1/approvals/waiting", headers=w["who"]["adarsh"]))
    assert [a["ticket_label"] for a in waiting] == [keep["label"]]


async def test_the_owner_hears_once_when_a_task_is_overdue(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Courier claim")
    neha, chetan = w["who"]["neha"], w["who"]["chetan"]
    task = ok(
        await client.post(
            f"{T}/{t['id']}/tasks",
            headers=neha,
            json={"text": "File the claim", "owner_id": w["ids"]["chetan"], "due_in": "1h"},
        ),
        201,
    )
    # A due time set in the past by hand: never alerted.
    ok(
        await client.post(
            f"{T}/{t['id']}/tasks",
            headers=neha,
            json={"text": "Late already", "due_at": "2020-01-01T09:00:00Z"},
        ),
        201,
    )
    now = datetime.now(UTC)

    assert await alert_workspace(app_sessionmaker, w["ws"], now) == 0  # not due yet
    assert await alert_workspace(app_sessionmaker, w["ws"], now + timedelta(hours=2)) == 1
    assert await alert_workspace(app_sessionmaker, w["ws"], now + timedelta(hours=3)) == 0
    assert await notes_for(client, chetan, "task_overdue") == [
        f"Task overdue on {t['label']}: File the claim"
    ]
    assert await notes_for(client, neha, "task_overdue") == []

    # A new due time, missed again: alerted again. A done task: never.
    ok(await client.patch(f"/api/v1/tasks/{task['id']}", headers=chetan, json={"due_in": "4h"}))
    assert await alert_workspace(app_sessionmaker, w["ws"], now + timedelta(hours=5)) == 1
    ok(await client.patch(f"/api/v1/tasks/{task['id']}", headers=chetan, json={"due_in": "4h"}))
    ok(await client.patch(f"/api/v1/tasks/{task['id']}", headers=chetan, json={"done": True}))
    assert await alert_workspace(app_sessionmaker, w["ws"], now + timedelta(hours=9)) == 0
    assert len(await notes_for(client, chetan, "task_overdue")) == 2
