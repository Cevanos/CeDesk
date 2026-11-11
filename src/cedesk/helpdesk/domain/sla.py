"""SLA clocks: when a ticket's first reply and its resolution are due, in business time.

Pure logic with no database. Callers pass the ticket's facts, the workspace's ordered policies,
its priority targets and calendar, and the current time. Every datetime must be timezone-aware;
the calendar's time zone decides what "9:00" and "Sunday" mean.

The ticket's status is not an input: Pending does not pause the clock. Only closed hours,
non-working days and holidays do.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CLOSE_SHARE = 0.25
"""A running clock with less than this share of its target left is `close`."""

MON_TO_SAT = frozenset({1, 2, 3, 4, 5, 6})
"""ISO weekdays: 1 is Monday, 7 is Sunday."""

OPENS = time(9, 0)
CLOSES = time(19, 0)


class SlaState(StrEnum):
    on_track = "on_track"
    close = "close"  # under 25% of the target left
    breached = "breached"  # the deadline passed first; stays so after the reply or resolution
    met = "met"  # replied or resolved in time


class ClockKind(StrEnum):
    first_reply = "first_reply"
    resolve = "resolve"


@dataclass(frozen=True)
class Calendar:
    """When the SLA clock runs, in the workspace's time zone."""

    timezone: str
    days: frozenset[int] = MON_TO_SAT
    start: time = OPENS
    end: time = CLOSES
    holidays: frozenset[date] = frozenset()
    always_open: bool = False
    """24/7: every minute counts, holidays included."""

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone '{self.timezone}'.") from exc
        if not self.days or not self.days <= frozenset(range(1, 8)):
            raise ValueError("Business days are ISO weekdays, 1 (Monday) to 7 (Sunday).")
        if self.start >= self.end:
            raise ValueError("Business hours must end after they start, on the same day.")

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class Target:
    first_reply_minutes: int
    resolve_minutes: int

    def __post_init__(self) -> None:
        if self.first_reply_minutes <= 0 or self.resolve_minutes <= 0:
            raise ValueError("SLA targets are a positive number of minutes.")


DEFAULT_TARGETS: Mapping[str, Target] = MappingProxyType(
    {
        "urgent": Target(15, 240),
        "high": Target(60, 480),
        "medium": Target(240, 1440),
        "low": Target(480, 4320),
    }
)
"""Per priority, from the mock: Urgent 15 min / 4 h, High 1 h / 8 h, Medium 4 h / 24 h,
Low 8 h / 72 h."""


@dataclass(frozen=True)
class SlaTicket:
    """What the SLA reads from a ticket. `priority` and `channel` are the stored values."""

    priority: str
    channel: str
    created_at: datetime
    vip: bool = False
    first_reply_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class Policy:
    """One row of the workspace's ordered SLA policies.

    An empty condition matches every ticket. A target left as None falls back to the
    ticket's priority target.
    """

    name: str
    first_reply_minutes: int | None = None
    resolve_minutes: int | None = None
    vip_only: bool = False
    channels: frozenset[str] = frozenset()
    priorities: frozenset[str] = frozenset()
    enabled: bool = True

    def __post_init__(self) -> None:
        for minutes in (self.first_reply_minutes, self.resolve_minutes):
            if minutes is not None and minutes <= 0:
                raise ValueError("SLA targets are a positive number of minutes.")

    def matches(self, ticket: SlaTicket) -> bool:
        return (
            self.enabled
            and (ticket.vip or not self.vip_only)
            and (not self.channels or ticket.channel in self.channels)
            and (not self.priorities or ticket.priority in self.priorities)
        )


LIVE_CHANNELS = frozenset({"chat", "whatsapp", "slack"})

DEFAULT_POLICIES: tuple[Policy, ...] = (
    Policy("VIP customers", first_reply_minutes=30, resolve_minutes=240, vip_only=True),
    Policy("Live chat & messaging", first_reply_minutes=10, channels=LIVE_CHANNELS),
    Policy("Default"),
)
"""The mock's three policies, in order. Default has no conditions, so it stays last."""


@dataclass(frozen=True)
class Clock:
    kind: ClockKind
    target_minutes: int
    due_at: datetime
    """In UTC."""
    stopped_at: datetime | None
    """When the reply or resolution happened, in UTC; None while the clock runs."""
    state: SlaState
    minutes_left: float
    """Business minutes to the deadline, as of now or of the stop; negative once overdue."""


@dataclass(frozen=True)
class TicketSla:
    policy: str | None
    """The policy that set the targets; None when none matched and the priority targets apply."""
    first_reply: Clock
    resolve: Clock
    paused: bool
    """A clock is running and the calendar is closed right now."""

    @property
    def current(self) -> Clock:
        """The clock the ticket's SLA pill shows: first reply until it stops, then resolution."""
        return self.first_reply if self.first_reply.stopped_at is None else self.resolve


def is_open(at: datetime, calendar: Calendar) -> bool:
    """Whether the SLA clock runs at this moment."""
    if calendar.always_open:
        return True
    local = _utc(at).astimezone(calendar.zone)
    return _working_day(local.date(), calendar) and calendar.start <= local.time() < calendar.end


def business_minutes_between(start: datetime, end: datetime, calendar: Calendar) -> float:
    """Working minutes from `start` to `end`; 0 when `end` is not after `start`."""
    return _business_time(_utc(start), _utc(end), calendar).total_seconds() / 60


