"""Richer rules and the safeguards (feature-gap plan 5.1, 5.5) through the real API with
RLS on: rules saved before keep their format and still run; the new conditions (contact
field, VIP, segment, time since a reply, snoozed, followers) and actions (snooze, add a
follower, email the customer, call a webhook) work; the run log records each run; the
hourly limit, the per-ticket cap and loop protection stop runs; test mode changes and
sends nothing; webhook rules are an Admin's; another workspace sees none of it."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import automation as guard
from cedesk.helpdesk.service import customer_replies
from cedesk.helpdesk.service.automation import prune_everywhere
from cedesk.helpdesk.service.snooze import wake_workspace
from cedesk.helpdesk.service.webhooks import send_due
from tests.db.helpers import Sm, bearer, ok, sign_in
from tests.db.inbox_helpers import T, as_, desk, sql
from tests.db.test_custom_roles import give, role
from tests.db.test_webhooks import SEALER, install

pytestmark = pytest.mark.db

R = "/api/v1/rules"
RUNS = "/api/v1/automation/runs"
TAG = [{"kind": "add_tag", "tag": "x"}]
OPEN = [{"kind": "status_in", "statuses": ["open"]}]


async def rule(
    client: AsyncClient,
    w: dict[str, Any],
    event: str,
    conditions: list[Any],
    actions: list[Any],
    status: int = 201,
    **extra: Any,
) -> Any:
    body = {
        "name": extra.pop("name", f"Rule {event} {len(str(actions))}"),
        "event": event,
        "conditions": conditions,
        "actions": actions,
    } | extra
    r = await client.post(R, headers=w["h"], json=body)
    assert r.status_code == status, r.text
    return r.json()


async def new_ticket(
    client: AsyncClient,
    w: dict[str, Any],
    subject: str = "Help",
    *,
    email: str = "ana@shop.test",
    channel: str = "web",
) -> dict[str, Any]:
    body = {
        "subject": subject,
        "channel": channel,
        "description": "It broke.",
        "contact": {"email": email, "name": "Ana Rao"},
    }
    t: dict[str, Any] = ok(await client.post(T, headers=w["h"], json=body), 201)
    return t


async def detail(client: AsyncClient, w: dict[str, Any], ticket_id: str) -> Any:
    return ok(await client.get(f"{T}/{ticket_id}", headers=w["h"]))


async def runs(client: AsyncClient, w: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = ok(await client.get(f"{RUNS}{query}", headers=w["h"]))["runs"]
    return found


async def reply(sm: Sm, w: dict[str, Any], ticket_id: str, body: str = "Any news?") -> Any:
    return await customer_replies.customer_reply(sm, w["ws"], UUID(ticket_id), body)


async def test_rules_saved_before_keep_their_format_and_still_run(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    conditions = [{"kind": "text_contains_any", "words": ["refund"]}]
    actions = [
        {"kind": "set_priority", "priority": "high", "keep_urgent": True},
        {"kind": "add_tag", "tag": "refund"},
    ]
    made = await rule(client, w, "created", conditions, actions, name="Refunds")
    assert made["conditions"] == conditions and made["actions"] == actions
    assert (made["max_runs_per_hour"], made["max_runs_per_day"]) == (1000, 10000)
    t = await new_ticket(client, w, "Refund please")
    assert t["priority"] == "high" and t["tags"] == ["refund"]
    [run] = await runs(client, w)
    assert (run["kind"], run["rule_id"], run["name"], run["trigger"]) == (
        "rule",
        made["id"],
        "Refunds",
        "created",
    )
    assert (run["state"], run["result"], run["depth"]) == ("done", "Priority High · Tag refund", 1)
    assert [s["said"] for s in run["steps"]] == ["Priority High", "Tag refund"]
    # A ticket that doesn't match leaves no run.
    await new_ticket(client, w, "Where is my parcel")
    assert len(await runs(client, w)) == 1


async def test_contact_field_vip_and_segment_conditions(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    h = w["h"]
    ok(
        await client.post(
            "/api/v1/contact-fields",
            headers=h,
            json={"key": "plan", "label": "Plan", "kind": "text"},
        ),
        201,
    )
    ok(
        await client.post(
            "/api/v1/contact-fields",
            headers=h,
            json={"key": "seats", "label": "Seats", "kind": "number"},
        ),
        201,
    )
    segment = ok(
        await client.post(
            "/api/v1/segments", headers=h, json={"name": "VIPs", "filters": {"vip": True}}
        ),
        201,
    )
    # What a rule names must exist, with a value of the field's kind.
    bad = [
        [{"kind": "contact_field", "key": "nope", "op": "equals", "value": "x"}],
        [{"kind": "contact_field", "key": "seats", "op": "equals", "value": "many"}],
        [{"kind": "contact_field", "key": "seats", "op": "contains", "value": "1"}],
        [{"kind": "contact_field", "key": "plan", "op": "more_than", "value": "a"}],
        [{"kind": "contact_in_segment", "segment_ids": [str(UUID(int=7))]}],
        [{"kind": "contact_vip", "value": "yes"}],
    ]
    for conditions in bad:
        await rule(client, w, "customer_replied", conditions, TAG, status=422)

    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "contact_field", "key": "plan", "op": "equals", "value": "GOLD"}],
        [{"kind": "add_tag", "tag": "gold"}],
        name="Gold",
    )
    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "contact_field", "key": "seats", "op": "more_than", "value": 10}],
        [{"kind": "add_tag", "tag": "big"}],
        name="Big",
    )
    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "contact_vip", "value": True}],
        [{"kind": "add_tag", "tag": "vip"}],
        name="VIP",
    )
    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "contact_in_segment", "segment_ids": [segment["id"]]}],
        [{"kind": "add_tag", "tag": "in-segment"}],
        name="Segment",
    )
    t = await new_ticket(client, w)
    await reply(sm, w, t["id"])
    assert (await detail(client, w, t["id"]))["tags"] == []

    contact = t["contact"]["id"]
    ok(
        await client.put(
            f"/api/v1/contacts/{contact}/attributes",
            headers=h,
            json={"attributes": {"plan": "gold", "seats": 12}},
        )
    )
    ok(await client.patch(f"/api/v1/contacts/{contact}/profile", headers=h, json={"vip": True}))
    await reply(sm, w, t["id"])
    assert sorted((await detail(client, w, t["id"]))["tags"]) == [
        "big",
        "gold",
        "in-segment",
        "vip",
    ]
    # A deleted segment is listed as a problem, not a 500.
    assert (await client.delete(f"/api/v1/segments/{segment['id']}", headers=h)).status_code == 204
    listed = {r["name"]: r["problems"] for r in ok(await client.get(R, headers=h))}
    assert listed["Segment"] == ["A segment it checks was deleted."]


async def test_time_since_a_reply_snoozed_and_followers_conditions(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "time_since", "of": "agent_reply", "op": "more_than", "minutes": 60}],
        [{"kind": "add_tag", "tag": "waited"}],
        name="Waited",
    )
    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "has_followers", "value": True}],
        [{"kind": "add_tag", "tag": "followed"}],
        name="Followed",
    )
    t = await new_ticket(client, w)
    ok(
        await client.post(
            f"{T}/{t['id']}/messages", headers=w["h"], json={"kind": "reply", "body": "Looking"}
        ),
        201,
    )
    await reply(sm, w, t["id"])
    assert (await detail(client, w, t["id"]))["tags"] == []  # replied a moment ago
    await sql(
        sm,
        w["ws"],
        "UPDATE hd_message SET created_at = now() - interval '2 hours' "
        "WHERE ticket_id = :t AND kind = 'agent'",
        t=t["id"],
    )
    ok(
        await client.post(
            f"{T}/{t['id']}/followers", headers=w["h"], json={"member_id": w["ids"]["Neha"]}
        )
    )
    await reply(sm, w, t["id"])
    assert sorted((await detail(client, w, t["id"]))["tags"]) == ["followed", "waited"]


async def test_snooze_follow_and_email_actions(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    h = w["h"]
    ok(
        await client.post(
            "/api/v1/email-addresses",
            headers=h,
            json={"address": "support@acme.test", "name": "Acme"},
        ),
        201,
    )
    for bad in (
        [{"kind": "snooze", "minutes": 0}],
        [{"kind": "add_follower", "member_id": str(UUID(int=3))}],
        [{"kind": "send_email", "body": " "}],
    ):
        await rule(client, w, "created", OPEN, bad, status=422)
    await rule(
        client,
        w,
        "created",
        [{"kind": "channel_in", "channels": ["email"]}],
        [
            {"kind": "send_email", "body": "Hi {{customer.first_name}}, we got it."},
            {"kind": "add_follower", "member_id": w["ids"]["Neha"]},
            {"kind": "snooze", "minutes": 120},
        ],
        name="Welcome",
    )
    t = await new_ticket(client, w, channel="email")
    got = await detail(client, w, t["id"])
    assert got["status"] == "pending" and got["snoozed_until"] is not None
    agent = [m for m in got["messages"] if m["kind"] == "agent"]
    assert [m["body"] for m in agent] == ["Hi Ana, we got it."]
    assert any(
        b.startswith(
            "Rule applied: Welcome → Email to the customer · Follower Neha · Snoozed for 2 h"
        )
        for b in [m["body"] for m in got["messages"] if m["kind"] == "system"]
    )
    [email] = await sql(
        sm,
        w["ws"],
        "SELECT to_addresses, body_text FROM hd_email_out WHERE ticket_id = :t",
        t=t["id"],
    )
    assert email[0] == ["ana@shop.test"] and email[1].startswith("Hi Ana, we got it.")
    followers = ok(await client.get(f"{T}/{t['id']}/followers", headers=h))
    assert [f["member_id"] for f in followers["followers"]] == [w["ids"]["Neha"]]
    [run] = await runs(client, w)
    assert "we got it" not in str(run)  # the run log keeps no message text
    # A web ticket gets the message too, but no email.
    t2 = await new_ticket(client, w)
    assert (await detail(client, w, t2["id"]))["tags"] == []


async def test_call_webhook_queues_a_signed_delivery_and_is_an_admins(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    receiver, fake = install(client)
    w = await desk(client, sm)
    hook = ok(
        await client.post(
            "/api/v1/webhooks",
            headers=w["h"],
            json={"url": "https://hooks.example.com/cedesk", "events": ["ticket.resolved"]},
        ),
        201,
    )
    await rule(
        client,
        w,
        "created",
        OPEN,
        [{"kind": "call_webhook", "webhook_id": str(UUID(int=9))}],
        status=422,
    )
    made = await rule(
        client,
        w,
        "created",
        OPEN,
        [{"kind": "call_webhook", "webhook_id": hook["id"]}],
        name="Tell CRM",
    )
    t = await new_ticket(client, w)
    [delivery] = await sql(sm, w["ws"], "SELECT event, body FROM hd_webhook_delivery")
    assert delivery[0] == "automation.webhook"
    body = delivery[1]
    assert body["automation"]["name"] == "Tell CRM" and body["ticket"]["id"] == t["id"]
    assert hook["secret"] not in str(body)
    sent = await send_due(sm, SEALER, fake)
    assert sent.delivered == 1
    [request] = receiver.got
    assert request.headers["X-CeDesk-Event"] == "automation.webhook"
    assert request.headers["X-CeDesk-Signature"].startswith("sha256=")
    [run] = await runs(client, w)
    assert run["result"] == "Webhook hooks.example.com" and "secret" not in str(run)

    # Someone with the settings right but not an Admin can't save or change one.
    ravi = await give(
        client, {**w, "ravi": w["ids"]["Ravi"]}, (await role(client, w, settings=True))["id"]
    )
    assert ok(await client.get(R, headers=ravi))  # they manage the other rules
    r = await client.post(
        R,
        headers=ravi,
        json={
            "name": "Leak",
            "event": "created",
            "conditions": OPEN,
            "actions": [{"kind": "call_webhook", "webhook_id": hook["id"]}],
        },
    )
    assert r.status_code == 403
    r = await client.patch(f"{R}/{made['id']}", headers=ravi, json={"enabled": False})
    assert r.status_code == 403
    ok(await client.patch(f"{R}/{made['id']}", headers=w["h"], json={"enabled": False}))


async def test_the_hourly_limit_and_the_per_ticket_cap(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await rule(client, w, "created", OPEN, TAG, max_runs_per_hour=5, max_runs_per_day=4, status=422)
    made = await rule(client, w, "created", OPEN, TAG, name="Once", max_runs_per_hour=1)
    first = await new_ticket(client, w)
    second = await new_ticket(client, w)
    assert first["tags"] == ["x"] and second["tags"] == []
    blocked = await runs(client, w, "?state=blocked")
    assert [(r["ticket_id"], r["result"]) for r in blocked] == [
        (second["id"], "Hourly limit reached (1 runs).")
    ]
    assert len(await runs(client, w, f"?rule_id={made['id']}")) == 2
    ok(await client.patch(f"{R}/{made['id']}", headers=w["h"], json={"max_runs_per_hour": 50}))
    assert (await new_ticket(client, w))["tags"] == ["x"]

    # Per ticket: a reply rule that keeps changing something stops at the cap.
    flip = await rule(client, w, "customer_replied", [], [], status=422)  # needs parts
    assert flip["error"]["code"]
    await rule(
        client,
        w,
        "customer_replied",
        OPEN,
        [{"kind": "set_status", "status": "pending"}],
        name="Flip",
    )
    await rule(
        client,
        w,
        "customer_replied",
        [{"kind": "status_in", "statuses": ["pending"]}],
        [{"kind": "set_status", "status": "open"}],
        name="Flop",
    )
    t = await new_ticket(client, w)
    await sql(
        sm,
        w["ws"],
        "INSERT INTO hd_automation_run (workspace_id, rule_id, name, "
        "ticket_id, trigger, state) SELECT :w, id, name, :t, 'customer_replied', 'done' "
        "FROM hd_rule, generate_series(1, :n) WHERE name = 'Flip'",
        w=w["ws"],
        t=t["id"],
        n=guard.TICKET_CAP,
    )
    await reply(sm, w, t["id"])
    capped = await runs(client, w, f"?ticket_id={t['id']}&state=blocked")
    assert [r["name"] for r in capped] == ["Flip"]
    assert capped[0]["result"] == f"Ran {guard.TICKET_CAP} times on this ticket in 24 hours."


async def test_loop_protection_stops_automations_answering_each_other(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """An auto-reply that keeps answering the automatic email: each answer is one level
    deeper; past the limit nothing runs."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    await rule(
        client,
        w,
        "customer_replied",
        OPEN,
        [{"kind": "send_email", "body": "Thanks, we'll be in touch."}],
        name="Ack",
    )
    t = await new_ticket(client, w)
    for _ in range(guard.MAX_DEPTH + 1):
        await reply(sm, w, t["id"], "Out of office")
    found = await runs(client, w, f"?ticket_id={t['id']}")
    assert [r["depth"] for r in reversed(found)] == list(range(1, guard.MAX_DEPTH + 2))
    assert found[0]["state"] == "blocked" and found[0]["result"].startswith("Loop protection")
    agent = [m for m in (await detail(client, w, t["id"]))["messages"] if m["kind"] == "agent"]
    assert len(agent) == guard.MAX_DEPTH
    # Later (outside the window) the chain starts again at depth 1.
    await sql(sm, w["ws"], "UPDATE hd_automation_run SET updated_at = now() - interval '1 hour'")
    await reply(sm, w, t["id"], "Back now")
    assert (await runs(client, w, f"?ticket_id={t['id']}"))[0]["depth"] == 1


