"""Saved reports (feature-gap plan 3.2): the rules that need no database.

A saved report is a name and a report definition (`domain/report_defs.py`) whose range is
a preset, so it is worked out afresh at every run. Personal reports are seen by their owner
only; shared ones by everyone who holds the reports right, and only roles with `share_views`
make, change or delete shared ones.
"""

NAME_MAX = 80
MAX_PERSONAL = 50  # per member
MAX_SHARED = 100  # per workspace


class ReportError(ValueError):
    """The message is shown to the member."""


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise ReportError(f"A report's name is 1-{NAME_MAX} characters.")
    return name
