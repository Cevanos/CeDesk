"""The live view (feature-gap plan 3.4): the rules that need no database.

A snapshot of the queue as of now: how many tickets are open, pending and snoozed (per
team), which tickets have waited longest for a first reply, which are due (or past due)
on their SLA clock soon, and how many agents are in each status.

- **Waiting** means open, not snoozed and with no agent reply yet, oldest first. (Waiting
  for the *next* reply isn't tracked: there is no column for it.)
- **At risk** means the SLA deadline falls within `window_minutes`, not the SLA pill's
  "close" state (a quarter of the target left, in business time, worked out per ticket in
  code): this one is a plain time window the database can serve from an index.
- **Online** means the status the agent set; there is no heartbeat.
- Tickets from another help desk never count as waiting, at risk or breached (their times
  are not ours); merged tickets are left out, as in every report.
"""

from datetime import timedelta

DEFAULT_WINDOW_MINUTES = 60
MIN_WINDOW_MINUTES = 5
MAX_WINDOW_MINUTES = 24 * 60
WAITING_LIMIT = 50  # the longest-waiting tickets listed
DUE_LIMIT = 20  # the soonest-due tickets listed
CACHE_TTL = timedelta(seconds=5)
"""Everyone looking at the same scope shares one answer for this long, so many open pages
don't each run the queries."""

STATUSES = ("available", "on_break", "transfers_only", "offline")


class LiveError(ValueError):
    """The message is shown to the member."""


def check_window(minutes: int) -> int:
    if not MIN_WINDOW_MINUTES <= minutes <= MAX_WINDOW_MINUTES:
        raise LiveError(
            f"window_minutes is {MIN_WINDOW_MINUTES} to {MAX_WINDOW_MINUTES} (one day)."
        )
    return minutes
