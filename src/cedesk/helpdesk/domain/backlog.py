"""Backlog over time (feature-gap plan 3.1): the rules that need no database.

Once a day the worker writes how many tickets are open, pending and past their SLA
deadline (`hd_backlog_snapshot`), for the whole workspace and for each team. The row for
day D is the state taken at the first pass after D ended, in the workspace's own time zone
(its SLA calendar's). History starts the day this shipped; a day the worker was down has no
row and is left as a gap.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

KEEP_DAYS = 3 * 365
"""Rows older than this are deleted: the longest plan history (`growth`: 3 years)."""

TOTAL = "total"
TEAM = "team"
NO_TEAM = "no_team"


def local_date(at: datetime, timezone: str) -> date:
    """The calendar day `at` falls on in `timezone` (an aware time)."""
    if at.tzinfo is None:
        raise ValueError("Times must be timezone-aware.")
    return at.astimezone(ZoneInfo(timezone)).date()


def day_to_capture(now: datetime, timezone: str) -> date:
    """The day a pass at `now` writes: yesterday in the workspace's time zone, the last day
    that has fully ended."""
    return local_date(now, timezone) - timedelta(days=1)


def keep_from(now: datetime, timezone: str) -> date:
    """The oldest day kept; earlier rows are pruned."""
    return local_date(now, timezone) - timedelta(days=KEEP_DAYS)


def days_covering(start: datetime, end: datetime, timezone: str) -> tuple[date, date]:
    """The first and last local day a report period [start, end) touches, both included."""
    first = local_date(start, timezone)
    last = local_date(end - timedelta(microseconds=1), timezone)
    return first, last


def utc_now() -> datetime:
    return datetime.now(UTC)
