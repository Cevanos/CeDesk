"""The rule engine (md W15-W17, 6.3, 6.4) without a database."""

from typing import Any
from uuid import UUID, uuid4

import pytest

from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.rules import Event, Facts, RuleError, State
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment, Status

REFUND, TECH_TYPE = uuid4(), uuid4()
BILLING, TECH = uuid4(), uuid4()
TYPES = {REFUND: "Refund", TECH_TYPE: "Technical problem"}
TEAMS = {BILLING: "Billing", TECH: "Tech"}


def rule(name: str, conditions: list[Any], actions: list[Any], event: str = "created") -> Any:
    return engine.parse_rule(uuid4(), name, event, conditions, actions)


def facts(text: str = "", **extra: Any) -> Facts:
    values: dict[str, Any] = {
        "text": text,
        "channel": Channel.web,
        "to_address": None,
        "sentiment": None,
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


def run(rules: list[Any], f: Facts, s: State, event: Event = Event.created) -> Any:
    return engine.run(rules, event, f, s, types=TYPES, teams=TEAMS)


REFUND_RULE = rule(
    "Refund requests to Billing",
    [{"kind": "text_contains_any", "words": ["Refund"]}],
    [
        {"kind": "set_type", "type_id": str(REFUND)},
        {"kind": "set_team", "team_id": str(BILLING)},
        {"kind": "set_priority", "priority": "high", "keep_urgent": True},
    ],
)
ERROR_RULE = rule(
    "App errors to Tech",
    [{"kind": "text_contains_any", "words": ["crash", "error"]}],
    [
        {"kind": "set_type", "type_id": str(TECH_TYPE)},
        {"kind": "set_team", "team_id": str(TECH)},
    ],
)


def test_a_matching_rule_changes_the_ticket_and_says_what_it_did() -> None:
    final, applied = run([REFUND_RULE], facts("I want a REFUND please"), state())
    assert (final.type_id, final.team_id, final.priority) == (REFUND, BILLING, Priority.high)
    assert [engine.line(a) for a in applied] == [
        "Rule applied: Refund requests to Billing → Type Refund · Team Billing · Priority High"
    ]


def test_no_match_changes_nothing() -> None:
    start = state()
    final, applied = run([REFUND_RULE, ERROR_RULE], facts("Where is my parcel?"), start)
    assert final == start
    assert applied == []


def test_every_matching_rule_runs_in_order_and_later_ones_win() -> None:
    final, applied = run([REFUND_RULE, ERROR_RULE], facts("refund: the app crashed"), state())
    assert [a.rule.name for a in applied] == ["Refund requests to Billing", "App errors to Tech"]
    assert (final.type_id, final.team_id) == (TECH_TYPE, TECH)
    assert final.priority is Priority.high  # the second rule does not touch it


def test_keep_urgent_never_lowers_an_urgent_ticket() -> None:
    final, applied = run([REFUND_RULE], facts("refund"), state(priority=Priority.urgent))
    assert final.priority is Priority.urgent
    assert applied[0].done == ("Type Refund", "Team Billing")


def test_only_if_empty_leaves_a_type_already_set() -> None:
    r = rule(
        "billing@",
        [{"kind": "to_address_contains", "text": "Billing@"}],
        [{"kind": "set_type", "type_id": str(TECH_TYPE), "only_if_empty": True}],
    )
    f = facts(to_address="billing@acme-store.in")
    assert run([r], f, state())[0].type_id == TECH_TYPE
    assert run([r], f, state(type_id=REFUND))[0].type_id == REFUND


def test_all_conditions_must_match() -> None:
    r = rule(
        "billing@ emails",
        [
            {"kind": "channel_in", "channels": ["email"]},
            {"kind": "to_address_contains", "text": "billing@"},
        ],
        [{"kind": "set_team", "team_id": str(BILLING)}],
    )
    to = "Billing@Acme-Store.in"
    assert run([r], facts(channel=Channel.email, to_address=to), state())[1]
    assert not run([r], facts(channel=Channel.web, to_address=to), state())[1]
    assert not run([r], facts(channel=Channel.email, to_address="support@x.in"), state())[1]
    assert not run([r], facts(channel=Channel.email), state())[1]


def test_status_and_sentiment_conditions() -> None:
    reopen = rule(
        "Reopen",
        [{"kind": "status_in", "statuses": ["pending"]}],
        [{"kind": "set_status", "status": "open"}],
        event="customer_replied",
    )
    final, _ = run([reopen], facts(), state(status=Status.pending), Event.customer_replied)
    assert final.status is Status.open
    assert not run([reopen], facts(), state(), Event.customer_replied)[1]
    # A customer_replied rule never runs on a new ticket, and the other way round.
    assert not run([reopen], facts(), state(status=Status.pending), Event.created)[1]

    upset = rule(
        "Upset",
        [{"kind": "sentiment_in", "sentiments": ["negative"]}],
        [{"kind": "add_tag", "tag": "Upset"}],
    )
    assert run([upset], facts(sentiment=Sentiment.negative), state())[0].tags == ("upset",)
    assert not run([upset], facts(sentiment=Sentiment.positive), state())[1]
    assert not run([upset], facts(), state())[1]


def test_a_tag_is_added_once_and_never_past_the_limit() -> None:
    r = rule(
        "Tag", [{"kind": "text_contains_any", "words": ["x"]}], [{"kind": "add_tag", "tag": "vip"}]
    )
    assert run([r], facts("x"), state(tags=("vip",)))[0].tags == ("vip",)
    full = tuple(f"t{i}" for i in range(20))
    final, applied = run([r], facts("x"), state(tags=full))
    assert final.tags == full
    assert applied == []  # it matched but changed nothing, so it writes no line


def test_a_deleted_team_is_skipped_and_reported() -> None:
    gone = uuid4()
    r = rule(
        "Old",
        [{"kind": "text_contains_any", "words": ["x"]}],
        [{"kind": "set_team", "team_id": str(gone)}, {"kind": "add_tag", "tag": "old"}],
    )
    final, applied = run([r], facts("x"), state())
    assert final.team_id is None
    assert applied[0].done == ("Tag old",)
    assert engine.problems(r, TYPES, TEAMS) == ["Its team was deleted."]
    assert engine.problems(REFUND_RULE, TYPES, TEAMS) == []


@pytest.mark.parametrize(
    ("conditions", "actions"),
    [
        ([], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "text_contains_any", "words": ["x"]}], []),
        ([{"kind": "nope"}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "text_contains_any", "words": []}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "text_contains_any", "words": [" "]}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "text_contains_any", "words": ["x" * 101]}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "text_contains_any", "words": ["x"] * 51}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "channel_in", "channels": ["fax"]}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "status_in", "statuses": "open"}], [{"kind": "add_tag", "tag": "x"}]),
        ([{"kind": "status_in", "statuses": ["open"]}], [{"kind": "add_tag", "tag": "a b!"}]),
        ([{"kind": "status_in", "statuses": ["open"]}], [{"kind": "set_team", "team_id": "x"}]),
        (
            [{"kind": "status_in", "statuses": ["open"]}],
            [{"kind": "set_priority", "priority": "p1"}],
        ),
        ([{"kind": "status_in", "statuses": ["open"]}], [{"kind": "set_status", "status": None}]),
        (
            [{"kind": "status_in", "statuses": ["open"]}],
            [{"kind": "set_type", "type_id": str(REFUND), "only_if_empty": "yes"}],
        ),
        (
            [{"kind": "status_in", "statuses": ["open"]}],
            [{"kind": "set_status", "status": "open"}, {"kind": "set_status", "status": "pending"}],
        ),
        ([{"kind": "status_in", "statuses": ["open"]}] * 11, [{"kind": "add_tag", "tag": "x"}]),
    ],
)
def test_a_bad_rule_is_refused(conditions: list[Any], actions: list[Any]) -> None:
    with pytest.raises(RuleError):
        engine.parse_rule(uuid4(), "Bad", "created", conditions, actions)


