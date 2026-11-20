"""Snooze (feature-gap plan 1.1), the rules that need no database.

- Snoozing puts a ticket in Pending until a time: at least a minute ahead, at most a
  year. A resolved ticket can't be snoozed (reopen it first).
- At that time the worker (once a minute) sets it back to Open and tells the assignee
  (notification `snooze_ended`) and the followers. A customer reply wakes it early.
- Setting another status by hand ends the snooze; so does anything else that changes the
  status (a rule, a merge). The database clears it whatever path made the change.
"""

from datetime import UTC, datetime, timedelta

MIN_AHEAD = timedelta(minutes=1)
MAX_AHEAD = timedelta(days=365)
WAKE_BATCH = 200  # tickets per workspace per pass; the rest come next minute
NOTIFICATION = "snooze_ended"


class SnoozeError(ValueError):
    """The message is shown to the agent."""


def check_until(until: datetime, now: datetime) -> datetime:
    if until.tzinfo is None:
        raise SnoozeError("until needs a time zone (for example 2026-11-21T09:00:00Z).")
    if until < now + MIN_AHEAD:
        raise SnoozeError("Snooze until a time at least a minute from now.")
    if until > now + MAX_AHEAD:
        raise SnoozeError("A ticket can be snoozed for a year at most.")
    return until


def when(until: datetime) -> str:
    """How a system line shows the time: `21 Nov 2026 09:00 UTC`."""
    return until.astimezone(UTC).strftime("%d %b %Y %H:%M UTC")


def snoozed_line(until: datetime, who: str) -> str:
    return f"Snoozed until {when(until)} by {who}"


def unsnoozed_line(who: str) -> str:
    return f"Snooze ended by {who} · ticket reopened"


CLEARED_LINE = "Snooze cleared (status changed)"
WOKE_LINE = "Snooze ended · ticket reopened"
CUSTOMER_WOKE_LINE = "Customer replied · snooze ended"


def woke_note(ticket_label: str, subject: str) -> str:
    return f"{ticket_label} is back from snooze: {subject}"[:500]
