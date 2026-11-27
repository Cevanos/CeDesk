"""Automation (feature-gap plan 5.1-5.5) without a database: the new conditions and
actions, the workflow graph's checks and walk, the safeguards and the catalog."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import automation as guard
from cedesk.helpdesk.domain import flows
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.flows import FlowError, Way
from cedesk.helpdesk.domain.rules import Facts, Member, RuleError, State, Targets
from cedesk.helpdesk.domain.tickets import Channel, Priority, Status

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
NEHA, HOOK, SEGMENT = uuid4(), uuid4(), uuid4()
TARGETS = Targets(members={NEHA: Member("Neha")}, webhooks={HOOK: "hooks.example.com"})


def facts(**extra: Any) -> Facts:
    values: dict[str, Any] = {
        "text": "",
        "channel": Channel.web,
        "to_address": None,
        "sentiment": None,
        "now": NOW,
        "created_at": NOW,
    }
    return Facts(**(values | extra))


def state(**extra: Any) -> State:
    values: dict[str, Any] = {
        "type_id": None,
        "team_id": None,
        "priority": Priority.medium,
        "status": Status.open,
        "tags": (),
    }
    return State(**(values | extra))


def cond(raw: dict[str, Any]) -> Any:
    return engine.parse_condition(raw)


def test_new_conditions_parse_and_match() -> None:
    plan = cond({"kind": "contact_field", "key": "plan", "op": "equals", "value": "Gold"})
    assert engine.matches(plan, facts(attributes={"plan": "gold"}), state())
    assert not engine.matches(plan, facts(), state())
    seats = cond({"kind": "contact_field", "key": "seats", "op": "more_than", "value": 10})
    assert engine.matches(seats, facts(attributes={"seats": 12}), state())
    assert not engine.matches(seats, facts(attributes={"seats": "x"}), state())
    unset = cond({"kind": "contact_field", "key": "plan", "op": "not_set"})
    assert engine.matches(unset, facts(attributes={"plan": ""}), state())
    vip = cond({"kind": "contact_vip", "value": True})
    assert engine.matches(vip, facts(contact_vip=True), state())
    seg = cond({"kind": "contact_in_segment", "segment_ids": [str(SEGMENT)], "negate": True})
    assert not engine.matches(seg, facts(segments=frozenset({SEGMENT})), state())
    assert engine.matches(cond({"kind": "snoozed", "value": True}), facts(), state(snoozed=True))
    assert engine.matches(cond({"kind": "has_followers", "value": False}), facts(), state())
    since = cond({"kind": "time_since", "of": "customer_reply", "op": "more_than", "minutes": 60})
    assert engine.matches(since, facts(last_customer_at=NOW - timedelta(hours=2)), state())
    assert not engine.matches(since, facts(last_customer_at=NOW - timedelta(minutes=5)), state())
    # No reply yet: counted from the ticket's creation.
    assert engine.matches(since, facts(created_at=NOW - timedelta(hours=3)), state())


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "contact_field", "key": "Bad Key", "op": "equals", "value": "x"},
        {"kind": "contact_field", "key": "plan", "op": "equals"},
        {"kind": "contact_field", "key": "plan", "op": "nope", "value": 1},
        {"kind": "contact_in_segment", "segment_ids": []},
        {"kind": "time_since", "of": "customer_reply", "op": "more_than", "minutes": 0},
        {"kind": "snoozed", "value": "yes"},
    ],
)
def test_bad_new_conditions_are_refused(raw: dict[str, Any]) -> None:
    with pytest.raises(RuleError):
        engine.parse_condition(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "snooze", "minutes": 0},
        {"kind": "snooze", "minutes": engine.MAX_MINUTES + 1},
        {"kind": "add_follower", "member_id": "x"},
        {"kind": "send_email", "body": ""},
        {"kind": "send_email", "body": "x" * (engine.EMAIL_MAX + 1)},
        {"kind": "call_webhook"},
    ],
)
def test_bad_new_actions_are_refused(raw: dict[str, Any]) -> None:
    with pytest.raises(RuleError):
        engine.parse_action(raw)


def test_new_actions_change_the_state_and_queue_effects() -> None:
    actions = [
        engine.parse_action(a)
        for a in (
            {"kind": "add_follower", "member_id": str(NEHA)},
            {"kind": "add_follower", "member_id": str(NEHA)},  # already: nothing
            {"kind": "snooze", "minutes": 90},
            {"kind": "send_email", "body": "Hi"},
            {"kind": "call_webhook", "webhook_id": str(HOOK)},
            {"kind": "call_webhook", "webhook_id": str(uuid4())},  # gone: skipped
        )
    ]
    after, done = engine.apply_actions(actions, state(), TARGETS)
    assert done == [
        "Follower Neha",
        "Snoozed for 1 h 30 min",
        "Email to the customer",
        "Webhook hooks.example.com",
    ]
    assert after.followers == frozenset({NEHA}) and after.snoozed
    assert [type(e).__name__ for e in after.effects] == [
        "AddFollower",
        "Snooze",
        "SendEmail",
        "CallWebhook",
    ]


def graph(*nodes: dict[str, Any], edges: list[Any]) -> dict[str, Any]:
    return {
        "nodes": [{"id": "start", "type": "trigger", "event": "created"}, *nodes],
        "edges": edges,
    }


TAG = {"id": "tag", "type": "action", "action": {"kind": "add_tag", "tag": "x"}}
WAIT = {"id": "wait", "type": "wait", "minutes": 60, "until": "customer_replied"}
DONE = {"id": "done", "type": "action", "action": {"kind": "add_tag", "tag": "done"}}
LATE = {"id": "late", "type": "action", "action": {"kind": "set_priority", "priority": "high"}}
GOOD = graph(
    TAG,
    WAIT,
    DONE,
    LATE,
    edges=[
        {"from": "start", "to": "tag"},
        {"from": "tag", "to": "wait"},
        {"from": "wait", "to": "done", "when": "replied"},
        {"from": "wait", "to": "late", "when": "timeout"},
    ],
)


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        ([], "object with `nodes`"),
        (graph(edges=[]), "2-50 nodes"),
        (graph(TAG, {**TAG, "id": "start"}, edges=[]), "Two nodes"),
        (
            graph(
                TAG,
                {"id": "s2", "type": "trigger", "event": "created"},
                edges=[{"from": "start", "to": "tag"}],
            ),
            "exactly one trigger",
        ),
        (graph(TAG, edges=[{"from": "start", "to": "nope"}]), "isn't there"),
        (
            graph(TAG, edges=[{"from": "start", "to": "tag"}, {"from": "tag", "to": "start"}]),
            "back into the trigger",
        ),
        (
            graph(TAG, edges=[{"from": "start", "to": "tag"}, {"from": "tag", "to": "tag"}]),
            "lead to itself",
        ),
        (graph(TAG, edges=[{"from": "start", "to": "tag", "when": "yes"}]), "is next"),
        (graph(TAG, DONE, edges=[{"from": "start", "to": "tag"}]), "Not reachable"),
        (
            graph(
                TAG,
                DONE,
                edges=[
                    {"from": "start", "to": "tag"},
                    {"from": "tag", "to": "done"},
                    {"from": "done", "to": "tag"},
                ],
            ),
            "without a wait",
        ),
        (
            graph(
                TAG, DONE, edges=[{"from": "start", "to": "tag"}, {"from": "start", "to": "done"}]
            ),
            "two next edges",
        ),
        (
            graph({"id": "w", "type": "wait", "minutes": 0}, edges=[{"from": "start", "to": "w"}]),
            "minutes is",
        ),
    ],
)
def test_bad_graphs_are_refused(raw: Any, says: str) -> None:
    with pytest.raises(FlowError, match=says):
        flows.parse_graph(raw)


def test_too_many_nodes() -> None:
    many = [{**TAG, "id": f"n{i}"} for i in range(flows.MAX_NODES)]
    with pytest.raises(FlowError, match="2-50 nodes"):
        flows.parse_graph(graph(*many, edges=[]))


def test_a_good_graph_round_trips_and_keeps_canvas_data() -> None:
    raw = {
        **GOOD,
        "nodes": [{**n, "position": {"x": 1, "y": 2}, "label": "L"} for n in GOOD["nodes"]],
    }
    parsed = flows.parse_graph(raw)
    stored = flows.stored_graph(parsed)
    assert flows.stored_graph(flows.parse_graph(stored)) == stored
    assert stored["nodes"][0]["position"] == {"x": 1, "y": 2}
    assert {e["when"] for e in stored["edges"]} == {"next", "replied", "timeout"}
    assert parsed.event is engine.Event.created


def test_walk_stops_at_a_wait_and_resumes_by_the_way_out() -> None:
    parsed = flows.parse_graph(GOOD)
    first = flows.walk(parsed, facts(), state(), TARGETS)
    assert (first.wait_at, first.wait_minutes, first.until_reply) == ("wait", 60, True)
    assert [s.node for s in first.steps] == ["tag", "wait"] and first.state.tags == ("x",)
    replied = flows.walk(parsed, facts(), first.state, TARGETS, at="wait", way=Way.replied)
    assert replied.wait_at is None and replied.state.tags == ("x", "done")
    late = flows.walk(parsed, facts(), first.state, TARGETS, at="wait", way=Way.timeout)
    assert late.state.priority is Priority.high and late.state.tags == ("x",)


def test_a_condition_branches() -> None:
    raw = graph(
        {"id": "vip", "type": "condition", "conditions": [{"kind": "contact_vip", "value": True}]},
        TAG,
        DONE,
        edges=[
            {"from": "start", "to": "vip"},
            {"from": "vip", "to": "tag", "when": "yes"},
            {"from": "vip", "to": "done", "when": "no"},
        ],
    )
    parsed = flows.parse_graph(raw)
    assert flows.walk(parsed, facts(contact_vip=True), state(), TARGETS).state.tags == ("x",)
    assert flows.walk(parsed, facts(), state(), TARGETS).state.tags == ("done",)


def test_a_failed_condition_says_what_the_ticket_has() -> None:
    snoozed = cond({"kind": "snoozed", "value": True})
    assert engine.why(snoozed, facts(), state()) == "the ticket is not snoozed"
    assert engine.why(snoozed, facts(), state(snoozed=True)) == "the ticket is snoozed"
    followers = cond({"kind": "has_followers", "value": True})
    assert engine.why(followers, facts(), state()) == "the ticket has no followers"
    plan = cond({"kind": "contact_field", "key": "tier", "op": "equals", "value": "Gold"})
    assert engine.why(plan, facts(attributes={"tier": "Silver"}), state()) == (
        "the customer's tier is Silver"
    )
    assert engine.why(plan, facts(), state()) == "the customer's tier is empty"
    since = cond({"kind": "time_since", "of": "customer_reply", "op": "more_than", "minutes": 60})
    ago = facts(last_customer_at=NOW - timedelta(minutes=30))
    assert engine.why(since, ago, state()) == "30 min since the last customer reply"


def test_a_condition_node_that_says_no_gives_its_reason() -> None:
    raw = graph(
        {"id": "snz", "type": "condition", "conditions": [{"kind": "snoozed", "value": True}]},
        TAG,
        edges=[{"from": "start", "to": "snz"}, {"from": "snz", "to": "tag", "when": "yes"}],
    )
    live = flows.walk(flows.parse_graph(raw), facts(), state(), TARGETS)
    assert live.steps[0].reason is None  # a live run does not work reasons out
    walked = flows.walk(flows.parse_graph(raw), facts(), state(), TARGETS, reasons=True)
    assert walked.steps[0].outcome == "no"
    assert walked.steps[0].reason == "the ticket is not snoozed"
    yes = flows.walk(flows.parse_graph(raw), facts(), state(snoozed=True), TARGETS, reasons=True)
    assert yes.steps[0].outcome == "yes" and yes.steps[0].reason is None


def test_a_loop_through_a_wait_stops_at_the_wait_limit() -> None:
    raw = graph(
        TAG,
        {"id": "w", "type": "wait", "minutes": 1},
        edges=[
            {"from": "start", "to": "tag"},
            {"from": "tag", "to": "w"},
            {"from": "w", "to": "tag"},
        ],
    )
    parsed = flows.parse_graph(raw)
    walked = flows.walk(parsed, facts(), state(), TARGETS, at="w", waits_before=flows.MAX_WAITS)
    assert walked.stopped and walked.wait_at is None


def test_safeguards() -> None:
    assert guard.check_limits(10, 10) == (10, 10)
    for per_hour, per_day in ((0, 10), (10, 5), (guard.MAX_PER_HOUR + 1, guard.MAX_PER_DAY)):
        with pytest.raises(guard.LimitError):
            guard.check_limits(per_hour, per_day)
    assert guard.depth(False, 4) == 1 and guard.depth(True, 4) == 5
    fine = guard.Counts(0, 0, 0)
    assert guard.blocked_reason(at_depth=1, counts=fine, per_hour=1, per_day=1) is None
    assert "Loop" in str(
        guard.blocked_reason(at_depth=guard.MAX_DEPTH + 1, counts=fine, per_hour=1, per_day=1)
    )
    cases = [
        (guard.Counts(guard.TICKET_CAP, 0, 0), "this ticket"),
        (guard.Counts(0, 1, 1), "Hourly"),
        (guard.Counts(0, 0, 5), "Daily"),
    ]
    for counts, says in cases:
        assert says in str(guard.blocked_reason(at_depth=1, counts=counts, per_hour=1, per_day=5))
    assert guard.summary([]) == "Nothing to change"


def test_the_catalog_lists_every_kind() -> None:
    got = flows.catalog(
        channels=["web"], statuses=["open"], sentiments=[], sla_states=[], priorities=["high"]
    )
    assert {c["kind"] for c in got["conditions"]} == set(engine.CONDITION_KINDS)
    assert {a["kind"] for a in got["actions"]} == set(engine.ACTION_KINDS)
    assert [t["event"] for t in got["triggers"]] == [e.value for e in engine.Event]
