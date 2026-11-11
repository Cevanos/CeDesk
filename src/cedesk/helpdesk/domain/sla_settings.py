"""SLA settings around the clock logic in `sla.py`: the workspace defaults, the "Reads
as" sentence on the targets page (md W9), and the rules for what an Admin may save as
policies (W10), business hours (W11) and holidays (W12). No database."""

from collections.abc import Mapping, Sequence
from datetime import date, time

from cedesk.helpdesk.domain.sla import (
    DEFAULT_TARGETS,
    LIVE_CHANNELS,
    Calendar,
    Policy,
    Target,
)
from cedesk.helpdesk.domain.tickets import Channel

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_CALENDAR = Calendar(DEFAULT_TIMEZONE)  # Mon-Sat 9:00-19:00, no holidays
PRIORITIES = ("urgent", "high", "medium", "low")
MAX_MINUTES = 30 * 24 * 60
MAX_POLICIES = 20
NAME_MAX = 80
# Today's channels, and the live ones the default policy already names (WhatsApp and
# Slack come with their channels), so the default list can be saved back as it is.
POLICY_CHANNELS = tuple(sorted({c.value for c in Channel} | LIVE_CHANNELS))
HOLIDAY_YEARS = (2000, 2100)


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


class SettingsError(ValueError):
    """What an Admin tried to save breaks a rule; the message says which, for the page."""


def _minutes_problem(first_reply: int | None, resolve: int | None) -> str | None:
    for minutes in (first_reply, resolve):
        if minutes is not None and not 1 <= minutes <= MAX_MINUTES:
            return f"Targets are 1 to {MAX_MINUTES} minutes (30 days)."
    if first_reply is not None and resolve is not None and first_reply > resolve:
        return "The first reply can't be due after the resolution."
    return None


def matches_everything(policy: Policy) -> bool:
    return not (policy.vip_only or policy.channels or policy.priorities)


def check_policies(policies: Sequence[Policy]) -> None:
    """The ordered list an Admin saves (W10). First enabled match wins, so:
    - the last one is on and has no conditions: every ticket gets an SLA ("Default");
    - no other one matches every ticket, or the ones after it could never apply.
    Raises SettingsError."""
    if not 1 <= len(policies) <= MAX_POLICIES:
        raise SettingsError(f"Keep 1 to {MAX_POLICIES} policies.")
    seen: set[str] = set()
    for p in policies:
        if not 1 <= len(p.name) <= NAME_MAX or p.name != p.name.strip():
            raise SettingsError(f"A policy name is 1 to {NAME_MAX} characters.")
        if p.name.lower() in seen:
            raise SettingsError(f"Two policies are called \u201c{p.name}\u201d.")
        seen.add(p.name.lower())
        unknown = sorted(p.channels - set(POLICY_CHANNELS))
        if unknown:
            raise SettingsError(f"{p.name}: unknown channel {', '.join(unknown)}.")
        unknown = sorted(p.priorities - set(PRIORITIES))
        if unknown:
            raise SettingsError(f"{p.name}: unknown priority {', '.join(unknown)}.")
        problem = _minutes_problem(p.first_reply_minutes, p.resolve_minutes)
        if problem:
            raise SettingsError(f"{p.name}: {problem}")
    *before, last = policies
    if not (last.enabled and matches_everything(last)):
        raise SettingsError(
            "The last policy must be on and have no conditions, so every ticket gets an SLA."
        )
    for p in before:
        if p.enabled and matches_everything(p):
            raise SettingsError(
                f"\u201c{p.name}\u201d matches every ticket, so the policies after it never "
                "apply. Give it a condition or move it last."
            )


def check_calendar(
    timezone: str, days: Sequence[int], opens: time, closes: time, always_open: bool
) -> Calendar:
    """Business hours (W11) as the clock will use them. Raises SettingsError."""
    if len(set(days)) != len(days):
        raise SettingsError("Each day once.")
    if opens.tzinfo is not None or closes.tzinfo is not None:
        raise SettingsError("Give the hours without a time zone; the time zone is set apart.")
    try:
        return Calendar(timezone, frozenset(days), opens, closes, always_open=always_open)
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc


def check_holiday(day: date, name: str) -> None:
    """A day the clock doesn't run (W12). Raises SettingsError."""
    if not HOLIDAY_YEARS[0] <= day.year <= HOLIDAY_YEARS[1]:
        raise SettingsError(f"Pick a day from {HOLIDAY_YEARS[0]} to {HOLIDAY_YEARS[1]}.")
    if not 1 <= len(name) <= NAME_MAX or name != name.strip():
        raise SettingsError(f"A holiday name is 1 to {NAME_MAX} characters.")


def breach_note(ticket_label: str, subject: str, *, first_reply: bool) -> str:
    """The notification an assignee and their team's Supervisors get (W14)."""
    clock = "first reply" if first_reply else "resolution"
    return f"SLA breached on {ticket_label} ({clock} overdue): {subject}"[:500]