def due_at(start: datetime, minutes: int, calendar: Calendar) -> datetime:
    """The moment `minutes` working minutes after `start` are used up, in UTC.

    Starting outside business hours, the clock waits for the next opening.
    """
    if minutes < 0:
        raise ValueError("Minutes to add cannot be negative.")
    return _add_business_time(_utc(start), timedelta(minutes=minutes), calendar)


def pick_policy(ticket: SlaTicket, policies: Sequence[Policy]) -> Policy | None:
    """The first enabled policy that matches, in the workspace's order."""
    return next((p for p in policies if p.matches(ticket)), None)


def targets_for(ticket: SlaTicket, policy: Policy | None, targets: Mapping[str, Target]) -> Target:
    """The policy's targets, each missing one taken from the ticket's priority target."""
    try:
        base = targets[ticket.priority]
    except KeyError:
        raise ValueError(f"No SLA target for priority '{ticket.priority}'.") from None
    if policy is None:
        return base
    return Target(
        first_reply_minutes=(
            base.first_reply_minutes
            if policy.first_reply_minutes is None
            else policy.first_reply_minutes
        ),
        resolve_minutes=(
            base.resolve_minutes if policy.resolve_minutes is None else policy.resolve_minutes
        ),
    )


def sla_state(
    ticket: SlaTicket,
    *,
    policies: Sequence[Policy],
    targets: Mapping[str, Target],
    calendar: Calendar,
    now: datetime,
) -> TicketSla:
    """Both clocks of one ticket as of `now`.

    The first-reply clock stops at the first agent reply, or at resolution when nobody
    replied. The resolve clock stops at resolution.
    """
    policy = pick_policy(ticket, policies)
    target = targets_for(ticket, policy, targets)
    replied = ticket.first_reply_at if ticket.first_reply_at is not None else ticket.resolved_at
    return TicketSla(
        policy=policy.name if policy is not None else None,
        first_reply=_clock(
            ClockKind.first_reply,
            ticket.created_at,
            target.first_reply_minutes,
            replied,
            now,
            calendar,
        ),
        resolve=_clock(
            ClockKind.resolve,
            ticket.created_at,
            target.resolve_minutes,
            ticket.resolved_at,
            now,
            calendar,
        ),
        paused=ticket.resolved_at is None and not is_open(now, calendar),
    )


def first_reply_met(
    ticket: SlaTicket,
    *,
    policies: Sequence[Policy],
    targets: Mapping[str, Target],
    calendar: Calendar,
    now: datetime,
) -> bool | None:
    """The first-reply clock only, for reports: True met, False breached, None still
    running in time. Same rule as `sla_state(...).first_reply`, without the rest."""
    target = targets_for(ticket, pick_policy(ticket, policies), targets)
    due = _add_business_time(
        _utc(ticket.created_at), timedelta(minutes=target.first_reply_minutes), calendar
    )
    stopped = ticket.first_reply_at if ticket.first_reply_at is not None else ticket.resolved_at
    if _utc(stopped if stopped is not None else now) > due:
        return False
    return True if stopped is not None else None


def _clock(
    kind: ClockKind,
    started: datetime,
    minutes: int,
    stopped: datetime | None,
    now: datetime,
    calendar: Calendar,
) -> Clock:
    start = _utc(started)
    end = _utc(stopped if stopped is not None else now)
    target = timedelta(minutes=minutes)
    due = _add_business_time(start, target, calendar)
    left = target - _business_time(start, end, calendar)
    if end > due:
        state = SlaState.breached
    elif stopped is not None:
        state = SlaState.met
    elif left < target * CLOSE_SHARE:
        state = SlaState.close
    else:
        state = SlaState.on_track
    return Clock(
        kind=kind,
        target_minutes=minutes,
        due_at=due,
        stopped_at=end if stopped is not None else None,
        state=state,
        minutes_left=left.total_seconds() / 60,
    )


def _utc(at: datetime) -> datetime:
    # Arithmetic on two datetimes sharing one zone object ignores DST, so work in UTC.
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("SLA times must be timezone-aware.")
    return at.astimezone(UTC)


def _working_day(day: date, calendar: Calendar) -> bool:
    return day.isoweekday() in calendar.days and day not in calendar.holidays


def _windows(calendar: Calendar, start: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Open periods in UTC, from the local day holding `start` onward. Never ends: a
    calendar has at least one working day a week and finitely many holidays."""
    zone = calendar.zone
    day = start.astimezone(zone).date()
    while True:
        if _working_day(day, calendar):
            yield (
                datetime.combine(day, calendar.start, tzinfo=zone).astimezone(UTC),
                datetime.combine(day, calendar.end, tzinfo=zone).astimezone(UTC),
            )
        day += timedelta(days=1)


def _business_time(start: datetime, end: datetime, calendar: Calendar) -> timedelta:
    if end <= start:
        return timedelta(0)
    if calendar.always_open:
        return end - start
    total = timedelta(0)
    for opens, closes in _windows(calendar, start):
        if opens >= end:
            break
        low, high = max(opens, start), min(closes, end)
        if high > low:
            total += high - low
    return total


def _add_business_time(start: datetime, amount: timedelta, calendar: Calendar) -> datetime:
    if calendar.always_open:
        return start + amount
    remaining = amount
    for opens, closes in _windows(calendar, start):
        if closes <= start:
            continue
        low = max(opens, start)
        available = closes - low
        if remaining <= available:
            return low + remaining
        remaining -= available
    raise AssertionError("unreachable: business windows never run out")
