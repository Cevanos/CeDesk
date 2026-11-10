"""Automation rules (md W15-W17) through the real API with RLS on: the mock's six rules
give the mock's results (End of Phase 2 check), rules run before auto-assignment and never
on agent edits, customer replies reopen or stay resolved on a thank-you, and the rule
settings are Admin-only and per workspace."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.service.auth import resolve
from cedesk.helpdesk.service.tickets import NewTicket, create_ticket
from cedesk.settings import Env, Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
RULES = "/api/v1/rules"
IS_OPEN = [{"kind": "status_in", "statuses": ["open"]}]
TAG_X = [{"kind": "add_tag", "tag": "x"}]


def lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


def rule_lines(t: dict[str, Any]) -> list[str]:
    return [b for b in lines(t) if b.startswith("Rule applied")]


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """A seeded workspace (the mock's teams, types, replies and rules) with Neha in
    Billing and Chetan in Tech, both Available."""
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
    return {"slug": slug, "token": token, "h": h, "neha": neha, "chetan": chetan,
            "teams": teams, "types": types, "ws": UUID(token.split(".")[0])}  # fmt: skip


async def new(client: AsyncClient, h: dict[str, str], subject: str, **extra: Any) -> Any:
    body = {"subject": subject, "contact": {"email": "priya@example.com", "name": "Priya Sharma"}}
    return ok(await client.post(T, headers=h, json=body | extra), 201)


async def by_service(sm: Sm, token: str, new_ticket: NewTicket) -> Any:
    """For what the agent API does not take (the address written to, sentiment): the
    connector and email call the service like this."""
    view = await create_ticket(sm, await resolve(sm, token), new_ticket)
    return view.ticket, [m["body"] for m in view.messages if m["kind"] == "system"]


# The mock's six rules


async def test_the_mocks_six_rules_are_there_in_order(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    rules = ok(await client.get(RULES, headers=w["h"]))
    assert [(r["name"], r["event"], r["enabled"], r["position"]) for r in rules] == [
        ("Refund requests to Billing", "created", True, 0),
        ("Order tracking to Shipping", "created", True, 1),
        ("billing@ emails to Billing", "created", True, 2),
        ("App errors to Tech", "created", True, 3),
        ("Reopen when the customer replies", "customer_replied", True, 4),
        ("Upset customer goes first", "created", True, 5),
    ]
    assert all(r["problems"] == [] for r in rules)


async def test_rule_1_refund_goes_to_billing_and_is_auto_assigned(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund for damaged T-shirt (order #1042)",
                  description="It arrived torn at the seam.")  # fmt: skip
    assert (t["type_name"], t["team_name"], t["priority"]) == ("Refund", "Billing", "high")
    assert rule_lines(t) == [
        "Rule applied: Refund requests to Billing → Type Refund · Team Billing · Priority High"
    ]
    # Rules run first, then routing picks someone in the team the rule chose.
    assert t["assignee_id"] == w["neha"]
    order = lines(t)
    assert order.index(rule_lines(t)[0]) < next(
        i for i, b in enumerate(order) if b.startswith("Auto-assigned")
    )
    # The new type's fields are there to fill.
    assert t["fields"] == {}


async def test_rule_2_tracking_goes_to_shipping(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Where is my order #1077?")
    assert (t["type_name"], t["team_name"], t["priority"]) == (
        "Shipping issue", "Shipping", "medium",
    )  # fmt: skip
    t = await new(client, w["h"], "Delivery late", description="My TRACKING number is dead")
    assert t["team_name"] == "Shipping"


async def test_rule_3_billing_emails_go_to_billing(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ticket, sys = await by_service(app_sessionmaker, w["token"], NewTicket(
        subject="Invoice copy for October bulk order", contact_email="rohan@guptatraders.co",
        contact_name="Rohan Gupta", channel=Channel.email, to_address="billing@acme-store.in",
    ))  # fmt: skip
    assert (ticket["type_name"], ticket["team_name"]) == ("Billing question", "Billing")
    assert "Rule applied: billing@ emails to Billing → Team Billing · Type Billing question" in sys

    # Not by email, or to another address: no match.
    ticket, sys = await by_service(app_sessionmaker, w["token"], NewTicket(
        subject="Invoice copy", contact_email="a@b.co", contact_name="A",
        channel=Channel.email, to_address="support@acme-store.in",
    ))  # fmt: skip
    assert ticket["team_name"] is None and not any(s.startswith("Rule") for s in sys)


async def test_rule_3_keeps_a_type_an_earlier_rule_set(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ticket, _ = await by_service(app_sessionmaker, w["token"], NewTicket(
        subject="Refund for invoice 77", contact_email="a@b.co", contact_name="A",
        channel=Channel.email, to_address="billing@acme-store.in",
    ))  # fmt: skip
    assert (ticket["type_name"], ticket["team_name"]) == ("Refund", "Billing")


async def test_rule_4_errors_go_to_tech(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "App problem on the payment page",
                  description="White screen ERROR after Pay")  # fmt: skip
    assert (t["type_name"], t["team_name"], t["assignee_id"]) == (
        "Technical problem", "Tech", w["chetan"],
    )  # fmt: skip


async def test_rule_6_an_upset_customer_goes_first_but_urgent_stays_urgent(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ticket, sys = await by_service(app_sessionmaker, w["token"], NewTicket(
        subject="Very bad service", contact_email="a@b.co", contact_name="A",
        sentiment=Sentiment.negative,
    ))  # fmt: skip
    assert (ticket["priority"], list(ticket["tags"])) == ("high", ["upset"])
    assert "Rule applied: Upset customer goes first → Priority High · Tag upset" in sys

    ticket, _ = await by_service(app_sessionmaker, w["token"], NewTicket(
        subject="Very bad service", contact_email="a@b.co", contact_name="A",
        sentiment=Sentiment.negative, priority=Priority.urgent,
    ))  # fmt: skip
    assert (ticket["priority"], list(ticket["tags"])) == ("urgent", ["upset"])


async def test_two_rules_can_match_and_the_later_one_wins(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund please", description="the app crashes on Pay")
    assert [line.split(" → ")[0] for line in rule_lines(t)] == [
        "Rule applied: Refund requests to Billing",
        "Rule applied: App errors to Tech",
    ]
    assert (t["type_name"], t["team_name"], t["priority"]) == ("Technical problem", "Tech", "high")
    assert t["assignee_id"] == w["chetan"]


async def test_no_rule_matches_an_ordinary_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Change my delivery address")
    assert rule_lines(t) == []
    assert (t["type_name"], t["team_name"], t["priority"]) == (None, None, "medium")


async def test_agent_edits_never_run_rules(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Change my delivery address")
    t = ok(await client.patch(f"{T}/{t['id']}", headers=w["h"],
                              json={"subject": "Refund now", "priority": "low"}))  # fmt: skip
    assert rule_lines(t) == []
    assert (t["priority"], t["team_name"]) == ("low", None)


async def test_a_chosen_assignee_is_kept_when_a_rule_picks_the_team(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund", assignee_id=w["chetan"])
    assert (t["team_name"], t["assignee_id"]) == ("Billing", w["chetan"])
    assert not any(b.startswith("Auto-assigned") for b in lines(t))


async def test_a_new_ticket_announces_its_final_state_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        rows = await outbox.for_ticket(s, UUID(t["id"]))
    kinds = [r["payload"]["event"] for r in rows]
    assert kinds.count("ticket.created") == 1 and "ticket.updated" not in kinds
    created = next(r["payload"] for r in rows if r["payload"]["event"] == "ticket.created")
    assert (created["ticket"]["team"], created["ticket"]["priority"]) == ("Billing", "high")


# Customer replies: reopen (W16) and thank-you (W17)


async def reply(client: AsyncClient, h: dict[str, str], ticket_id: str, body: str) -> Any:
    r = await client.post(f"{T}/{ticket_id}/simulate-customer-reply", headers=h,
                          json={"body": body})  # fmt: skip
    return ok(r)


async def notes_for(client: AsyncClient, w: dict[str, Any], email: str) -> list[dict[str, Any]]:
    token = await sign_in(client, w["slug"], email)
    return ok(await client.get("/api/v1/notifications", headers=bearer(token)))["notifications"]


async def test_a_reply_on_pending_reopens_it_and_tells_the_assignee(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"status": "pending"}))
    t = await reply(client, w["h"], t["id"], "Here is the photo you asked for.")
    assert t["status"] == "open"
    assert t["messages"][-2]["kind"] == "customer"
    assert t["messages"][-2]["author_name"] == "Priya Sharma"
    assert rule_lines(t)[-1] == "Rule applied: Reopen when the customer replies → Status Open"
    assert t["reopen_count"] == 0  # Pending to Open is not a reopen
    notes = await notes_for(client, w, "neha@example.com")
    assert notes[0]["kind"] == "customer_reply"
    assert notes[0]["text"] == f"Priya Sharma replied on {t['label']}: Refund"


async def test_a_reply_on_an_open_ticket_only_adds_the_message(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    before = len(lines(t))
    t = await reply(client, w["h"], t["id"], "Any news?")
    assert t["status"] == "open" and len(lines(t)) == before
    assert (await notes_for(client, w, "neha@example.com"))[0]["kind"] == "customer_reply"


async def test_a_thank_you_keeps_a_resolved_ticket_resolved(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["h"]))
    unread = len(await notes_for(client, w, "neha@example.com"))
    t = await reply(client, w["h"], t["id"], "Thanks!")
    assert (t["status"], t["reopen_count"]) == ("resolved", 0)
    assert lines(t)[-1] == "Thank-you detected · ticket stays resolved"
    assert len(await notes_for(client, w, "neha@example.com")) == unread  # nobody bothered


async def test_any_other_reply_reopens_a_resolved_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["h"]))
    t = await reply(client, w["h"], t["id"], "Thanks, but the money has not arrived.")
    assert (t["status"], t["reopen_count"], t["resolved_at"]) == ("open", 1, None)
    assert lines(t)[-1] == "Customer replied · ticket reopened"
    assert (await notes_for(client, w, "neha@example.com"))[0]["kind"] == "customer_reply"
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        rows = await outbox.for_ticket(s, UUID(t["id"]))
    tail = [r["payload"]["event"] for r in rows][-2:]
    assert tail == ["message.created", "ticket.updated"]


async def test_a_switched_off_reopen_rule_leaves_pending_alone(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    reopen = next(
        r for r in ok(await client.get(RULES, headers=w["h"])) if r["event"] == "customer_replied"
    )
    ok(await client.patch(f"{RULES}/{reopen['id']}", headers=w["h"], json={"enabled": False}))
    t = await new(client, w["h"], "Refund")
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"status": "pending"}))
    t = await reply(client, w["h"], t["id"], "Here you go")
    assert t["status"] == "pending"


async def test_a_reply_rule_that_moves_the_team_routes_the_ticket_again(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.post(RULES, headers=w["h"], json={
        "name": "Refund talk goes to Billing", "event": "customer_replied",
        "conditions": [{"kind": "text_contains_any", "words": ["money back"]}],
        "actions": [{"kind": "set_team", "team_id": w["teams"]["Billing"]}],
    }), 201)  # fmt: skip
    t = await new(client, w["h"], "App crash on Pay")
    assert (t["team_name"], t["assignee_id"]) == ("Tech", w["chetan"])
    t = await reply(client, w["h"], t["id"], "Forget it, I want my money back")
    assert (t["team_name"], t["assignee_id"]) == ("Billing", w["neha"])
    assert any(b.startswith("Auto-assigned to Neha") for b in lines(t))


async def test_simulate_is_admin_only_and_checks_its_input(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    agent = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    url = f"{T}/{t['id']}/simulate-customer-reply"
    assert (await client.post(url, headers=agent, json={"body": "x"})).status_code == 403
    assert (await client.post(url, headers=w["h"], json={"body": "  "})).status_code == 422
    missing = f"{T}/00000000-0000-0000-0000-000000000000/simulate-customer-reply"
    assert (await client.post(missing, headers=w["h"], json={"body": "x"})).status_code == 404


async def test_simulate_is_off_in_production(app_sessionmaker: Sm) -> None:
    from httpx import ASGITransport

    from cedesk.main import create_app

    url = "postgresql+asyncpg://cedesk_app:x@db/cedesk"
    settings = Settings(env=Env.production, secret_key="p" * 40, sign_in_limit=100_000,
                        database_url=url)  # fmt: skip
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        w = await desk(c, app_sessionmaker)
        t = await new(c, w["h"], "Refund")
        url = f"{T}/{t['id']}/simulate-customer-reply"
        r = await c.post(url, headers=w["h"], json={"body": "x"})
        assert r.status_code == 404
        # A non-Admin gets the same 404, so the route does not show it exists.
        neha = bearer(await sign_in(c, w["slug"], "neha@example.com"))
        assert (await c.post(url, headers=neha, json={"body": "x"})).status_code == 404


# Rule settings


async def test_an_admin_adds_changes_reorders_and_deletes_rules(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["h"]
    vip = ok(await client.post(RULES, headers=h, json={
        "name": "  VIP   words ", "event": "created",
        "conditions": [{"kind": "text_contains_any", "words": ["  Lawyer ", "lawyer", "COURT"]}],
        "actions": [{"kind": "set_priority", "priority": "urgent"},
                    {"kind": "add_tag", "tag": "Legal Risk"}],
    }), 201)  # fmt: skip
    assert (vip["name"], vip["position"], vip["enabled"]) == ("VIP words", 6, True)
    assert vip["conditions"] == [{"kind": "text_contains_any", "words": ["lawyer", "court"]}]
    assert vip["actions"][1] == {"kind": "add_tag", "tag": "legal-risk"}

    t = await new(client, h, "I will go to court")
    assert (t["priority"], t["tags"]) == ("urgent", ["legal-risk"])

    vip = ok(await client.patch(f"{RULES}/{vip['id']}", headers=h, json={"enabled": False}))
    assert vip["enabled"] is False and vip["name"] == "VIP words"
    assert (await new(client, h, "court"))["priority"] == "medium"

    ids = [r["id"] for r in ok(await client.get(RULES, headers=h))]
    order = ok(await client.put(f"{RULES}/order", headers=h, json={"ids": ids[::-1]}))
    assert [r["id"] for r in order] == ids[::-1]
    assert [r["position"] for r in order] == list(range(7))
    r = await client.put(f"{RULES}/order", headers=h, json={"ids": ids[1:]})
    assert r.status_code == 422

    assert (await client.delete(f"{RULES}/{vip['id']}", headers=h)).status_code == 204
    assert (await client.delete(f"{RULES}/{vip['id']}", headers=h)).status_code == 404
    assert len(ok(await client.get(RULES, headers=h))) == 6


async def test_rule_order_decides_who_wins(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["h"]
    ids = {r["name"]: r["id"] for r in ok(await client.get(RULES, headers=h))}
    order = list(ids.values())
    refund, errors = ids["Refund requests to Billing"], ids["App errors to Tech"]
    i, j = order.index(refund), order.index(errors)
    order[i], order[j] = order[j], order[i]
    ok(await client.put(f"{RULES}/order", headers=h, json={"ids": order}))
    t = await new(client, h, "Refund please", description="the app crashes")
    assert t["team_name"] == "Billing"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "event": "updated", "conditions": IS_OPEN,
         "actions": TAG_X},
        {"name": "x", "event": "created", "conditions": [],
         "actions": TAG_X},
        {"name": "x", "event": "created", "conditions": [{"kind": "colour_is"}],
         "actions": TAG_X},
        {"name": "x", "event": "created", "conditions": ["refund"],
         "actions": TAG_X},
        {"name": "x", "event": "created", "conditions": IS_OPEN,
         "actions": [{"kind": "set_team", "team_id": "00000000-0000-0000-0000-000000000000"}]},
        {"name": " ", "event": "created", "conditions": IS_OPEN,
         "actions": TAG_X},
    ],
)  # fmt: skip
async def test_a_bad_rule_is_refused(
    client: AsyncClient, app_sessionmaker: Sm, body: dict[str, Any]
) -> None:
    w = await desk(client, app_sessionmaker)
    r = await client.post(RULES, headers=w["h"], json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "Invalid"


async def test_rule_names_are_unique(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    body = {"name": "App errors to Tech", "event": "created",
            "conditions": IS_OPEN,
            "actions": TAG_X}  # fmt: skip
    r = await client.post(RULES, headers=w["h"], json=body)
    assert r.status_code == 409
    mine = ok(await client.post(RULES, headers=w["h"], json=body | {"name": "Mine"}), 201)
    r = await client.patch(f"{RULES}/{mine['id']}", headers=w["h"],
                           json={"name": "App errors to Tech"})  # fmt: skip
    assert r.status_code == 409


async def test_a_deleted_team_shows_as_a_problem_and_the_rest_still_runs(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["h"]
    assert (
        await client.delete(f"/api/v1/teams/{w['teams']['Billing']}", headers=h)
    ).status_code == 204
    refund = next(
        r for r in ok(await client.get(RULES, headers=h)) if r["name"].startswith("Refund")
    )
    assert refund["problems"] == ["Its team was deleted."]
    t = await new(client, h, "Refund")
    assert rule_lines(t) == [
        "Rule applied: Refund requests to Billing → Type Refund · Priority High"
    ]
    assert t["team_name"] is None
    # Fixing the action clears the problem; a changed rule can't point at a missing team.
    gone = refund["actions"][1]["team_id"]
    actions = [a for a in refund["actions"] if a["kind"] != "set_team"]
    r = await client.patch(f"{RULES}/{refund['id']}", headers=h,
                           json={"actions": [*actions, {"kind": "set_team", "team_id": gone}]},
                           )  # fmt: skip
    assert r.status_code == 422
    fixed = ok(await client.patch(f"{RULES}/{refund['id']}", headers=h, json={"actions": actions}))
    assert fixed["problems"] == []


async def test_only_admins_see_or_change_rules(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await add(client, w["token"], "sup@example.com", "supervisor")
    rule_id = ok(await client.get(RULES, headers=w["h"]))[0]["id"]
    for email in ["sup@example.com", "neha@example.com"]:
        h = bearer(await sign_in(client, w["slug"], email))
        for r in [
            await client.get(RULES, headers=h),
            await client.post(RULES, headers=h, json={}),
            await client.patch(f"{RULES}/{rule_id}", headers=h, json={"enabled": False}),
            await client.put(f"{RULES}/order", headers=h, json={"ids": [rule_id]}),
            await client.delete(f"{RULES}/{rule_id}", headers=h),
        ]:
            assert r.status_code in (403, 422), r.text
            if r.status_code == 422:  # the body failed first; the same call with a body is 403
                assert r.request.method == "POST"
    h = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    full = {"name": "x", "event": "created", "conditions": IS_OPEN,
            "actions": TAG_X}  # fmt: skip
    assert (await client.post(RULES, headers=h, json=full)).status_code == 403


async def test_one_workspace_never_sees_or_runs_anothers_rules(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    first = await desk(client, app_sessionmaker)
    slug = await new_workspace(app_sessionmaker)  # no defaults
    other = bearer(await sign_in(client, slug, "admin@example.com"))
    assert ok(await client.get(RULES, headers=other)) == []
    theirs = ok(await client.get(RULES, headers=first["h"]))[0]["id"]
    assert (
        await client.patch(f"{RULES}/{theirs}", headers=other, json={"enabled": False})
    ).status_code == 404
    assert (await client.delete(f"{RULES}/{theirs}", headers=other)).status_code == 404
    t = await new(client, other, "Refund")
    assert rule_lines(t) == [] and t["team_name"] is None


async def test_a_reply_rule_reads_only_the_new_message(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.post(RULES, headers=w["h"], json={
        "name": "Legal words", "event": "customer_replied",
        "conditions": [{"kind": "text_contains_any", "words": ["lawyer"]}],
        "actions": [{"kind": "add_tag", "tag": "legal"}],
    }), 201)  # fmt: skip
    t = await new(client, w["h"], "Late parcel")
    t = await reply(client, w["h"], t["id"], "I will call my lawyer")
    assert t["tags"] == ["legal"]
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"remove_tags": ["legal"]}))
    # The word is in the history, but not in this reply: the rule does not run again.
    t = await reply(client, w["h"], t["id"], "ok, waiting")
    assert t["tags"] == []
    assert sum(b.startswith("Rule applied: Legal words") for b in lines(t)) == 1


async def test_the_same_channel_message_is_saved_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    from cedesk.helpdesk.service.customer_replies import Outcome, customer_reply

    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["h"]))
    ticket_id = UUID(t["id"])
    first = await customer_reply(app_sessionmaker, w["ws"], ticket_id, "Still no money",
                                 channel_message_id="wamid.1")  # fmt: skip
    again = await customer_reply(app_sessionmaker, w["ws"], ticket_id, "Still no money",
                                 channel_message_id="wamid.1")  # fmt: skip
    assert first.outcome is Outcome.reopened
    assert (again.outcome, again.message_id) == (Outcome.duplicate, first.message_id)
    t = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert t["reopen_count"] == 1
    assert sum(m["kind"] == "customer" for m in t["messages"]) == 1
    notes = await notes_for(client, w, "neha@example.com")
    assert sum(n["kind"] == "customer_reply" for n in notes) == 1
    # Another message id is a new message.
    other = await customer_reply(app_sessionmaker, w["ws"], ticket_id, "Hello?",
                                 channel_message_id="wamid.2")  # fmt: skip
    assert other.outcome is Outcome.added


async def test_a_thank_you_in_hindi_keeps_it_resolved(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["h"]))
    for body in ["Thank you so much!", "Dhanyavaad 🙏"]:
        t = await reply(client, w["h"], t["id"], body)
        assert (t["status"], t["reopen_count"]) == ("resolved", 0), body


async def test_a_reply_rule_that_reroutes_sends_one_notification(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.post(RULES, headers=w["h"], json={
        "name": "Bugs go to Tech", "event": "customer_replied",
        "conditions": [{"kind": "text_contains_any", "words": ["bug"]}],
        "actions": [{"kind": "set_team", "team_id": w["teams"]["Tech"]}],
    }), 201)  # fmt: skip
    t = await new(client, w["h"], "Refund")  # Billing, Neha
    t = await reply(client, w["h"], t["id"], "Also the app has a bug")
    assert t["assignee_id"] == w["chetan"]
    chetan = await notes_for(client, w, "chetan@example.com")
    assert len(chetan) == 1
    assert chetan[0]["kind"] == "assigned"
    assert chetan[0]["text"] == (
        f"Priya Sharma replied on {t['label']}, and it was auto-assigned to you: Refund"
    )
    neha = await notes_for(client, w, "neha@example.com")
    assert not any(n["kind"] == "customer_reply" for n in neha)


async def test_when_nobody_can_take_it_the_old_assignee_hears_the_reply(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.post(RULES, headers=w["h"], json={
        "name": "Parcels go to Shipping", "event": "customer_replied",
        "conditions": [{"kind": "text_contains_any", "words": ["parcel"]}],
        "actions": [{"kind": "set_team", "team_id": w["teams"]["Shipping"]}],
    }), 201)  # fmt: skip
    t = await new(client, w["h"], "Refund")  # Billing, Neha; Shipping has nobody
    t = await reply(client, w["h"], t["id"], "Where is the parcel?")
    assert (t["team_name"], t["assignee_id"]) == ("Shipping", None)
    neha = await notes_for(client, w, "neha@example.com")
    assert neha[0]["kind"] == "customer_reply"


@pytest.mark.parametrize("channel_id", ["", "x" * 201])
async def test_a_bad_channel_message_id_is_refused(
    client: AsyncClient, app_sessionmaker: Sm, channel_id: str
) -> None:
    from cedesk.helpdesk.service.customer_replies import customer_reply
    from cedesk.helpdesk.service.errors import Invalid

    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund")
    with pytest.raises(Invalid):
        await customer_reply(app_sessionmaker, w["ws"], UUID(t["id"]), "hi",
                             channel_message_id=channel_id)  # fmt: skip


async def test_a_rule_that_no_longer_reads_still_lists(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    from sqlalchemy import text

    w = await desk(client, app_sessionmaker)
    rule_id = ok(await client.get(RULES, headers=w["h"]))[0]["id"]
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(
            text('UPDATE hd_rule SET conditions = \'[{"kind": "gone"}]\' WHERE id = :id'),
            {"id": rule_id},
        )
    rules = ok(await client.get(RULES, headers=w["h"]))
    broken = next(r for r in rules if r["id"] == rule_id)
    assert broken["problems"] == ["Its conditions or actions no longer read; save it again."]
    # New tickets still work: the broken rule is skipped.
    assert (await new(client, w["h"], "Where is my order?"))["team_name"] == "Shipping"
    # And the Admin can fix it.
    fixed = ok(await client.patch(f"{RULES}/{rule_id}", headers=w["h"],
                                  json={"conditions": [{"kind": "text_contains_any",
                                                        "words": ["refund"]}]}))  # fmt: skip
    assert fixed["problems"] == []


async def test_a_rule_with_nothing_to_change_writes_no_line(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w["h"], "Refund please", type_id=w["types"]["Refund"],
                  team_id=w["teams"]["Billing"], priority="high")  # fmt: skip
    assert rule_lines(t) == []
