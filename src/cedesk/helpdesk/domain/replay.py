"""Replay: a rule or workflow tried on past tickets (differentiator plan #3). Pure rules:
what counts as a change, what counts as a conflict, and who wins one.

- A **change** is a field the rule (or workflow) would leave different on the ticket:
  status, priority, type, team, or tags it would add. Effects (an email, a webhook, a
  follower, a snooze) are listed by what they would say, never done.
- A **conflict** is another enabled rule for the same event, tried on the same ticket as
  it is, setting one of status, priority, type or team to a different value. Rules run in
  their order, then workflows; on the same field the later one wins.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain.rules import State

LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 1, 1000, 500
PAGE = 100  # tickets per worker page; counts are saved after each
SAMPLES_MAX = 200  # tickets it would change, and conflicts, kept to show
KEEP_DAYS = 30
LEASE_SECONDS = 300  # a running replay no page touched for this long is picked up again
STATES = ("queued", "running", "done", "failed", "cancelled")
ACTIVE = ("queued", "running")

FIELDS = ("status", "priority", "type", "team")  # what two rules can disagree on


@dataclass(frozen=True)
class Change:
    field: str  # status | priority | type | team | tags
    before: str | None
    after: str | None

    def stored(self) -> dict[str, Any]:
        return {"field": self.field, "before": self.before, "after": self.after}


def _value(state: State, field: str, names: Mapping[str, Mapping[UUID, str]]) -> str | None:
    if field == "status":
        return state.status.value
    if field == "priority":
        return state.priority.value
    if field == "type":
        return names["types"].get(state.type_id) if state.type_id else None
    return names["teams"].get(state.team_id) if state.team_id else None


def _raw(state: State, field: str) -> object:
    return {
        "status": state.status, "priority": state.priority,
        "type": state.type_id, "team": state.team_id,
    }[field]  # fmt: skip


def changes(before: State, after: State, names: Mapping[str, Mapping[UUID, str]]) -> list[Change]:
    """What the ticket would look like differently. `names`: {"types": ..., "teams": ...}
    for type and team names."""
    out = [
        Change(f, _value(before, f, names), _value(after, f, names))
        for f in FIELDS
        if _raw(before, f) != _raw(after, f)
    ]
    added = [t for t in after.tags if t not in before.tags]
    if added:
        out.append(Change("tags", None, ", ".join(added)))
    return out


@dataclass(frozen=True)
class Other:
    """Another enabled rule, tried on the same ticket as it is."""

    rule_id: UUID
    name: str
    order: int  # its place in run order
    after: State


@dataclass(frozen=True)
class Clash:
    field: str
    tested_value: str | None
    other_rule_id: UUID
    other_rule: str
    other_value: str | None
    winner: str  # "tested" or "other": the one that runs later

    def stored(self) -> dict[str, Any]:
        return {
            "field": self.field, "tested_value": self.tested_value,
            "other_rule_id": str(self.other_rule_id), "other_rule": self.other_rule,
            "other_value": self.other_value, "winner": self.winner,
        }  # fmt: skip


def clashes(
    before: State,
    tested_after: State,
    tested_order: float,
    others: Sequence[Other],
    names: Mapping[str, Mapping[UUID, str]],
) -> list[Clash]:
    """Where another rule sets a field the tested one sets, to something else.
    `tested_order`: its place in run order (a draft rule goes last; a workflow runs after
    every rule)."""
    out: list[Clash] = []
    for f in FIELDS:
        mine = _raw(tested_after, f)
        if mine == _raw(before, f):
            continue
        for other in others:
            theirs = _raw(other.after, f)
            if theirs == _raw(before, f) or theirs == mine:
                continue
            out.append(
                Clash(
                    f, _value(tested_after, f, names), other.rule_id, other.name,
                    _value(other.after, f, names),
                    "tested" if tested_order > other.order else "other",
                )
            )  # fmt: skip
    return out


def check_limit(limit: int) -> int:
    if not LIMIT_MIN <= limit <= LIMIT_MAX:
        raise ValueError(f"Replay {LIMIT_MIN} to {LIMIT_MAX} tickets.")
    return limit


def summary(state: str, checked: int, limit: int, would_change: int, conflicts: int) -> str:
    """One line for the screen."""
    if state in ACTIVE:
        return f"Checked {checked} of up to {limit} tickets so far."
    if state == "cancelled":
        return f"Cancelled after {checked} tickets."
    if state == "failed":
        return f"Stopped after {checked} tickets."
    tail = f", {conflicts} conflicts with other rules" if conflicts else ""
    return f"Would change {would_change} of {checked} tickets{tail}."
