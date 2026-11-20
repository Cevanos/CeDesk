"""Automation safeguards and the run log (feature-gap plan 5.5), the rules that need no
database.

Every rule or flow that matches a ticket makes one run, kept in the run log. Before it
runs, three checks can stop it (the run is logged as `blocked`, the ticket is not
touched):

1. **Loop depth.** An automation that emails the customer or snoozes the ticket can cause
   the next event (the customer's answer, often an auto-reply; the wake-up). Such a run is
   marked `outward`. An event on a ticket within `CHAIN_MINUTES` of an outward run is one
   level deeper than that run; past `MAX_DEPTH` levels nothing runs. Ticket creation and
   SLA breaches start at depth 1.
2. **Per ticket.** One rule or flow runs at most `TICKET_CAP` times on one ticket in 24
   hours.
3. **Per rule or flow.** At most its `max_runs_per_hour` and `max_runs_per_day` runs in
   the last hour and day, across all tickets (defaults 1000 and 10000; an Admin sets them
   per rule or flow).

Blocked runs do not count toward the limits. Finished runs are kept `KEEP_DAYS` days.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

MAX_DEPTH = 5
CHAIN_MINUTES = 10
TICKET_CAP = 20  # runs of one rule or flow on one ticket in 24 hours
DEFAULT_PER_HOUR = 1000
DEFAULT_PER_DAY = 10000
MAX_PER_HOUR = 100_000
MAX_PER_DAY = 1_000_000
KEEP_DAYS = 30
RESUME_BATCH = 100  # waiting runs resumed per workspace per pass
SENDER = "Automation"  # who an automatic email is from when the workspace has no name


class RunState(StrEnum):
    done = "done"
    waiting = "waiting"  # a flow run, on a wait step
    cancelled = "cancelled"
    blocked = "blocked"  # a safeguard stopped it; nothing was changed
    failed = "failed"


class LimitError(ValueError):
    """The message is for the Admin."""


def check_limits(per_hour: int, per_day: int) -> tuple[int, int]:
    if not 1 <= per_hour <= MAX_PER_HOUR:
        raise LimitError(f"max_runs_per_hour is 1-{MAX_PER_HOUR}.")
    if not 1 <= per_day <= MAX_PER_DAY:
        raise LimitError(f"max_runs_per_day is 1-{MAX_PER_DAY}.")
    if per_day < per_hour:
        raise LimitError("max_runs_per_day can't be lower than max_runs_per_hour.")
    return per_hour, per_day


def chain_since(now: datetime) -> datetime:
    return now - timedelta(minutes=CHAIN_MINUTES)


def depth(chained: bool, deepest_outward: int) -> int:
    """This event's depth: one below the deepest outward run that may have caused it."""
    return deepest_outward + 1 if chained else 1


@dataclass(frozen=True)
class Counts:
    on_ticket: int  # this rule or flow on this ticket, last 24 h
    last_hour: int
    last_day: int


def blocked_reason(*, at_depth: int, counts: Counts, per_hour: int, per_day: int) -> str | None:
    """Why the run may not go ahead, or None."""
    if at_depth > MAX_DEPTH:
        return (
            f"Loop protection: automations already answered each other {MAX_DEPTH} times "
            "on this ticket."
        )
    if counts.on_ticket >= TICKET_CAP:
        return f"Ran {TICKET_CAP} times on this ticket in 24 hours."
    if counts.last_hour >= per_hour:
        return f"Hourly limit reached ({per_hour} runs)."
    if counts.last_day >= per_day:
        return f"Daily limit reached ({per_day} runs)."
    return None


def summary(done: list[str] | tuple[str, ...]) -> str:
    """A run's result line: what it did."""
    return " · ".join(done) if done else "Nothing to change"
