"""The archive tier (md O3), the rules that need no database.

- A resolved ticket older than the workspace's archive age (default 120 days; the Admin
  can set 30 to 3650) moves to the archive: out of the ticket list, the views, search and
  reports, so those stay fast. An imported ticket counts from when it was imported (as for
  retention), so a fresh import doesn't vanish into the archive the next night.
- The worker moves them in batches; each batch locks its tickets with SKIP LOCKED, so
  several workers never move the same ticket twice.
- An archived ticket is found by its number or its customer (email or contact) and opens
  read only: its messages, files' names and rating. It can't be reopened or replied to.
- Retention (md O2) deletes from the archive too, with the files. A customer's privacy
  export includes their archived tickets, and erasing them blanks those as well.
"""

DEFAULT_DAYS = 120
MIN_DAYS = 30
MAX_DAYS = 3650
BATCH = 200
SEARCH_LIMIT = 50


class BadArchive(ValueError):
    """The message is for the person who typed it."""


def archive_days(setting: int | None) -> int:
    return setting if setting is not None else DEFAULT_DAYS


def check_days(days: int | None) -> int | None:
    if days is not None and not MIN_DAYS <= days <= MAX_DAYS:
        raise BadArchive(
            f"Archive resolved tickets after {MIN_DAYS} to {MAX_DAYS} days, or null for the "
            f"default ({DEFAULT_DAYS})."
        )
    return days


def ticket_number(raw: str) -> int | None:
    """What an agent types to find one: 1042, T-1042 or #1042."""
    value = raw.strip().upper().removeprefix("T-").removeprefix("#")
    return int(value) if value.isdigit() and 0 < len(value) <= 9 else None
