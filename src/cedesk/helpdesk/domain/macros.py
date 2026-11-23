"""Macros (md W18): one click runs a list of steps on a ticket, in order. No database here.

Stored as JSON, a list of steps:
    [{"kind": "reply", "text": "Hi {{customer.first_name}}, ..."},
     {"kind": "note", "text": "Check the payment reached the customer in 7 days."},
     {"kind": "set_status", "status": "resolved"},
     {"kind": "set_priority", "priority": "low"},
     {"kind": "set_type", "type_id": "...", "only_if_empty": false},
     {"kind": "set_team", "team_id": "..."},
     {"kind": "add_tag", "tag": "refund"}]

The change steps are the rule actions (`domain/rules.py`), so they behave the same way.
A reply's placeholders are filled when it runs, as for a typed reply. A macro has at most
one reply and one note, and each change kind once (tags may repeat).

The ticket gets one line: 'Macro “Refund approved” run by Neha: Reply sent · Tag refund ·
Priority Low · Note added · Status Resolved'.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain import rules
from cedesk.helpdesk.domain.rules import AddTag, SetPriority, SetStatus, SetTeam, SetType

MAX_MACROS = 100
MAX_STEPS = 20
NAME_MAX = 80
TEXT_MAX = 10_000


class MacroError(ValueError):
    pass


@dataclass(frozen=True)
class Reply:
    text: str


@dataclass(frozen=True)
class Note:
    text: str


Change = SetType | SetTeam | SetPriority | SetStatus | AddTag
Step = Reply | Note | Change

_CHANGE_KINDS = ("set_type", "set_team", "set_priority", "set_status", "add_tag")


def parse_step(raw: Mapping[str, Any]) -> Step:
    if not isinstance(raw, Mapping):
        raise MacroError("Each step is an object with a `kind`.")
    kind = raw.get("kind")
    if kind in ("reply", "note"):
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MacroError(f"A {kind} step needs its text.")
        text = text.strip()
        if len(text) > TEXT_MAX:
            raise MacroError(f"A {kind} is at most {TEXT_MAX} characters.")
        return Reply(text) if kind == "reply" else Note(text)
    if kind in _CHANGE_KINDS:
        try:
            action = rules.parse_action(raw)
        except rules.RuleError as exc:
            raise MacroError(str(exc)) from exc
        assert isinstance(action, rules.CHANGES)  # _CHANGE_KINDS are change kinds only
        return action
    raise MacroError(
        "A step kind is reply, note, set_status, set_priority, set_type, set_team or add_tag."
    )


def parse_steps(raw: Sequence[Any]) -> tuple[Step, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise MacroError("A macro needs at least one step.")
    if len(raw) > MAX_STEPS:
        raise MacroError(f"A macro has at most {MAX_STEPS} steps.")
    steps = tuple(parse_step(s) for s in raw)
    kinds = [type(s) for s in steps if not isinstance(s, AddTag)]
    if len(kinds) != len(set(kinds)):
        raise MacroError("Each kind of step appears once (only add_tag may repeat).")
    return steps


def stored_step(step: Step) -> dict[str, Any]:
    if isinstance(step, Reply):
        return {"kind": "reply", "text": step.text}
    if isinstance(step, Note):
        return {"kind": "note", "text": step.text}
    return rules.stored_action(step)


def changes_ticket(steps: Sequence[Step]) -> bool:
    """Whether running it needs the right to change fields."""
    return any(not isinstance(s, Reply | Note) for s in steps)


def line(name: str, who: str, done: Sequence[str]) -> str:
    what = " · ".join(done) if done else "nothing to change"
    return f"Macro “{name}” run by {who}: {what}"


def problems(
    steps: Sequence[Step], types: Mapping[UUID, str], teams: Mapping[UUID, str]
) -> list[str]:
    """What an Admin should fix: a type or team step whose type or team was deleted."""
    found = []
    for s in steps:
        if isinstance(s, SetType) and s.type_id not in types:
            found.append("Its ticket type was deleted.")
        if isinstance(s, SetTeam) and s.team_id not in teams:
            found.append("Its team was deleted.")
    return found
