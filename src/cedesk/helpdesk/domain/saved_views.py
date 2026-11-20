"""Saved views (feature-gap plan 1.2), the rules that need no database.

A view is a name and a set of ticket-list filters (checked by `tickets.filters_from_json`,
the same rules as `GET /tickets`). Personal views are seen by their owner only; shared
views by everyone who reads tickets, and only roles with `share_views` make, change or
delete them.
"""

NAME_MAX = 80
MAX_PERSONAL = 50  # per member
MAX_SHARED = 100  # per workspace


class ViewError(ValueError):
    """The message is shown to the member."""


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise ViewError(f"A view's name is 1-{NAME_MAX} characters.")
    return name
