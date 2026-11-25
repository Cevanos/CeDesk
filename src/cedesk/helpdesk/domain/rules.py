"""Automation rules (md W15, W16, 6.3) and thank-you detection (W17). No database here.

A rule runs on one event:
- `created`: a new ticket, before auto-assignment, so a rule can pick the team.
- `customer_replied`: the customer wrote on an Open or Pending ticket. (Agent edits never
  run rules, so a rule can't undo what an agent just chose.)
- `sla_breached`: an open ticket's SLA clock ran out (the worker, once a minute; once per
  clock, with the breach alert), e.g. to raise the priority or move it to another team.
- `snooze_ended`: a snoozed ticket's time came and the worker reopened it (plan 5.1).

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
Plan 5.1 adds (the contact is the ticket's customer):
                 {"kind": "contact_field", "key": "plan", "op": "equals", "value": "gold"}
                    op: equals, not_equals, is_set, not_set, contains (text), more_than,
                    less_than (numbers and dates); the value is checked against the field
                 {"kind": "contact_vip", "value": true}
                 {"kind": "contact_in_segment", "segment_ids": ["..."], "negate": false}
                 {"kind": "time_since", "of": "customer_reply", "op": "more_than",
                  "minutes": 2880}  (of: customer_reply, agent_reply; none yet counts
                  from when the ticket was made)
                 {"kind": "snoozed", "value": true}
                 {"kind": "has_followers", "value": true}
    actions:    [{"kind": "set_type", "type_id": "...", "only_if_empty": false},
                 {"kind": "set_team", "team_id": "..."},
                 {"kind": "set_priority", "priority": "high", "keep_urgent": true},
                 {"kind": "set_status", "status": "open"},
                 {"kind": "add_tag", "tag": "upset"},
Plan 5.1 adds (each one's side effect is done by the service, in the same transaction):
                 {"kind": "snooze", "minutes": 1440}  (Pending until then; not on a
                  resolved ticket)
                 {"kind": "add_follower", "member_id": "..."}  (active, sees the ticket)
                 {"kind": "send_email", "body": "Hi {{customer.first_name}}, ..."}  (a
                  reply from the workspace, sent the way replies are)
                 {"kind": "call_webhook", "webhook_id": "..."}  (one of the workspace's
                  webhooks: signed, retried and logged like its other deliveries)]
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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
EMAIL_MAX = 5000  # an automatic email's text
MAX_MINUTES = 525_600  # a year: snooze and time_since
MAX_SEGMENTS = 10  # in one contact_in_segment condition
VALUE_MAX = 500  # a contact_field condition's value as text

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
    snooze_ended = "snooze_ended"


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


class FieldOp(StrEnum):
    equals = "equals"
    not_equals = "not_equals"
    is_set = "is_set"
    not_set = "not_set"
    contains = "contains"  # text: any case
    more_than = "more_than"  # numbers, and dates (later than)
    less_than = "less_than"


_VALUELESS = frozenset({FieldOp.is_set, FieldOp.not_set})


@dataclass(frozen=True)
class ContactFieldIs:
    """A contact field's value. The key and value are checked against the workspace's
    contact fields by the service when the rule is saved."""

    key: str
    op: FieldOp
    value: str | int | float | bool | None = None


@dataclass(frozen=True)
class ContactVip:
    value: bool


@dataclass(frozen=True)
class ContactInSegment:
    segment_ids: frozenset[UUID]  # in any of them (with negate: in none of them)
    negate: bool = False


class Since(StrEnum):
    customer_reply = "customer_reply"
    agent_reply = "agent_reply"


class Compare(StrEnum):
    more_than = "more_than"
    less_than = "less_than"


@dataclass(frozen=True)
class TimeSince:
    of: Since
    op: Compare
    minutes: int


@dataclass(frozen=True)
class Snoozed:
    value: bool


@dataclass(frozen=True)
class HasFollowers:
    value: bool


Condition = (
    TextContainsAny
    | ChannelIn
    | ToAddressContains
    | StatusIn
    | SentimentIn
    | SlaIn
    | ContactFieldIs
    | ContactVip
    | ContactInSegment
    | TimeSince
    | Snoozed
    | HasFollowers
)


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


Change = SetType | SetTeam | SetPriority | SetStatus | AddTag
CHANGES = (SetType, SetTeam, SetPriority, SetStatus, AddTag)  # what macros may use too


@dataclass(frozen=True)
class Snooze:
    minutes: int


@dataclass(frozen=True)
class AddFollower:
    member_id: UUID


@dataclass(frozen=True)
class SendEmail:
    body: str  # placeholders filled when it is sent


@dataclass(frozen=True)
class CallWebhook:
    webhook_id: UUID


Action = Change | Snooze | AddFollower | SendEmail | CallWebhook
REPEATABLE = (AddTag, AddFollower)  # may appear more than once in a rule

# What an action asks the service to do besides changing the ticket's fields.
Effect = Snooze | AddFollower | SendEmail | CallWebhook
OUTWARD = (SendEmail, Snooze)  # can make the next event (a customer's answer, a wake-up)


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
    # Plan 5.1. The contact's: VIP, attribute values, and which of the segments the rules
    # name it is in (only those are worked out).
    contact_vip: bool = False
    attributes: Mapping[str, object] = field(default_factory=dict)
    segments: frozenset[UUID] = frozenset()
    now: datetime | None = None
    created_at: datetime | None = None  # the ticket's
    last_customer_at: datetime | None = None
    last_agent_at: datetime | None = None


@dataclass(frozen=True)
class Member:
    """Someone an automation may add as a follower: active. `only_team` is set when their
    role sees only one team's tickets."""

    name: str
    only_team: UUID | None = None


