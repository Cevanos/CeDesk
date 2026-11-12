"""Report rules (md R1-R4, R6, R8) that need no database.

- The period is [from, to): tickets created in it count as created, tickets resolved in
  it as resolved, ratings given in it as CSAT. "Open" and "breaching" are as of now.
- Imported tickets (R6) stay out of the time metrics (first response, resolution, SLA
  met, breaching): their times come from the old help desk. They still count everywhere
  else, and their CSAT counts.
- Times are wall-clock minutes, as the customer waited; the SLA met % uses business
  hours, as the SLA pill does.
- Tickets merged into another are left out (the ticket they went into counts).

More metrics (R3, R4):
- One-touch resolution: of the tickets resolved in the period, the share resolved with
  exactly one reply from a team member and never reopened. Chat history copied in from
  Cevanos has no CeDesk author, so it doesn't count as a reply.
- Reopen rate: of the tickets created in the period that were resolved at least once
  (resolved now, or reopened), the share reopened at least once.
- Imported tickets stay out of both: how many replies they got and whether they were
  reopened happened in the old help desk.
- Backlog: Open or Pending now, by how long ago it was created (`AGE_BUCKETS`).
- Channels, topics and sentiment: created in the period, and open now.
- Time logged: entries logged in the period, whoever logged them (the totals), and per
  agent; open tasks are as of now.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

IMPORT_SOURCES: tuple[str, ...] = (
    "intercom", "zendesk", "freshdesk", "gorgias", "helpscout", "hubspot", "zoho", "csv",
)  # fmt: skip
"""`hd_ticket.source` of an imported ticket (md M1's 8 sources); the importer sets it."""

HAPPY = 4  # a CSAT score of 4 or 5 is happy
DEFAULT_DAYS = 30
MAX_DAYS = 366
NOT_SET = "Not set"  # a ticket with no topic or sentiment yet (e.g. made before triage)


@dataclass(frozen=True)
class AgeBucket:
    key: str
    label: str
    under_hours: int | None  # None: the last bucket, no upper end


AGE_BUCKETS: tuple[AgeBucket, ...] = (
    AgeBucket("under_1h", "Under 1 hour", 1),
    AgeBucket("1h_4h", "1 to 4 hours", 4),
    AgeBucket("4h_24h", "4 to 24 hours", 24),
    AgeBucket("over_1d", "Over 1 day", None),
)
"""Backlog by age (md R4). Each bucket holds ages from the previous bound (inclusive) to
its own (exclusive): exactly 1 hour old is "1 to 4 hours"."""

GOVERNANCE_PATH = "/dashboard/governance"


def governance_url(app_url: str) -> str:
    """The Governance report in the Cevanos web app (md R5): Cevanos keeps it, CeDesk
    only links to it."""
    return app_url.rstrip("/") + GOVERNANCE_PATH


class BadPeriod(ValueError):
    pass


def period(
    start: datetime | None, end: datetime | None, now: datetime
) -> tuple[datetime, datetime]:
    """[start, end). Defaults: the last 30 days up to now."""
    end = end or now
    start = start or end - timedelta(days=DEFAULT_DAYS)
    if start.tzinfo is None or end.tzinfo is None:
        raise BadPeriod("from and to need a time zone, e.g. 2026-11-01T00:00:00+05:30.")
    if start >= end:
        raise BadPeriod("from must be before to.")
    if end - start > timedelta(days=MAX_DAYS):
        raise BadPeriod(f"A report covers at most {MAX_DAYS} days.")
    return start, end


def minutes(seconds: float | Decimal | None) -> float | None:
    """Postgres gives an average of seconds as a Decimal; reports show a float."""
    if seconds is None:
        return None
    return float(round(Decimal(seconds) / 60, 1))


def percent(part: int, whole: int) -> float | None:
    """None when there is nothing to measure (no "0%" for an empty team)."""
    return round(100 * part / whole, 1) if whole else None
