"""Sandbox rules (md S10): a copy of the rules, macros and saved replies an Admin edits
and tries on sample tickets before deploying it over the live ones.

Items are plain dicts in their stored form, with the live item's `id` (None for an item
added in the sandbox). Comparing goes by id: an id the live list doesn't have (it was
deleted meanwhile) counts as added.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment, Status


class Section(StrEnum):
    rules = "rules"
    macros = "macros"
    saved_replies = "saved_replies"


FIELDS: Mapping[Section, tuple[str, ...]] = {
    Section.rules: ("name", "enabled", "event", "conditions", "actions"),
    Section.macros: ("name", "steps"),
    Section.saved_replies: ("shortcut", "body"),
}
ORDERED = frozenset({Section.rules, Section.macros})  # saved replies list by shortcut


def label(section: Section, item: Mapping[str, Any]) -> str:
    if section is Section.saved_replies:
        return f"/{item['shortcut']}"
    return str(item["name"])


def item(section: Section, row: Mapping[Any, Any]) -> dict[str, Any]:
    """A live row in the sandbox's form."""
    return {"id": str(row["id"]), **{f: row[f] for f in FIELDS[section]}}


@dataclass(frozen=True)
class Changed:
    id: str
    label: str
    changes: dict[str, list[Any]]  # field: [live, sandbox]


@dataclass(frozen=True)
class Diff:
    added: list[str] = field(default_factory=list)  # labels
    changed: list[Changed] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # labels
    order_changed: bool = False

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed or self.order_changed)


def diff(
    section: Section, live: Sequence[Mapping[str, Any]], box: Sequence[Mapping[str, Any]]
) -> Diff:
    live_by_id = {str(x["id"]): x for x in live}
    box_ids = {str(x["id"]) for x in box if x.get("id")}
    added, changed = [], []
    for x in box:
        old = live_by_id.get(str(x.get("id")))
        if old is None:
            added.append(label(section, x))
            continue
        delta = {f: [old[f], x[f]] for f in FIELDS[section] if old[f] != x[f]}
        if delta:
            changed.append(Changed(str(old["id"]), label(section, x), delta))
    removed = [label(section, x) for x in live if str(x["id"]) not in box_ids]
    # Deploying appends new items, so the order changes when kept items move or a new
    # one sits before a kept one.
    kept_live = [str(x["id"]) for x in live if str(x["id"]) in box_ids]
    is_new = [str(x.get("id")) not in live_by_id for x in box]
    kept_box = [str(x["id"]) for x, new in zip(box, is_new, strict=True) if not new]
    new_first = any(new and not later for i, new in enumerate(is_new) for later in is_new[i + 1 :])
    moved = section in ORDERED and (kept_live != kept_box or new_first)
    return Diff(added, changed, removed, moved)


def check_unique(section: Section, items: Sequence[Mapping[str, Any]]) -> str | None:
    """What's wrong with the list as a whole, or None."""
    ids = [str(x["id"]) for x in items if x.get("id")]
    if len(ids) != len(set(ids)):
        return "The same id is listed twice."
    key = "shortcut" if section is Section.saved_replies else "name"
    seen: set[str] = set()
    for x in items:
        k = str(x[key]).lower()
        if k in seen:
            return f"Two items are called {label(section, x)}."
        seen.add(k)
    return None


# Trying the rules on sample tickets


@dataclass(frozen=True)
class Sample:
    name: str
    text: str
    channel: Channel
    to_address: str | None = None
    sentiment: Sentiment | None = None
    priority: Priority = Priority.medium


SAMPLES = (
    Sample("Refund request by email", "Refund please: my parcel arrived broken",
           Channel.email, "billing@yourshop.com", Sentiment.negative),
    Sample("Angry chat: checkout down", "Your checkout is down and nothing works, fix it now!",
           Channel.chat, None, Sentiment.negative, Priority.high),
    Sample("Order status on the web form", "Where is my order 1234? It has been a week.",
           Channel.web, None, Sentiment.neutral),
    Sample("Invoice question by phone", "Please send a GST invoice for my last order",
           Channel.phone, None, None, Priority.low),
    Sample("Thanks from the portal", "Thanks a lot, all sorted!", Channel.portal,
           None, Sentiment.positive, Priority.low),
)  # fmt: skip


@dataclass(frozen=True)
class Outcome:
    lines: list[str]
    state: engine.State


def try_rules(
    rules: Sequence[engine.Rule],
    sample: Sample,
    *,
    types: Mapping[UUID, str],
    teams: Mapping[UUID, str],
) -> Outcome:
    """What the enabled `rules` would do to a new ticket like `sample`. Nothing is saved."""
    facts = engine.Facts(
        text=sample.text,
        channel=sample.channel,
        to_address=sample.to_address,
        sentiment=sample.sentiment,
    )
    state = engine.State(
        type_id=None, team_id=None, priority=sample.priority, status=Status.open, tags=()
    )
    final, applied = engine.run(rules, engine.Event.created, facts, state, types=types, teams=teams)
    return Outcome([engine.line(a) for a in applied], final)