@dataclass(frozen=True)
class Targets:
    """Names of what actions point at (for the lines), and only what exists now: ticket
    types, teams, active members, turned-on webhooks (by a label that is not the URL)."""

    types: Mapping[UUID, str] = field(default_factory=dict)
    teams: Mapping[UUID, str] = field(default_factory=dict)
    members: Mapping[UUID, Member] = field(default_factory=dict)
    webhooks: Mapping[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class State:
    """What actions change. Conditions on status see the current state."""

    type_id: UUID | None
    team_id: UUID | None
    priority: Priority
    status: Status
    tags: tuple[str, ...]
    snoozed: bool = False
    followers: frozenset[UUID] = frozenset()
    effects: tuple[Effect, ...] = ()  # for the service to do, in order


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
    if kind == "contact_field":
        return _contact_field(raw)
    if kind == "contact_vip":
        return ContactVip(_bool(raw, "value"))
    if kind == "contact_in_segment":
        ids = _list(raw, "segment_ids")
        if len(ids) > MAX_SEGMENTS:
            raise RuleError(f"At most {MAX_SEGMENTS} segments in one condition.")
        return ContactInSegment(
            frozenset(_uuid(v, "Each segment id") for v in ids), _flag(raw, "negate")
        )
    if kind == "time_since":
        try:
            of = Since(str(raw.get("of")))
        except ValueError as exc:
            raise RuleError("`of` is customer_reply or agent_reply.") from exc
        try:
            op = Compare(str(raw.get("op")))
        except ValueError as exc:
            raise RuleError("`op` is more_than or less_than.") from exc
        return TimeSince(of, op, _minutes(raw))
    if kind == "snoozed":
        return Snoozed(_bool(raw, "value"))
    if kind == "has_followers":
        return HasFollowers(_bool(raw, "value"))
    raise RuleError(f"A condition kind is one of: {', '.join(CONDITION_KINDS)}.")


CONDITION_KINDS = (
    "text_contains_any", "channel_in", "to_address_contains", "status_in", "sentiment_in",
    "sla_in", "contact_field", "contact_vip", "contact_in_segment", "time_since", "snoozed",
    "has_followers",
)  # fmt: skip
ACTION_KINDS = (
    "set_type", "set_team", "set_priority", "set_status", "add_tag", "snooze", "add_follower",
    "send_email", "call_webhook",
)  # fmt: skip


def _bool(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise RuleError(f"`{key}` is true or false.")
    return value


def _minutes(raw: Mapping[str, Any]) -> int:
    value = raw.get("minutes")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_MINUTES:
        raise RuleError(f"`minutes` is a whole number from 1 to {MAX_MINUTES}.")
    return value


def _contact_field(raw: Mapping[str, Any]) -> ContactFieldIs:
    key = raw.get("key")
    if not isinstance(key, str) or not re.match(r"^[a-z][a-z0-9_]{0,39}$", key):
        raise RuleError("`key` is a contact field's key.")
    try:
        op = FieldOp(str(raw.get("op")))
    except ValueError as exc:
        raise RuleError(f"`op` is one of: {', '.join(o.value for o in FieldOp)}.") from exc
    value = raw.get("value")
    if op in _VALUELESS:
        return ContactFieldIs(key, op)
    if value is None or isinstance(value, list | dict) or value == "":
        raise RuleError(f"A contact_field condition with {op.value} needs a value.")
    if isinstance(value, str):
        value = value.strip()
        if len(value) > VALUE_MAX:
            raise RuleError(f"A value is at most {VALUE_MAX} characters.")
    if op is FieldOp.contains and not isinstance(value, str):
        raise RuleError("contains takes text.")
    if op in (FieldOp.more_than, FieldOp.less_than) and (
        isinstance(value, bool) or not isinstance(value, int | float | str)
    ):
        raise RuleError(f"{op.value} takes a number or a date.")
    return ContactFieldIs(key, op, value)


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
    if kind == "snooze":
        return Snooze(_minutes(raw))
    if kind == "add_follower":
        return AddFollower(_uuid(raw.get("member_id"), "member_id"))
    if kind == "send_email":
        body = raw.get("body")
        if not isinstance(body, str) or not body.strip():
            raise RuleError("send_email needs its text (`body`).")
        if len(body.strip()) > EMAIL_MAX:
            raise RuleError(f"An automatic email is at most {EMAIL_MAX} characters.")
        return SendEmail(body.strip())
    if kind == "call_webhook":
        return CallWebhook(_uuid(raw.get("webhook_id"), "webhook_id"))
    raise RuleError(f"An action kind is one of: {', '.join(ACTION_KINDS)}.")


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
        raise RuleError(
            "event is created, customer_replied, sla_breached or snooze_ended."
        ) from exc
    if not 1 <= len(conditions) <= MAX_PARTS:
        raise RuleError(f"A rule has 1-{MAX_PARTS} conditions.")
    if not 1 <= len(actions) <= MAX_PARTS:
        raise RuleError(f"A rule has 1-{MAX_PARTS} actions.")
    parsed_actions = tuple(parse_action(a) for a in actions)
    kinds = [type(a) for a in parsed_actions if not isinstance(a, REPEATABLE)]
    if len(kinds) != len(set(kinds)):
        raise RuleError(
            "Each action kind appears once per rule (add_tag and add_follower may repeat)."
        )
    parsed_conditions = tuple(parse_condition(c) for c in conditions)
    check_conditions(when, parsed_conditions)
    return Rule(rule_id, name.strip(), when, parsed_conditions, parsed_actions)


def check_conditions(when: Event, conditions: Sequence[Condition]) -> None:
    """What can't be asked on this event (a flow's condition steps too)."""
    if when is Event.created and any(isinstance(c, SlaIn) for c in conditions):
        raise RuleError("A new ticket's SLA has only just started: use sla_in on a reply "
                        "or on sla_breached.")  # fmt: skip


def needs_sla(rules: Sequence[Rule]) -> bool:
    """Whether any rule reads the SLA (worked out only then)."""
    return any(isinstance(c, SlaIn) for r in rules for c in r.conditions)


def segments_named(conditions: Sequence[Condition]) -> frozenset[UUID]:
    """The segments these conditions ask about (only those are worked out)."""
    return frozenset(
        s for c in conditions if isinstance(c, ContactInSegment) for s in c.segment_ids
    )


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
    if isinstance(c, ContactFieldIs):
        out: dict[str, Any] = {"kind": "contact_field", "key": c.key, "op": c.op.value}
        if c.op not in _VALUELESS:
            out["value"] = c.value
        return out
    if isinstance(c, ContactVip):
        return {"kind": "contact_vip", "value": c.value}
    if isinstance(c, ContactInSegment):
        return {
            "kind": "contact_in_segment",
            "segment_ids": sorted(str(x) for x in c.segment_ids),
            "negate": c.negate,
        }
    if isinstance(c, TimeSince):
        return {"kind": "time_since", "of": c.of.value, "op": c.op.value, "minutes": c.minutes}
    if isinstance(c, Snoozed):
        return {"kind": "snoozed", "value": c.value}
    if isinstance(c, HasFollowers):
        return {"kind": "has_followers", "value": c.value}
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
    if isinstance(a, Snooze):
        return {"kind": "snooze", "minutes": a.minutes}
    if isinstance(a, AddFollower):
        return {"kind": "add_follower", "member_id": str(a.member_id)}
    if isinstance(a, SendEmail):
        return {"kind": "send_email", "body": a.body}
    if isinstance(a, CallWebhook):
        return {"kind": "call_webhook", "webhook_id": str(a.webhook_id)}
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
    if isinstance(condition, ContactFieldIs):
        return field_matches(condition, facts.attributes.get(condition.key))
    if isinstance(condition, ContactVip):
        return facts.contact_vip is condition.value
    if isinstance(condition, ContactInSegment):
        inside = bool(condition.segment_ids & facts.segments)
        return inside is not condition.negate
    if isinstance(condition, TimeSince):
        return _since_matches(condition, facts)
    if isinstance(condition, Snoozed):
        return state.snoozed is condition.value
    if isinstance(condition, HasFollowers):
        return bool(state.followers) is condition.value
    return facts.sentiment is not None and facts.sentiment in condition.sentiments


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def field_matches(c: ContactFieldIs, value: object) -> bool:
    """A missing or empty value matches only not_set (and not_equals)."""
    empty = value is None or value == ""
    if c.op is FieldOp.is_set:
        return not empty
    if c.op is FieldOp.not_set:
        return empty
    if c.op is FieldOp.not_equals:
        return empty or not _same(value, c.value)
    if empty:
        return False
    if c.op is FieldOp.equals:
        return _same(value, c.value)
    if c.op is FieldOp.contains:
        return str(c.value).lower() in str(value).lower()
    # more_than / less_than: dates (YYYY-MM-DD) compare as text, numbers as numbers.
    if isinstance(value, str) and isinstance(c.value, str) and _ISO_DAY.match(value):
        return value > c.value if c.op is FieldOp.more_than else value < c.value
    mine, theirs = _number(value), _number(c.value)
    if mine is None or theirs is None:
        return False
    return mine > theirs if c.op is FieldOp.more_than else mine < theirs


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _same(value: object, wanted: object) -> bool:
    if isinstance(value, bool) or isinstance(wanted, bool):
        return value is wanted
    if isinstance(value, str) and isinstance(wanted, str):
        return value.lower() == wanted.lower()
    mine, theirs = _number(value), _number(wanted)
    return mine is not None and mine == theirs


def _since_matches(c: TimeSince, facts: Facts) -> bool:
    last = facts.last_customer_at if c.of is Since.customer_reply else facts.last_agent_at
    last = last or facts.created_at
    if last is None or facts.now is None:
        return False
    gone = facts.now - last
    limit = timedelta(minutes=c.minutes)
    return gone > limit if c.op is Compare.more_than else gone < limit


def act(
    action: Action,
    state: State,
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
    targets: Targets | None = None,
) -> tuple[State, str | None]:
    """The new state and what to say about it; None when the action could not run or
    changed nothing (the ticket already had that value). The plan 5.1 actions add an
    effect for the service to carry out."""
    if isinstance(action, Snooze | AddFollower | SendEmail | CallWebhook):
        return _effect(action, state, targets or Targets())
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


def minutes_text(minutes: int) -> str:
    """`90` -> `1 h 30 min`, `2880` -> `2 days`."""
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} h")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts)


def _effect(
    action: Snooze | AddFollower | SendEmail | CallWebhook, state: State, targets: Targets
) -> tuple[State, str | None]:
    if isinstance(action, Snooze):
        if state.status is Status.resolved:
            return state, None  # a resolved ticket can't be snoozed
        changed = replace(
            state, status=Status.pending, snoozed=True, effects=(*state.effects, action)
        )
        return changed, f"Snoozed for {minutes_text(action.minutes)}"
    if isinstance(action, AddFollower):
        member = targets.members.get(action.member_id)
        if member is None or action.member_id in state.followers:
            return state, None
        if member.only_team is not None and member.only_team != state.team_id:
            return state, None  # their role can't see this ticket
        changed = replace(
            state,
            followers=state.followers | {action.member_id},
            effects=(*state.effects, action),
        )
        return changed, f"Follower {member.name}"
    if isinstance(action, SendEmail):
        return replace(state, effects=(*state.effects, action)), "Email to the customer"
    label = targets.webhooks.get(action.webhook_id)
    if label is None:
        return state, None  # deleted or turned off
    return replace(state, effects=(*state.effects, action)), f"Webhook {label}"


def matched(rule: Rule, facts: Facts, state: State) -> bool:
    return all(matches(c, facts, state) for c in rule.conditions)


def apply_actions(
    actions: Sequence[Action], state: State, targets: Targets
) -> tuple[State, list[str]]:
    """Every action in order; what each one that did something says."""
    done: list[str] = []
    for action in actions:
        state, said = act(action, state, targets.types, targets.teams, targets)
        if said:
            done.append(said)
    return state, done


@dataclass(frozen=True)
class Explained:
    """Test mode (plan 5.5): each condition and whether it matched, then what each action
    would say (None: it would change nothing)."""

    conditions: list[tuple[dict[str, Any], bool, str]]  # condition, matched, why
    matched: bool
    actions: list[tuple[dict[str, Any], str | None]]
    state: State


def _name(value: object) -> str:
    return str(getattr(value, "value", value))


def _span(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 2880:
        return f"{minutes // 60} h"
    return f"{minutes // 1440} days"


def why(condition: Condition, facts: Facts, state: State) -> str:
    """What the ticket actually has, in a few plain words, for test mode ("the ticket is
    not snoozed"). Says the fact, not the verdict, so it reads right for a pass or a fail."""
    if isinstance(condition, TextContainsAny):
        hit = any(word in facts.text.lower() for word in condition.words)
        return "the message has one of the words" if hit else "the message has none of the words"
    if isinstance(condition, ChannelIn):
        return f"the channel is {_name(facts.channel)}"
    if isinstance(condition, ToAddressContains):
        return f"the customer wrote to {facts.to_address or 'no address'}"
    if isinstance(condition, StatusIn):
        return f"the ticket is {_name(state.status)}"
    if isinstance(condition, SlaIn):
        return f"the SLA is {_name(facts.sla) if facts.sla is not None else 'not running'}"
    if isinstance(condition, ContactFieldIs):
        value = facts.attributes.get(condition.key)
        shown = "empty" if value is None or value == "" else str(value)
        return f"the customer's {condition.key} is {shown}"
    if isinstance(condition, ContactVip):
        return "the customer is VIP" if facts.contact_vip else "the customer is not VIP"
    if isinstance(condition, ContactInSegment):
        inside = bool(condition.segment_ids & facts.segments)
        return "the customer is in those segments" if inside else "the customer is not in them"
    if isinstance(condition, TimeSince):
        last = (
            facts.last_customer_at if condition.of is Since.customer_reply else facts.last_agent_at
        )
        last = last or facts.created_at
        who = "customer" if condition.of is Since.customer_reply else "agent"
        if last is None or facts.now is None:
            return f"there is no {who} reply time to count from"
        return f"{_span(facts.now - last)} since the last {who} reply"
    if isinstance(condition, Snoozed):
        return "the ticket is snoozed" if state.snoozed else "the ticket is not snoozed"
    if isinstance(condition, HasFollowers):
        return "the ticket has followers" if state.followers else "the ticket has no followers"
    shown = _name(facts.sentiment) if facts.sentiment is not None else "unknown"
    return f"the sentiment is {shown}"


def explain(rule: Rule, facts: Facts, state: State, targets: Targets) -> Explained:
    checks = [
        (stored_condition(c), matches(c, facts, state), why(c, facts, state))
        for c in rule.conditions
    ]
    ok = all(m for _, m, _ in checks)
    would: list[tuple[dict[str, Any], str | None]] = []
    if ok:
        for action in rule.actions:
            state, said = act(action, state, targets.types, targets.teams, targets)
            would.append((stored_action(action), said))
    return Explained(checks, ok, would, state)


def run(
    rules: Sequence[Rule],
    event: Event,
    facts: Facts,
    state: State,
    *,
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
    targets: Targets | None = None,
) -> tuple[State, list[Applied]]:
    """Runs the enabled rules (pass them in order) for one event. A rule whose type or
    team was deleted still runs its other actions. Returns the final state and, per rule
    that changed something, what it did. (The service runs rules one by one, with the
    safeguards between them; the sandbox uses this.)"""
    targets = targets or Targets(types, teams)
    applied: list[Applied] = []
    for rule in rules:
        if rule.event is not event:
            continue
        if not matched(rule, facts, state):
            continue
        state, done = apply_actions(rule.actions, state, targets)
        if done:  # a rule that matched but found nothing to change writes no line
            applied.append(Applied(rule, tuple(done)))
    return state, applied


def line(applied: Applied) -> str:
    """The ticket's system line for one rule that ran."""
    what = " · ".join(applied.done) if applied.done else "nothing to change"
    return f"Rule applied: {applied.rule.name} → {what}"


def problems(
    rule: Rule,
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
    known: "Known | None" = None,
) -> list[str]:
    """What an Admin should fix: actions that point at a deleted type or team (and,
    given `known`, a member, webhook, segment or contact field that is gone)."""
    return action_problems(rule.actions, types, teams, known) + (
        condition_problems(rule.conditions, known) if known else []
    )


@dataclass(frozen=True)
class Known:
    """What exists now, for `problems`: active members, webhooks (on or off: an Admin
    may turn one off for a while), segments and contact fields by key."""

    members: frozenset[UUID] = frozenset()
    webhooks: frozenset[UUID] = frozenset()
    segments: frozenset[UUID] = frozenset()
    fields: frozenset[str] = frozenset()


def action_problems(
    actions: Sequence[Action],
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
    known: Known | None = None,
) -> list[str]:
    found = []
    for action in actions:
        if isinstance(action, SetType) and action.type_id not in types:
            found.append("Its ticket type was deleted.")
        if isinstance(action, SetTeam) and action.team_id not in teams:
            found.append("Its team was deleted.")
        if known is None:
            continue
        if isinstance(action, AddFollower) and action.member_id not in known.members:
            found.append("The follower it adds was removed or deactivated.")
        if isinstance(action, CallWebhook) and action.webhook_id not in known.webhooks:
            found.append("Its webhook was deleted.")
    return found


def condition_problems(conditions: Sequence[Condition], known: Known | None) -> list[str]:
    if known is None:
        return []
    found = []
    for c in conditions:
        if isinstance(c, ContactInSegment) and not c.segment_ids <= known.segments:
            found.append("A segment it checks was deleted.")
        if isinstance(c, ContactFieldIs) and c.key not in known.fields:
            found.append(f"The contact field {c.key} was deleted.")
    return found
