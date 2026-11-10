"""Macro steps (md W18) without a database."""

from typing import Any
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import macros
from cedesk.helpdesk.domain.macros import MacroError, Note, Reply
from cedesk.helpdesk.domain.rules import AddTag, SetPriority, SetStatus, SetTeam
from cedesk.helpdesk.domain.tickets import Priority, Status


def test_steps_read_in_order_and_store_back_the_same() -> None:
    team = uuid4()
    raw: list[dict[str, Any]] = [
        {"kind": "reply", "text": "  Hi {{customer.first_name}}  "},
        {"kind": "add_tag", "tag": "Refund"},
        {"kind": "set_priority", "priority": "low"},
        {"kind": "note", "text": "check"},
        {"kind": "set_status", "status": "resolved"},
        {"kind": "set_team", "team_id": str(team)},
        {"kind": "add_tag", "tag": "two"},
    ]
    steps = macros.parse_steps(raw)
    assert steps == (
        Reply("Hi {{customer.first_name}}"),
        AddTag("refund"),
        SetPriority(Priority.low, keep_urgent=False),
        Note("check"),
        SetStatus(Status.resolved),
        SetTeam(team),
        AddTag("two"),
    )
    assert macros.parse_steps([macros.stored_step(s) for s in steps]) == steps
    assert macros.changes_ticket(steps)
    assert not macros.changes_ticket([Reply("x"), Note("y")])


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "reply",
        [{"kind": "reply"}],
        [{"kind": "note", "text": 5}],
        [{"kind": "set_type", "type_id": str(uuid4())}],
        [{"kind": "note", "text": "a"}, {"kind": "note", "text": "b"}],
        [{"kind": "set_status", "status": "open"}, {"kind": "set_status", "status": "pending"}],
        [{"kind": "add_tag", "tag": "x"}] * 21,
        [None],
    ],
)
def test_a_bad_macro_is_refused(raw: Any) -> None:
    with pytest.raises(MacroError):
        macros.parse_steps(raw)


def test_the_ticket_line_and_problems() -> None:
    assert (
        macros.line("Refund approved", "Neha", ["Reply sent", "Status Resolved"])
        == "Macro “Refund approved” run by Neha: Reply sent · Status Resolved"
    )
    assert macros.line("X", "Neha", []) == "Macro “X” run by Neha: nothing to change"
    team, gone = uuid4(), uuid4()
    assert macros.problems([SetTeam(team), Note("x")], {team: "Tech"}) == []
    assert macros.problems([SetTeam(gone)], {team: "Tech"}) == ["Its team was deleted."]
