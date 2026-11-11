"""Rules and the SLA (md W14, W15) with RLS on: rules on the `sla_breached` event run when
the worker finds a breach (before the alert, which goes to whoever has the ticket after
them), and the `sla_in` condition reads the clock the SLA pill shows."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service import customer_replies
from cedesk.helpdesk.service.sla_alerts import alert_workspace
from tests.db.helpers import Sm, ok
from tests.db.test_reports import T, desk, ticket
from tests.db.test_sla_alerts import inbox, made_ago

pytestmark = pytest.mark.db

RULES = "/api/v1/rules"


async def rule(client: AsyncClient, w: dict[str, Any], body: dict[str, Any]) -> Any:
    return ok(await client.post(RULES, headers=w["h"], json={"enabled": True, **body}), 201)


async def get(client: AsyncClient, w: dict[str, Any], ticket_id: str) -> Any:
    return ok(await client.get(f"{T}/{ticket_id}", headers=w["h"]))


def rule_lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["body"].startswith("Rule applied")]


async def test_a_breach_rule_escalates_and_the_new_owners_hear(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    shipping = {"name": "Shipping", "member_ids": [w["ids"]["Ravi"], w["ids"]["Sam"]]}
    ok(await client.patch(f"/api/v1/teams/{w['teams']['Shipping']}", headers=w["h"],
                          json=shipping))  # fmt: skip
    await rule(client, w, {"name": "Escalate breaches", "event": "sla_breached",
                           "conditions": [{"kind": "status_in", "statuses": ["open"]}],
                           "actions": [{"kind": "set_team", "team_id": w["teams"]["Shipping"]},
                                       {"kind": "set_priority", "priority": "high"},
                                       {"kind": "add_tag", "tag": "escalated"}]})  # fmt: skip
    late = await ticket(client, w, "Late", "Billing", "Neha")
    fresh = await ticket(client, w, "Fresh", "Billing", "Neha")
    await made_ago(app_sessionmaker, w, late, 90)  # Medium: 60 minutes to the first reply

    assert await alert_workspace(app_sessionmaker, w["ws"]) == 1
    t = await get(client, w, late)
    assert (t["team_name"], t["priority"], t["tags"]) == ("Shipping", "high", ["escalated"])
    assert rule_lines(t) == [
        "Rule applied: Escalate breaches → Team Shipping · Priority High · Tag escalated"
    ]
    assert t["assignee_id"] is None  # Shipping doesn't auto-assign: back in the queue
    # The alert goes to Shipping's Supervisor, not to Neha, who no longer has it.
    note = f"SLA breached on T-{t['number']} (first reply overdue): Late"
    assert await inbox(client, w, "Sam") == [note]
    assert await inbox(client, w, "Neha") == []
    # High is due 60 minutes after creation too: already past, already alerted.
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
    assert rule_lines(await get(client, w, fresh)) == []  # on time: no breach, no rule


async def test_a_breach_rule_that_gives_more_time_alerts_again_later(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Lowering the priority moves the due time into the future: when that passes too,
    the ticket breaches (and alerts) again; the rule then finds nothing to change."""
    w = await desk(client, app_sessionmaker)
    await rule(client, w, {"name": "Breached: low", "event": "sla_breached",
                           "conditions": [{"kind": "sla_in", "states": ["breached"]}],
                           "actions": [{"kind": "set_priority", "priority": "low"}]})  # fmt: skip
    t = await ticket(client, w, "Slow", "Billing", "Neha")
    await made_ago(app_sessionmaker, w, t, 90)
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 1
    now = await get(client, w, t)
    assert (now["priority"], now["sla"]["state"]) == ("low", "on_track")  # 480 minutes
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
    # Seven hours on (Low gives 480 minutes from creation, 90 used): due again.
    later = datetime.now(UTC) + timedelta(hours=7)
    assert await alert_workspace(app_sessionmaker, w["ws"], now=later - timedelta(hours=1)) == 0
    assert await alert_workspace(app_sessionmaker, w["ws"], now=later) == 1
    assert len(await inbox(client, w, "Neha")) == 2
    assert len(rule_lines(await get(client, w, t))) == 1


async def test_sla_in_on_a_customer_reply(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await rule(client, w, {"name": "Kept waiting", "event": "customer_replied",
                           "conditions": [{"kind": "sla_in", "states": ["breached"]}],
                           "actions": [{"kind": "set_priority", "priority": "urgent"},
                                       {"kind": "add_tag", "tag": "kept-waiting"}]})  # fmt: skip
    late = await ticket(client, w, "Late", "Billing", "Neha")
    fresh = await ticket(client, w, "Fresh", "Billing", "Neha")
    await made_ago(app_sessionmaker, w, late, 90)
    for t in (late, fresh):
        await customer_replies.customer_reply(app_sessionmaker, w["ws"], UUID(t), "Any news?")
    assert (await get(client, w, late))["tags"] == ["kept-waiting"]
    assert (await get(client, w, late))["priority"] == "urgent"
    assert (await get(client, w, fresh))["tags"] == []


async def test_sla_rules_are_checked(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    tag = [{"kind": "add_tag", "tag": "x"}]
    for event, conditions in (
        ("created", [{"kind": "sla_in", "states": ["breached"]}]),  # only just started
        ("customer_replied", [{"kind": "sla_in", "states": ["late"]}]),
        ("customer_replied", [{"kind": "sla_in", "states": []}]),
        ("sla_broken", [{"kind": "status_in", "statuses": ["open"]}]),
    ):
        r = await client.post(RULES, headers=w["h"], json={
            "name": "Bad", "event": event, "conditions": conditions, "actions": tag})  # fmt: skip
        assert r.status_code == 422, (event, conditions)
    made = await rule(client, w, {"name": "Every state", "event": "customer_replied",
                                  "conditions": [{"kind": "sla_in", "states": [
                                      "met", "on_track", "close", "breached"]}],
                                  "actions": tag})  # fmt: skip
    assert made["conditions"] == [
        {"kind": "sla_in", "states": ["breached", "close", "met", "on_track"]}
    ]
