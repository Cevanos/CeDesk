"""Scheduled report emails (feature-gap plan 3.5): the rules that need no database.

A schedule sends a saved or built-in report to team members at an hour of the workspace's
own day: daily, weekly on a weekday (ISO, 1 is Monday) or monthly on a day from 1 to 28.
The recipients are members, never free addresses; a member may receive a report only if they
hold the reports right and see at least as many teams' tickets as the schedule's owner (the
report is worked out under the owner's scope, so a narrower reader must not get a wider
owner's numbers).
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from cedesk.helpdesk.domain.permissions import Access, Action, Scope
from cedesk.helpdesk.domain.report_defs import PRESET_RANGES

NAME_MAX = 80
MAX_PER_WORKSPACE = 20
MAX_RECIPIENTS = 10
CADENCES = ("daily", "weekly", "monthly")
DEFAULT_RANGE = {"daily": "last_7d", "weekly": "last_7d", "monthly": "last_month"}
BATCH = 20  # schedules one worker pass sends per workspace
LIST_ROWS = 15  # groups shown in the email (the rest are in the report itself)


class ScheduleError(ValueError):
    """The message is shown to the member."""


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise ScheduleError(f"A schedule's name is 1-{NAME_MAX} characters.")
    return name


def check_timing(
    cadence: str, weekday: int | None, day_of_month: int | None, hour: int, range_preset: str | None
) -> str:
    """Checks the timing fields together; returns the range to use."""
    if cadence not in CADENCES:
        raise ScheduleError(f"cadence is one of: {', '.join(CADENCES)}.")
    if (cadence == "weekly") != (weekday is not None):
        raise ScheduleError("weekday goes with a weekly schedule, and a weekly one needs it.")
    if weekday is not None and not 1 <= weekday <= 7:
        raise ScheduleError("weekday is 1 (Monday) to 7 (Sunday).")
    if (cadence == "monthly") != (day_of_month is not None):
        raise ScheduleError(
            "day_of_month goes with a monthly schedule, and a monthly one needs it."
        )
    if day_of_month is not None and not 1 <= day_of_month <= 28:
        raise ScheduleError("day_of_month is 1 to 28, so every month has it.")
    if not 0 <= hour <= 23:
        raise ScheduleError("hour is 0 to 23, in the workspace's time zone.")
    rng = range_preset or DEFAULT_RANGE[cadence]
    if rng not in PRESET_RANGES:
        raise ScheduleError(f"range is one of: {', '.join(PRESET_RANGES)}.")
    return rng


def _local(day: date, hour: int, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)


def next_run(
    after: datetime, cadence: str, weekday: int | None, day_of_month: int | None, hour: int,
    timezone: str,
) -> datetime:  # fmt: skip
    """The first time strictly after `after` that matches the schedule, in UTC. The hour is
    the workspace's local hour (a time that doesn't exist on a clock-change day moves on by
    the clock; one that happens twice uses the first)."""
    zone = ZoneInfo(timezone)
    day = after.astimezone(zone).date()
    for _ in range(40):  # a month and a bit is always enough
        if (
            cadence == "daily"
            or (cadence == "weekly" and day.isoweekday() == weekday)
            or (cadence == "monthly" and day.day == day_of_month)
        ):
            at = _local(day, hour, zone)
            if at > after:
                return at
        day += timedelta(days=1)
    raise ScheduleError("No next run found for this schedule.")


def may_receive(owner: Access, recipient: Access) -> bool:
    """Whether `recipient` may be sent a report worked out under `owner`'s scope."""
    if not recipient.can(Action.reports):
        return False
    if recipient.scope is not Scope.team_only:
        return True  # sees every team's tickets
    return owner.scope is Scope.team_only and owner.team_id == recipient.team_id


def format_value(value: float | int | None, unit: str) -> str:
    """A number for the email's plain text."""
    if value is None:
        return "-"
    if unit == "count":
        return f"{int(value):,}"
    if unit == "minutes":
        whole = round(float(value))
        hours, minutes = divmod(whole, 60)
        return f"{hours} h {minutes} min" if hours else f"{minutes} min"
    if unit == "percent":
        return f"{float(value):.1f}%"
    return f"{float(value):.2f} / 5"  # score


def subject(workspace: str, schedule: str) -> str:
    return f"{workspace}: {schedule}"[:200]


def email_body(
    *, schedule: str, report: str, period: str, total: str, rows: list[tuple[str, str]],
    more: int, notes: list[str], link: str,
) -> str:  # fmt: skip
    """The plain-text email: the numbers, a link to the full report, how to stop."""
    lines = [f"{schedule}", "", f"Report: {report}", f"Period: {period}", f"Total: {total}"]
    if rows:
        lines += ["", *[f"  {label}: {value}" for label, value in rows]]
        if more:
            lines.append(f"  ... and {more} more in the report")
    if notes:
        lines += ["", *notes]
    lines += [
        "",
        f"Open the full report: {link}",
        "",
        "You get this because a teammate added you to a scheduled report. To stop these "
        "emails, open Reports, then Scheduled reports, and choose Leave.",
    ]
    return "\n".join(lines) + "\n"
