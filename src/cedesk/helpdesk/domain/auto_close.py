"""Auto-close inactive tickets (feature-gap plan 1.4), the rules that need no database.

- Off by default. An Admin turns it on with a number of days (1-365) and, if they want,
  a notice for the customer (up to 5,000 characters).
- Once a day the worker resolves each Pending ticket that has had no reply from the
  customer, and no other change, for that many days. The notice, when set, is added as
  a reply first, so the customer gets it the way they get any reply (email, portal or
  chat). Each close has a line on the ticket and an audit entry.
- Never closed: snoozed tickets (they wake up on their own), a chat handover still
  waiting for Accept, tickets with an approval still waiting for a decision, tickets
  with an open child ticket, merged tickets, and tickets brought in by an import (their
  history is someone else's; closing hundreds of them on the first night would surprise
  everyone, and the customers would get notices about old conversations).
"""

MIN_DAYS = 1
MAX_DAYS = 365
DEFAULT_DAYS = 7
NOTICE_MAX = 5000
BATCH = 200  # tickets per transaction; a workspace's pass loops until none are left


class AutoCloseError(ValueError):
    """The message is shown to the Admin."""


def check(after_days: int, notice: str | None) -> tuple[int, str | None]:
    if not MIN_DAYS <= after_days <= MAX_DAYS:
        raise AutoCloseError(f"after_days is {MIN_DAYS} to {MAX_DAYS}.")
    text = (notice or "").strip() or None
    if text is not None and len(text) > NOTICE_MAX:
        raise AutoCloseError(f"The notice is at most {NOTICE_MAX} characters.")
    return after_days, text


def closed_line(days: int) -> str:
    unit = "day" if days == 1 else "days"
    return f"Resolved automatically: no reply from the customer for {days} {unit}"


def follower_note(ticket_label: str, subject: str) -> str:
    return f"{ticket_label} was resolved automatically (no reply): {subject}"[:500]
