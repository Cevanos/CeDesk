"""Macros (md W18) through the real API with RLS on: the mock's three macros give the
mock's results (End of Phase 2 check), all steps land in one transaction, light agents
and unaccepted handovers are refused, and the settings are Admin-only and per workspace."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.service.auth import resolve
from cedesk.helpdesk.service.tickets import NewTicket, create_ticket
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
M = "/api/v1/macros"
NOTE_X = [{"kind": "note", "text": "x"}]


def lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """A seeded workspace with Neha (agent, Billing) and Chetan (agent, Tech)."""
    slug = await new_workspace(sm, seed=True)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    chetan = await add(client, token, "chetan@example.com", "agent", "Chetan")
    teams = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/teams", headers=h))}
    types = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/ticket-types", headers=h))}
    ok(await client.patch(f"/api/v1/teams/{teams['Billing']}", headers=h,
                          json={"member_ids": [neha]}))  # fmt: skip
    ok(await client.patch(f"/api/v1/teams/{teams['Tech']}", headers=h,
                          json={"member_ids": [chetan]}))  # fmt: skip
    macros = {m["name"]: m["id"] for m in ok(await client.get(M, headers=h))}
    agent = bearer(await sign_in(client, slug, "neha@example.com"))
    return {"slug": slug, "token": token, "h": h, "agent": agent, "neha": neha,
            "chetan": chetan, "teams": teams, "types": types, "macros": macros,
            "ws": UUID(token.split(".")[0])}  # fmt: skip


async def refund_ticket(client: AsyncClient, w: dict[str, Any]) -> Any:
    """T-1001 from the mock: Priya's refund, in Billing, with Neha."""
    body = {
        "subject": "Damaged T-shirt",
        "contact": {"email": "priya@example.com", "name": "Priya Sharma"},
        "type_id": w["types"]["Refund"],
        "fields": {"order_id": "#1042", "amount": 1299},
        "assignee_id": w["neha"],
    }
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def run(client: AsyncClient, h: dict[str, str], ticket_id: str, macro_id: str) -> Any:
    return await client.post(f"{T}/{ticket_id}/macros/{macro_id}", headers=h)


