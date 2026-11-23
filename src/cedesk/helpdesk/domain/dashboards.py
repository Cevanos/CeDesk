"""Dashboards (feature-gap plan 3.3): the rules that need no database.

A dashboard is a name and up to `MAX_CARDS` cards in order; a card is a saved report or a
built-in report. Personal dashboards are seen by their owner only; shared ones by everyone
who holds the reports right, and only roles with `share_views` make, change or delete them.
A shared dashboard may only hold built-in reports and *shared* saved reports, so everyone
who sees it can run every card.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain.report_defs import PRESETS

NAME_MAX = 80
MAX_CARDS = 20
MAX_PERSONAL = 20  # per member
MAX_SHARED = 50  # per workspace


class DashboardError(ValueError):
    """The message is shown to the member."""


@dataclass(frozen=True)
class CardRef:
    """A saved report (`report_id`) or a built-in one (`preset`): exactly one."""

    report_id: UUID | None = None
    preset: str | None = None


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise DashboardError(f"A dashboard's name is 1-{NAME_MAX} characters.")
    return name


def parse_cards(raw: Sequence[Mapping[str, Any]]) -> list[CardRef]:
    """The cards from JSON: each `{report_id}` or `{preset}`, at most `MAX_CARDS`."""
    if len(raw) > MAX_CARDS:
        raise DashboardError(f"A dashboard has at most {MAX_CARDS} cards.")
    cards = []
    for i, item in enumerate(raw, 1):
        unknown = set(item) - {"report_id", "preset"}
        if unknown or (("report_id" in item) == ("preset" in item)):
            raise DashboardError(f"Card {i}: give a report_id or a preset, and nothing else.")
        if "preset" in item:
            if item["preset"] not in PRESETS:
                raise DashboardError(f"Card {i}: no built-in report called {item['preset']}.")
            cards.append(CardRef(preset=str(item["preset"])))
        else:
            try:
                cards.append(CardRef(report_id=UUID(str(item["report_id"]))))
            except ValueError as exc:
                raise DashboardError(f"Card {i}: report_id must be an id.") from exc
    return cards
