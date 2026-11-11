"""Automation rules (md W15, W16, 6.3) and thank-you detection (W17). No database here.

A rule runs on one event:
- `created`: a new ticket, before auto-assignment, so a rule can pick the team.
- `customer_replied`: the customer wrote on an Open or Pending ticket. (Agent edits never
  run rules, so a rule can't undo what an agent just chose.)
- `sla_breached`: an open ticket's SLA clock ran out (the worker, once a minute; once per
  clock, with the breach alert), e.g. to raise the priority or move it to another team.

Every condition must match (AND); inside one condition, any listed value matches (OR).
Every enabled rule that matches runs, in order, and later rules see what earlier rules
changed. Each one that runs writes "Rule applied: <name> → <what it did>".

Stored as JSON:
    conditions: [{"kind": "text_contains_any", "words": ["refund"]},
                 {"kind": "channel_in", "channels": ["email"]},
                 {"kind": "to_address_contains", "text": "billing@"},
                 {"kind": "status_in", "statuses": ["pending"]},
                 {"kind": "sentiment_in", "sentiments": ["negative"]},
                 {"kind": "sla_in", "states": ["breached"]}]
`sla_in` reads the clock the ticket's SLA pill shows (first reply, then resolution):
on_track, close (under 25% left), breached or met. Not on `created` (the clock only just
started).
    actions:    [{"kind": "set_type", "type_id": "...", "only_if_empty": false},
                 {"kind": "set_team", "team_id": "..."},
                 {"kind": "set_priority", "priority": "high", "keep_urgent": true},
                 {"kind": "set_status", "status": "open"},
                 {"kind": "add_tag", "tag": "upset"}]
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain.sla import SlaState
from cedesk.helpdesk.domain.tickets import (
    MAX_TAGS,
    TAG,
    Channel,
    Priority,
    Sentiment,
    Status,
    title,
)

MAX_RULES = 100
MAX_PARTS = 10  # conditions, and actions, per rule
MAX_WORDS = 50
MAX_WORD = 100
NAME_MAX = 80

# md 6.4: a reply that is only a thank-you keeps a resolved ticket resolved. The whole
# message must be the thank-you: "thanks, but it is still broken" reopens the ticket.
_THANKS_LEAD = r"(?:(?:ok(?:ay)?|great|cool|perfect|got\s+it|done|noted|sure)[\s,.!]+)?"
_THANKS_CORE = (
    r"(?:thanks|thank\s*(?:you|u)|thanku|thx|tnx|tq|ty|many\s+thanks|"
    r"dhanyavaa?d|dhanyawaa?d|shukriya|shukria|धन्यवाद|शुक्रिया)"
)
_THANKS_TAIL = r"(?:[\s,]+(?:so\s+much|very\s+much|a\s+lot|again|sir|ma'?am|bro|ji))*"
# Emoji, also with a skin tone (U+1F3FB-1F3FF) or the emoji style mark (U+FE0F).
_EMOJI = "[🙏👍😊🙂☺❤\ufe0f\U0001f3fb-\U0001f3ff]"
_THANKS_END = rf"(?:[\s.,!]|:-?\)|:-?D|{_EMOJI})*"
THANKS_ONLY = re.compile(
    rf"^\s*{_THANKS_LEAD}{_THANKS_CORE}{_THANKS_TAIL}{_THANKS_END}$", re.IGNORECASE
)
_EMOJI_ONLY = re.compile(rf"^\s*{_EMOJI}(?:\s|{_EMOJI})*$")


def is_thanks_only(body: str) -> bool:
    return bool(THANKS_ONLY.match(body) or _EMOJI_ONLY.match(body))


class RuleError(ValueError):
    pass


class Event(StrEnum):
    created = "created"
    customer_replied = "customer_replied"
    sla_breached = "sla_breached"


# Conditions


@dataclass(frozen=True)
class TextContainsAny:
    words: tuple[str, ...]  # lower-case; matched anywhere in the subject + customer messages


@dataclass(frozen=True)
class ChannelIn:
    channels: frozenset[Channel]


@dataclass(frozen=True)
class ToAddressContains:
    text: str  # lower-case, e.g. "billing@"


@dataclass(frozen=True)
class StatusIn:
    statuses: frozenset[Status]


@dataclass(frozen=True)
class SentimentIn:
    sentiments: frozenset[Sentiment]


@dataclass(frozen=True)
class SlaIn:
    states: frozenset[SlaState]


Condition = TextContainsAny | ChannelIn | ToAddressContains | StatusIn | SentimentIn | SlaIn


# Actions


@dataclass(frozen=True)
class SetType:
    type_id: UUID
    only_if_empty: bool = False  # leave a type an earlier rule or person already set


@dataclass(frozen=True)
class SetTeam:
    team_id: UUID


@dataclass(frozen=True)
class SetPriority:
    priority: Priority
    keep_urgent: bool = False  # "not if Urgent": never lowers an Urgent ticket


@dataclass(frozen=True)
class SetStatus:
    status: Status


@dataclass(frozen=True)
class AddTag:
    tag: str


Action = SetType | SetTeam | SetPriority | SetStatus | AddTag


@dataclass(frozen=True)
class Rule:
    id: UUID
    name: str
    event: Event
    conditions: tuple[Condition, ...]
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Facts:
    """What conditions look at."""

    text: str  # subject + every customer message
    channel: Channel
    to_address: str | None  # the address the customer wrote to (email tickets)
    sentiment: Sentiment | None
    sla: SlaState | None = None  # the pill's clock; only worked out when a rule asks


@dataclass(frozen=True)
class State:
    """What actions change. Conditions on status see the current state."""

    type_id: UUID | None
    team_id: UUID | None
    priority: Priority
    status: Status
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Applied:
    rule: Rule
    done: tuple[str, ...]  # "Type Refund", "Team Billing", ... one per action that ran


# Reading and checking the stored JSON


def _list(raw: Mapping[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise RuleError(f"`{key}` must be a non-empty list.")
    return value


def _enums[E: StrEnum](raw: Mapping[str, Any], key: str, enum: type[E]) -> frozenset[E]:
    try:
        return frozenset(enum(v) for v in _list(raw, key))
    except ValueError as exc:
        allowed = ", ".join(e.value for e in enum)
        raise RuleError(f"`{key}` values must be from: {allowed}.") from exc


def _text(value: Any, what: str, max_len: int = MAX_WORD) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleError(f"{what} must be text.")
    text = " ".join(value.split()).lower()
    if len(text) > max_len:
        raise RuleError(f"{what} is at most {max_len} characters.")
    return text


def _uuid(value: Any, what: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise RuleError(f"{what} must be an id.") from exc


def _flag(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise RuleError(f"`{key}` is true or false.")
    return value


def parse_condition(raw: Mapping[str, Any]) -> Condition:
    kind = raw.get("kind")
    if kind == "text_contains_any":
        words = _list(raw, "words")
        if len(words) > MAX_WORDS:
            raise RuleError(f"At most {MAX_WORDS} words in one condition.")
        cleaned = tuple(dict.fromkeys(_text(w, "Each word") for w in words))
        return TextContainsAny(cleaned)
    if kind == "channel_in":
        return ChannelIn(_enums(raw, "channels", Channel))
    if kind == "to_address_contains":
        return ToAddressContains(_text(raw.get("text"), "The address text", 254))
    if kind == "status_in":
        return StatusIn(_enums(raw, "statuses", Status))
    if kind == "sentiment_in":
        return SentimentIn(_enums(raw, "sentiments", Sentiment))
    if kind == "sla_in":
        return SlaIn(_enums(raw, "states", SlaState))
    raise RuleError(
        "A condition kind is text_contains_any, channel_in, to_address_contains, "
        "status_in, sentiment_in or sla_in."
    )


def parse_action(raw: Mapping[str, Any]) -> Action:
    kind = raw.get("kind")
    if kind == "set_type":
        return SetType(_uuid(raw.get("type_id"), "type_id"), _flag(raw, "only_if_empty"))
    if kind == "set_team":
        return SetTeam(_uuid(raw.get("team_id"), "team_id"))
    if kind == "set_priority":
        try:
            priority = Priority(str(raw.get("priority")))
        except ValueError as exc:
            raise RuleError("priority is urgent, high, medium or low.") from exc
        return SetPriority(priority, _flag(raw, "keep_urgent"))
    if kind == "set_status":
        try:
            status = Status(str(raw.get("status")))
        except ValueError as exc:
            raise RuleError("status is open, pending or resolved.") from exc
        return SetStatus(status)
    if kind == "add_tag":
        tag = str(raw.get("tag") or "").strip().lower().replace(" ", "-")
        if not TAG.match(tag):
            raise RuleError("A tag is 1-40 letters, digits, - or _.")
        return AddTag(tag)
    raise RuleError("An action kind is set_type, set_team, set_priority, set_status or add_tag.")


def parse_rule(
    rule_id: UUID,
    name: str,
    event: str,
    conditions: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> Rule:
    try:
        when = Event(event)
    except ValueError as exc:
        raise RuleError("event is created, customer_replied or sla_breached.") from exc
    if not 1 <= len(conditions) <= MAX_PARTS:
        raise RuleError(f"A rule has 1-{MAX_PARTS} conditions.")
    if not 1 <= len(actions) <= MAX_PARTS:
        raise RuleError(f"A rule has 1-{MAX_PARTS} actions.")
    parsed_actions = tuple(parse_action(a) for a in actions)
    kinds = [type(a) for a in parsed_actions if not isinstance(a, AddTag)]
    if len(kinds) != len(set(kinds)):
        raise RuleError("Each action kind appears once per rule (add_tag may repeat).")
    parsed_conditions = tuple(parse_condition(c) for c in conditions)
    if when is Event.created and any(isinstance(c, SlaIn) for c in parsed_conditions):
        raise RuleError("A new ticket's SLA has only just started: use sla_in on a reply "
                        "or on sla_breached.")  # fmt: skip
    return Rule(rule_id, name.strip(), when, parsed_conditions, parsed_actions)


def needs_sla(rules: Sequence[Rule]) -> bool:
    """Whether any rule reads the SLA (worked out only then)."""
    return any(isinstance(c, SlaIn) for r in rules for c in r.conditions)


def stored_condition(c: Condition) -> dict[str, Any]:
    """The JSON kept in the database: checked and in one fixed shape."""
    if isinstance(c, TextContainsAny):
        return {"kind": "text_contains_any", "words": list(c.words)}
    if isinstance(c, ChannelIn):
        return {"kind": "channel_in", "channels": sorted(x.value for x in c.channels)}
    if isinstance(c, ToAddressContains):
        return {"kind": "to_address_contains", "text": c.text}
    if isinstance(c, StatusIn):
        return {"kind": "status_in", "statuses": sorted(x.value for x in c.statuses)}
    if isinstance(c, SlaIn):
        return {"kind": "sla_in", "states": sorted(x.value for x in c.states)}
    return {"kind": "sentiment_in", "sentiments": sorted(x.value for x in c.sentiments)}


def stored_action(a: Action) -> dict[str, Any]:
    if isinstance(a, SetType):
        return {"kind": "set_type", "type_id": str(a.type_id), "only_if_empty": a.only_if_empty}
    if isinstance(a, SetTeam):
        return {"kind": "set_team", "team_id": str(a.team_id)}
    if isinstance(a, SetPriority):
        return {"kind": "set_priority", "priority": a.priority.value, "keep_urgent": a.keep_urgent}
    if isinstance(a, SetStatus):
        return {"kind": "set_status", "status": a.status.value}
    return {"kind": "add_tag", "tag": a.tag}


# Running


def matches(condition: Condition, facts: Facts, state: State) -> bool:
    if isinstance(condition, TextContainsAny):
        text = facts.text.lower()
        return any(word in text for word in condition.words)
    if isinstance(condition, ChannelIn):
        return facts.channel in condition.channels
    if isinstance(condition, ToAddressContains):
        return facts.to_address is not None and condition.text in facts.to_address.lower()
    if isinstance(condition, StatusIn):
        return state.status in condition.statuses
    if isinstance(condition, SlaIn):
        return facts.sla is not None and facts.sla in condition.states
    return facts.sentiment is not None and facts.sentiment in condition.sentiments


def act(
    action: Action,
    state: State,
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
) -> tuple[State, str | None]:
    """The new state and what to say about it; None when the action could not run or
    changed nothing (the ticket already had that value)."""
    if isinstance(action, SetType):
        if action.type_id not in types or action.type_id == state.type_id:
            return state, None
        if action.only_if_empty and state.type_id is not None:
            return state, None
        return replace(state, type_id=action.type_id), f"Type {types[action.type_id]}"
    if isinstance(action, SetTeam):
        if action.team_id not in teams or action.team_id == state.team_id:
            return state, None
        return replace(state, team_id=action.team_id), f"Team {teams[action.team_id]}"
    if isinstance(action, SetPriority):
        if action.priority is state.priority:
            return state, None
        if action.keep_urgent and state.priority is Priority.urgent:
            return state, None
        return replace(state, priority=action.priority), f"Priority {title(action.priority)}"
    if isinstance(action, SetStatus):
        if action.status is state.status:
            return state, None
        return replace(state, status=action.status), f"Status {title(action.status)}"
    if action.tag in state.tags or len(state.tags) >= MAX_TAGS:
        return state, None
    return replace(state, tags=(*state.tags, action.tag)), f"Tag {action.tag}"


def run(
    rules: Sequence[Rule],
    event: Event,
    facts: Facts,
    state: State,
    *,
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
) -> tuple[State, list[Applied]]:
    """Runs the enabled rules (pass them in order) for one event. A rule whose type or
    team was deleted still runs its other actions. Returns the final state and, per rule
    that changed something, what it did."""
    applied: list[Applied] = []
    for rule in rules:
        if rule.event is not event:
            continue
        if not all(matches(c, facts, state) for c in rule.conditions):
            continue
        done: list[str] = []
        for action in rule.actions:
            state, said = act(action, state, types, teams)
            if said:
                done.append(said)
        if done:  # a rule that matched but found nothing to change writes no line
            applied.append(Applied(rule, tuple(done)))
    return state, applied


def line(applied: Applied) -> str:
    """The ticket's system line for one rule that ran."""
    what = " · ".join(applied.done) if applied.done else "nothing to change"
    return f"Rule applied: {applied.rule.name} → {what}"


def problems(rule: Rule, types: Mapping[UUID, str], teams: Mapping[UUID, str]) -> list[str]:
    """What an Admin should fix: actions that point at a deleted type or team."""
    found = []
    for action in rule.actions:
        if isinstance(action, SetType) and action.type_id not in types:
            found.append("Its ticket type was deleted.")
        if isinstance(action, SetTeam) and action.team_id not in teams:
            found.append("Its team was deleted.")
    return found
