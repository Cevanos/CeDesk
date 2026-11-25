"""Replay's pure rules (differentiator plan #3): changes, conflicts and who wins, limits,
the summary line."""

from dataclasses import replace
from uuid import UUID

import pytest

from cedesk.helpdesk.domain import replay as r
from cedesk.helpdesk.domain.rules import State
from cedesk.helpdesk.domain.tickets import Priority, Status

BILLING, SHIPPING = UUID(int=1), UUID(int=2)
NAMES = {"types": {}, "teams": {BILLING: "Billing", SHIPPING: "Shipping"}}
BEFORE = State(type_id=None, team_id=None, priority=Priority.medium, status=Status.open,
               tags=("a",))  # fmt: skip


def test_changes_list_each_field_with_names_and_added_tags() -> None:
    after = replace(BEFORE, priority=Priority.high, team_id=BILLING, tags=("a", "vip", "x"))
    found = [c.stored() for c in r.changes(BEFORE, after, NAMES)]
    assert found == [
        {"field": "priority", "before": "medium", "after": "high"},
        {"field": "team", "before": None, "after": "Billing"},
        {"field": "tags", "before": None, "after": "vip, x"},
    ]
    assert r.changes(BEFORE, BEFORE, NAMES) == []


def other(team: UUID | None, order: int, status: Status = Status.open) -> r.Other:
    after = replace(BEFORE, team_id=team, status=status)
    return r.Other(UUID(int=9), "Other", order, after)


def test_a_conflict_is_the_same_field_set_differently_and_the_later_one_wins() -> None:
    mine = replace(BEFORE, team_id=SHIPPING)
    found = r.clashes(BEFORE, mine, 5.5, [other(BILLING, 2)], NAMES)
    assert [c.stored()["winner"] for c in found] == ["tested"]
    assert (found[0].tested_value, found[0].other_value) == ("Shipping", "Billing")
    assert r.clashes(BEFORE, mine, 1, [other(BILLING, 2)], NAMES)[0].winner == "other"
    # Same value, a field the other leaves alone, or one the tested rule leaves alone: none.
    assert r.clashes(BEFORE, mine, 1, [other(SHIPPING, 2)], NAMES) == []
    assert r.clashes(BEFORE, mine, 1, [other(None, 2, Status.pending)], NAMES) == []
    assert r.clashes(BEFORE, BEFORE, 1, [other(BILLING, 2)], NAMES) == []


def test_the_limit_is_1_to_1000() -> None:
    assert r.check_limit(1) == 1 and r.check_limit(1000) == 1000
    for bad in (0, 1001):
        with pytest.raises(ValueError, match="1 to 1000"):
            r.check_limit(bad)


def test_the_summary_line() -> None:
    assert r.summary("running", 200, 500, 3, 0) == "Checked 200 of up to 500 tickets so far."
    assert r.summary("done", 500, 500, 12, 0) == "Would change 12 of 500 tickets."
    assert r.summary("done", 9, 500, 2, 1).endswith(", 1 conflicts with other rules.")
    assert r.summary("cancelled", 40, 500, 1, 0) == "Cancelled after 40 tickets."
    assert r.summary("failed", 0, 500, 0, 0) == "Stopped after 0 tickets."