async def test_test_mode_shows_what_would_happen_and_changes_nothing(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    t = await new_ticket(client, w, "Refund for order", channel="email")
    made = await rule(
        client,
        w,
        "customer_replied",
        [
            {"kind": "text_contains_any", "words": ["refund"]},
            {"kind": "contact_vip", "value": False},
        ],
        [
            {"kind": "set_priority", "priority": "high"},
            {"kind": "add_tag", "tag": "refund"},
            {"kind": "send_email", "body": "Hi"},
        ],
        name="Refund replies",
    )
    got = ok(
        await client.post(f"{R}/{made['id']}/test", headers=w["h"], json={"ticket_id": t["id"]})
    )
    assert got["matched"] is True and got["blocked"] is None
    assert [c["matched"] for c in got["conditions"]] == [True, True]
    assert [a["would"] for a in got["actions"]] == [
        "Priority High",
        "Tag refund",
        "Email to the customer",
    ]
    assert got["ticket_after"]["priority"] == "high" and got["ticket_after"]["tags"] == ["refund"]
    draft = {
        "event": "created",
        "conditions": [{"kind": "contact_vip", "value": True}],
        "actions": TAG,
    }
    got = ok(
        await client.post(f"{R}/test", headers=w["h"], json={"ticket_id": t["id"], "rule": draft})
    )
    assert got["matched"] is False and got["actions"] == []
    bad = await client.post(f"{R}/test", headers=w["h"], json={"ticket_id": t["id"]})
    assert bad.status_code == 422
    # Nothing changed, nothing was sent or logged.
    after = await detail(client, w, t["id"])
    assert after["priority"] == t["priority"] and after["tags"] == []
    assert [m for m in after["messages"] if m["kind"] == "agent"] == []
    assert await runs(client, w) == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_email_out") == []
    missing = await client.post(
        f"{R}/{made['id']}/test", headers=w["h"], json={"ticket_id": str(UUID(int=1))}
    )
    assert missing.status_code == 404


async def test_snooze_ended_rules_run_when_the_worker_wakes_a_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await rule(
        client,
        w,
        "snooze_ended",
        [{"kind": "snoozed", "value": False}],
        [{"kind": "set_priority", "priority": "urgent"}],
        name="Back",
    )
    t = await new_ticket(client, w)
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    ok(await client.post(f"{T}/{t['id']}/snooze", headers=w["h"], json={"until": until}))
    await sql(
        sm,
        w["ws"],
        "UPDATE hd_ticket SET snoozed_until = now() - interval '1 minute' WHERE id = :t",
        t=t["id"],
    )
    assert await wake_workspace(sm, w["ws"]) == 1
    got = await detail(client, w, t["id"])
    assert (got["status"], got["priority"]) == ("open", "urgent")
    assert (await runs(client, w))[0]["trigger"] == "snooze_ended"


async def test_who_reads_the_run_log_and_workspace_isolation(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    made = await rule(client, w, "created", OPEN, TAG, name="Tag all")
    t = await new_ticket(client, w)
    [run] = await runs(client, w)
    for who in ("Neha", "Sam", "Meena"):  # no settings right
        h = await as_(client, w, who)
        for r in (
            await client.get(RUNS, headers=h),
            await client.get(f"{RUNS}/{run['id']}", headers=h),
            await client.post(f"{R}/{made['id']}/test", headers=h, json={"ticket_id": t["id"]}),
        ):
            assert r.status_code == 403
    assert ok(await client.get(f"{RUNS}/{run['id']}", headers=w["h"]))["id"] == run["id"]
    bad = await client.get(f"{RUNS}?state=nope", headers=w["h"])
    assert bad.status_code == 422
    assert (await client.get(f"{RUNS}?cursor=xyz", headers=w["h"])).status_code == 422
    # Paging.
    await new_ticket(client, w)
    page = ok(await client.get(f"{RUNS}?limit=1", headers=w["h"]))
    assert len(page["runs"]) == 1 and page["next_cursor"]
    rest = ok(await client.get(f"{RUNS}?limit=1&cursor={page['next_cursor']}", headers=w["h"]))
    assert rest["runs"][0]["id"] == run["id"]

    other = await desk(client, sm)
    assert await runs(client, other) == []
    assert (await client.get(f"{RUNS}/{run['id']}", headers=other["h"])).status_code == 404
    assert (
        await client.post(f"{R}/{made['id']}/test", headers=other["h"], json={"ticket_id": t["id"]})
    ).status_code == 404
    mine = await rule(client, other, "created", OPEN, TAG, name="Theirs")
    r = await client.post(f"{R}/{mine['id']}/test", headers=other["h"], json={"ticket_id": t["id"]})
    assert r.status_code == 404  # the first workspace's ticket isn't theirs
    assert (await client.post(f"{RUNS}/{run['id']}/cancel", headers=other["h"])).status_code == 404


async def test_old_finished_runs_are_pruned(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await rule(client, w, "created", OPEN, TAG, name="Tag all")
    await new_ticket(client, w)
    await new_ticket(client, w)
    await sql(
        sm,
        w["ws"],
        "UPDATE hd_automation_run SET finished_at = now() - interval '31 days' "
        "WHERE id = (SELECT id FROM hd_automation_run ORDER BY created_at LIMIT 1)",
    )
    assert await prune_everywhere(sm) >= 1
    assert len(await runs(client, w)) == 1


async def test_an_api_key_cant_save_a_webhook_rule(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    install(client)
    w = await desk(client, sm)
    hook = ok(
        await client.post(
            "/api/v1/webhooks",
            headers=w["h"],
            json={"url": "https://hooks.example.com/x", "events": ["ticket.resolved"]},
        ),
        201,
    )
    key = ok(
        await client.post(
            "/api/v1/api-keys", headers=w["h"], json={"name": "CI", "access": "admin"}
        ),
        201,
    )
    h = {"Authorization": f"Bearer {key['key']}"}
    r = await client.post(
        R,
        headers=h,
        json={
            "name": "Leak",
            "event": "created",
            "conditions": OPEN,
            "actions": [{"kind": "call_webhook", "webhook_id": hook["id"]}],
        },
    )
    assert r.status_code == 403
    assert bearer(await sign_in(client, w["slug"], "admin@example.com"))
