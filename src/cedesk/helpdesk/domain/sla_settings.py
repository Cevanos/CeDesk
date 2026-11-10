"""SLA settings around the clock logic in `sla.py`: the workspace defaults and the
"Reads as" sentence on the targets page (md W9). No database."""

from collections.abc import Mapping

from cedesk.helpdesk.domain.sla import DEFAULT_TARGETS, Calendar, Target

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_CALENDAR = Calendar(DEFAULT_TIMEZONE)  # Mon-Sat 9:00-19:00, no holidays
PRIORITIES = ("urgent", "high", "medium", "low")
MAX_MINUTES = 30 * 24 * 60


def duration(minutes: int) -> str:
    """15 → '15 min', 240 → '4 h', 90 → '1 h 30 min', 2880 → '48 h'."""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h" if not rest else f"{hours} h {rest} min"


def reads_as(priority: str, target: Target) -> str:
    return (
        f"{priority.capitalize()} tickets get a first reply within "
        f"{duration(target.first_reply_minutes)} and are resolved within "
        f"{duration(target.resolve_minutes)} of business time."
    )


def target_problem(first_reply: int, resolve: int) -> str | None:
    for minutes in (first_reply, resolve):
        if not 1 <= minutes <= MAX_MINUTES:
            return f"Targets are 1 to {MAX_MINUTES} minutes (30 days)."
    if first_reply > resolve:
        return "The first reply can't be due after the resolution."
    return None


def with_defaults(saved: Mapping[str, Target]) -> dict[str, Target]:
    """Saved targets, and the mock's default for any priority not saved."""
    return {p: saved.get(p, DEFAULT_TARGETS[p]) for p in PRIORITIES}
