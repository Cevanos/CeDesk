"""Team report rules (md R1, R2, R6) that need no database.

- The period is [from, to): tickets created in it count as created, tickets resolved in
  it as resolved, ratings given in it as CSAT. "Open" and "breaching" are as of now.
- Imported tickets (R6) stay out of the time metrics (first response, resolution, SLA
  met, breaching): their times come from the old help desk. They still count everywhere
  else, and their CSAT counts.
- Times are wall-clock minutes, as the customer waited; the SLA met % uses business
  hours, as the SLA pill does.
- Tickets merged into another are left out (the ticket they went into counts).
"""

from datetime import datetime, timedelta

IMPORT_SOURCES: tuple[str, ...] = (
    "intercom", "zendesk", "freshdesk", "gorgias", "helpscout", "hubspot", "zoho", "csv",
)  # fmt: skip
"""`hd_ticket.source` of an imported ticket (md M1's 8 sources); the importer sets it."""

HAPPY = 4  # a CSAT score of 4 or 5 is happy
DEFAULT_DAYS = 30
MAX_DAYS = 366


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


def minutes(seconds: float | None) -> float | None:
    return round(seconds / 60, 1) if seconds is not None else None


def percent(part: int, whole: int) -> float | None:
    """None when there is nothing to measure (no "0%" for an empty team)."""
    return round(100 * part / whole, 1) if whole else None