async def test_the_mocks_three_macros_are_there_in_order(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    rows = ok(await client.get(M, headers=w["agent"]))  # agents see the menu too
    assert [(m["name"], m["position"], m["problems"]) for m in rows] == [
        ("Refund approved", 0, []),
        ("Ask for a photo", 1, []),
        ("Escalate to Tech", 2, []),
    ]
    assert [s["kind"] for s in rows[0]["steps"]] == [
        "reply", "add_tag", "set_priority", "note", "set_status",
    ]  # fmt: skip


async def test_refund_approved_does_all_five_steps_in_one_go(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await refund_ticket(client, w)
    t = ok(await run(client, w["agent"], t["id"], w["macros"]["Refund approved"]))

    assert (t["status"], t["priority"], t["tags"]) == ("resolved", "low", ["refund"])
    assert t["resolved_at"] is not None and t["first_reply_at"] is not None
    reply, note, line = t["messages"][-3:]
    assert reply["kind"] == "agent" and reply["author_name"] == "Neha"
    assert reply["body"] == (
        "Hi Priya, your refund of ₹1299 for order #1042 is approved. The money will reach "
        "your original payment method in 5–7 business days."  # noqa: RUF001 (the mock's dash)
    )
    assert note["kind"] == "note"
    assert note["body"] == "Refund approved. Check the payment reached the customer in 7 days."
    assert line["body"] == (
        "Macro “Refund approved” run by Neha: Reply sent · Tag refund · Priority Low · "
        "Note added · Status Resolved"
    )

    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        rows = await outbox.for_ticket(s, UUID(t["id"]))
    kinds = [r["payload"]["event"] for r in rows]
    assert kinds.count("message.created") == 2  # the reply and the note
    assert "ticket.resolved" in kinds


async def test_ask_for_a_photo_replies_and_waits_on_the_customer(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await refund_ticket(client, w)
    t = ok(await run(client, w["agent"], t["id"], w["macros"]["Ask for a photo"]))
    assert t["status"] == "pending"
    assert t["messages"][-2]["body"].startswith("Could you please share a photo")
    assert lines(t)[-1] == "Macro “Ask for a photo” run by Neha: Reply sent · Status Pending"

    # Then the customer answers and the default reply rule opens it again.
    r = await client.post(f"{T}/{t['id']}/simulate-customer-reply", headers=w["h"],
                          json={"body": "Here is the photo"})  # fmt: skip
    assert ok(r)["status"] == "open"


async def test_escalate_to_tech_moves_and_reroutes_the_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await refund_ticket(client, w)  # Neha's own ticket
    t = ok(await run(client, w["agent"], t["id"], w["macros"]["Escalate to Tech"]))
    assert (t["team_name"], t["priority"], t["assignee_id"]) == ("Tech", "high", w["chetan"])
    assert lines(t)[-2:] == [
        "Macro “Escalate to Tech” run by Neha: Team Tech · Priority High · Note added",
        next(b for b in lines(t) if b.startswith("Auto-assigned to Chetan")),
    ]
    chetan = bearer(await sign_in(client, w["slug"], "chetan@example.com"))
    notes = ok(await client.get("/api/v1/notifications", headers=chetan))["notifications"]
    assert notes[0]["kind"] == "assigned"


async def test_an_agent_cant_take_someone_elses_ticket_with_a_macro(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    body = {"subject": "Bug", "contact": {"email": "a@b.co", "name": "A"},
            "assignee_id": w["chetan"]}  # fmt: skip
    t = ok(await client.post(T, headers=w["h"], json=body), 201)
    team_ticket = ok(await client.patch(f"{T}/{t['id']}", headers=w["h"],
                                        json={"team_id": w["teams"]["Billing"],
                                              "assignee_id": w["chetan"]}))  # fmt: skip
    r = await run(client, w["agent"], team_ticket["id"], w["macros"]["Escalate to Tech"])
    assert r.status_code == 403
    # Nothing from the macro was saved, not even its note.
    after = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert after["messages"] == team_ticket["messages"]
    assert (after["team_name"], after["priority"]) == ("Billing", "medium")


async def test_light_agents_cant_run_macros(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await add(client, w["token"], "light@example.com", "light_agent")
    light = bearer(await sign_in(client, w["slug"], "light@example.com"))
    t = await refund_ticket(client, w)
    assert len(ok(await client.get(M, headers=light))) == 3
    r = await run(client, light, t["id"], w["macros"]["Ask for a photo"])
    assert r.status_code == 403


async def test_a_handover_must_be_accepted_first(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    caller = await resolve(app_sessionmaker, w["token"])
    view = await create_ticket(app_sessionmaker, caller, NewTicket(
        subject="Handover", contact_email="a@b.co", contact_name="A", accepted=False,
        conversation_id="conv-1",
    ))  # fmt: skip
    r = await run(client, w["h"], str(view.ticket["id"]), w["macros"]["Ask for a photo"])
    assert r.status_code == 409
    assert r.json()["error"]["message"] == "Accept the handover first."


async def test_if_match_refuses_a_stale_run(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    r = await client.post(T, headers=w["h"], json={
        "subject": "Hi", "contact": {"email": "a@b.co", "name": "A"},
    })  # fmt: skip
    etag, t = r.headers["ETag"], ok(r, 201)
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"priority": "low"}))
    url = f"{T}/{t['id']}/macros/{w['macros']['Ask for a photo']}"
    r = await client.post(url, headers=w["h"] | {"If-Match": etag})
    assert r.status_code == 409
    fresh = (await client.get(f"{T}/{t['id']}", headers=w["h"])).headers["ETag"]
    r = await client.post(url, headers=w["h"] | {"If-Match": fresh})
    assert r.status_code == 200 and r.headers["ETag"] != fresh


async def test_missing_ticket_or_macro_is_404(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await refund_ticket(client, w)
    zero = "00000000-0000-0000-0000-000000000000"
    assert (await run(client, w["h"], zero, w["macros"]["Ask for a photo"])).status_code == 404
    assert (await run(client, w["h"], t["id"], zero)).status_code == 404


# Settings


async def test_an_admin_adds_changes_reorders_and_deletes_macros(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["h"]
    m = ok(await client.post(M, headers=h, json={"name": "  Bulk   order ", "steps": [
        {"kind": "reply", "text": "  Hi {{customer.name}}, 12% off 40+.  "},
        {"kind": "add_tag", "tag": "Bulk Order"},
    ]}), 201)  # fmt: skip
    assert (m["name"], m["position"]) == ("Bulk order", 3)
    assert m["steps"] == [
        {"kind": "reply", "text": "Hi {{customer.name}}, 12% off 40+."},
        {"kind": "add_tag", "tag": "bulk-order"},
    ]
    t = await refund_ticket(client, w)
    t = ok(await run(client, w["agent"], t["id"], m["id"]))
    assert t["messages"][-2]["body"] == "Hi Priya Sharma, 12% off 40+."
    assert t["tags"] == ["bulk-order"]

    m = ok(await client.patch(f"{M}/{m['id']}", headers=h, json={"name": "Bulk"}))
    assert m["name"] == "Bulk" and len(m["steps"]) == 2
    m = ok(await client.patch(f"{M}/{m['id']}", headers=h, json={"steps": NOTE_X}))
    assert (m["name"], m["steps"]) == ("Bulk", NOTE_X)

    ids = [x["id"] for x in ok(await client.get(M, headers=h))]
    order = ok(await client.put(f"{M}/order", headers=h, json={"ids": ids[::-1]}))
    assert [x["id"] for x in order] == ids[::-1]
    assert (await client.put(f"{M}/order", headers=h, json={"ids": ids[1:]})).status_code == 422

    assert (await client.delete(f"{M}/{m['id']}", headers=h)).status_code == 204
    assert (await client.delete(f"{M}/{m['id']}", headers=h)).status_code == 404
    assert len(ok(await client.get(M, headers=h))) == 3


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [{"kind": "send_email"}],
        [{"kind": "reply", "text": "  "}],
        [{"kind": "reply", "text": "x" * 10_001}],
        [{"kind": "reply", "text": "a"}, {"kind": "reply", "text": "b"}],
        [{"kind": "set_status", "status": "closed"}],
        [{"kind": "set_type", "type_id": "00000000-0000-0000-0000-000000000000"}],
        [{"kind": "set_team", "team_id": "00000000-0000-0000-0000-000000000000"}],
        ["reply"],
        NOTE_X * 21,
    ],
)
async def test_a_bad_macro_is_refused(
    client: AsyncClient, app_sessionmaker: Sm, steps: list[Any]
) -> None:
    w = await desk(client, app_sessionmaker)
    r = await client.post(M, headers=w["h"], json={"name": "Bad", "steps": steps})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "Invalid"


async def test_macro_names_are_unique(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    r = await client.post(M, headers=w["h"], json={"name": "Refund approved", "steps": NOTE_X})
    assert r.status_code == 409
    mine = ok(await client.post(M, headers=w["h"], json={"name": "Mine", "steps": NOTE_X}), 201)
    r = await client.patch(f"{M}/{mine['id']}", headers=w["h"], json={"name": "Ask for a photo"})
    assert r.status_code == 409


async def test_a_deleted_team_shows_as_a_problem_and_the_rest_still_runs(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["h"]
    assert (
        await client.delete(f"/api/v1/teams/{w['teams']['Tech']}", headers=h)
    ).status_code == 204
    escalate = next(m for m in ok(await client.get(M, headers=h)) if m["name"].startswith("Esc"))
    assert escalate["problems"] == ["Its team was deleted."]
    t = await refund_ticket(client, w)
    t = ok(await run(client, w["agent"], t["id"], escalate["id"]))
    assert lines(t)[-1] == "Macro “Escalate to Tech” run by Neha: Priority High · Note added"
    assert t["team_name"] is None


async def test_only_admins_change_macros(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await add(client, w["token"], "sup@example.com", "supervisor")
    macro_id = w["macros"]["Refund approved"]
    for h in [w["agent"], bearer(await sign_in(client, w["slug"], "sup@example.com"))]:
        for r in [
            await client.post(M, headers=h, json={"name": "x", "steps": NOTE_X}),
            await client.patch(f"{M}/{macro_id}", headers=h, json={"name": "y"}),
            await client.put(f"{M}/order", headers=h, json={"ids": [macro_id]}),
            await client.delete(f"{M}/{macro_id}", headers=h),
        ]:
            assert r.status_code == 403, r.text
    assert (await client.get(M)).status_code == 401


async def test_one_workspace_never_sees_or_runs_anothers_macros(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    first = await desk(client, app_sessionmaker)
    slug = await new_workspace(app_sessionmaker)  # no defaults
    other = bearer(await sign_in(client, slug, "admin@example.com"))
    assert ok(await client.get(M, headers=other)) == []
    theirs = first["macros"]["Ask for a photo"]
    assert (
        await client.patch(f"{M}/{theirs}", headers=other, json={"name": "x"})
    ).status_code == 404
    t = ok(await client.post(T, headers=other, json={
        "subject": "Hi", "contact": {"email": "a@b.co", "name": "A"},
    }), 201)  # fmt: skip
    assert (await run(client, other, t["id"], theirs)).status_code == 404


async def test_the_macro_line_lists_only_real_changes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    body = {"subject": "App problem", "description": "white screen error",
            "contact": {"email": "a@b.co", "name": "A"}}  # fmt: skip
    t = ok(await client.post(T, headers=w["h"], json=body), 201)
    assert t["team_name"] == "Tech"  # the errors rule put it there
    t = ok(await run(client, w["h"], t["id"], w["macros"]["Escalate to Tech"]))
    assert lines(t)[-1] == "Macro “Escalate to Tech” run by Admin: Priority High · Note added"
    assert t["assignee_id"] == w["chetan"]  # not routed again: the team did not change