def test_a_bad_event_is_refused() -> None:
    with pytest.raises(RuleError):
        engine.parse_rule(uuid4(), "Bad", "updated", [{"kind": "status_in", "statuses": ["open"]}],
                          [{"kind": "add_tag", "tag": "x"}])  # fmt: skip


def test_tags_may_repeat_in_one_rule() -> None:
    r = rule(
        "Two tags",
        [{"kind": "text_contains_any", "words": ["x"]}],
        [{"kind": "add_tag", "tag": "a"}, {"kind": "add_tag", "tag": "b"}],
    )
    assert run([r], facts("x"), state())[0].tags == ("a", "b")


def test_stored_json_reads_back_the_same() -> None:
    for r in [REFUND_RULE, ERROR_RULE]:
        conditions = [engine.stored_condition(c) for c in r.conditions]
        actions = [engine.stored_action(a) for a in r.actions]
        again = engine.parse_rule(r.id, r.name, r.event.value, conditions, actions)
        assert again == r
    assert engine.stored_condition(REFUND_RULE.conditions[0]) == {
        "kind": "text_contains_any",
        "words": ["refund"],
    }
    assert isinstance(engine.stored_action(REFUND_RULE.actions[0])["type_id"], str)
    assert UUID(engine.stored_action(REFUND_RULE.actions[0])["type_id"]) == REFUND


@pytest.mark.parametrize(
    "body",
    [
        "thanks", "Thanks!", "  thank you. ", "THX", "ok thanks", "Great, thanks!!",
        "great thanks", "Thank you so much!", "thanks a lot", "Thanks again 🙏", "ty", "TQ",
        "thank u", "Thanku", "many thanks", "okay, thank you very much", "got it, thanks",
        "Dhanyavaad", "shukriya ji", "धन्यवाद", "🙏", "👍👍", "thanks sir", "Noted, thanks.",
        "Thanks 🙏🏻", "👍🏽", "Thank you, ma'am", "thanks, sir", "thanks :)", "thank you :-)",
        "Thanks ❤️", "ok thanks :D",
    ],
)  # fmt: skip
def test_a_thank_you_only_reply(body: str) -> None:
    assert engine.is_thanks_only(body)


@pytest.mark.parametrize(
    "body",
    [
        "thanks, but it is still broken", "no thanks", "thank you so much, one more thing",
        "ok", "", "thanks for nothing", "type", "thank you, where is my refund?",
        "not received, thanks", "🙏 still waiting", "thanks :( still broken", "👎",
        "thanks, but sir it is broken",
    ],
)  # fmt: skip
def test_anything_more_than_thanks_is_a_real_reply(body: str) -> None:
    assert not engine.is_thanks_only(body)


def test_only_real_changes_are_reported() -> None:
    already = state(type_id=TECH_TYPE, team_id=TECH, priority=Priority.high)
    _, applied = run([REFUND_RULE, ERROR_RULE], facts("refund, app crashed"), already)
    # Refund changed type and team; errors moved them back. Priority was already High.
    assert [a.done for a in applied] == [("Type Refund", "Team Billing"),
                                         ("Type Technical problem", "Team Tech")]  # fmt: skip
    same = rule(
        "Same",
        [{"kind": "text_contains_any", "words": ["x"]}],
        [{"kind": "set_status", "status": "open"}, {"kind": "add_tag", "tag": "a"}],
    )
    _, applied = run([same], facts("x"), state(tags=("a",)))
    assert applied == []
