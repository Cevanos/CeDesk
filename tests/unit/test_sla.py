"""SLA clocks in business time, without a database.

Dates in 2026: Fri 6 Nov, Sat 7 Nov, Sun 8 Nov (Diwali), Mon 9 Nov; Christmas is a Friday.
"""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from cedesk.helpdesk.domain.sla import (
    DEFAULT_POLICIES,
    DEFAULT_TARGETS,
    Calendar,
    ClockKind,
    Policy,
    SlaState,
    SlaTicket,
    Target,
    TicketSla,
    business_minutes_between,
    current_due,
    due_at,
    first_reply_met,
    is_open,
    pick_policy,
    sla_state,
    targets_for,
)

IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
DIWALI, CHRISTMAS, NEW_YEAR = date(2026, 11, 8), date(2026, 12, 25), date(2027, 1, 1)

# The mock's workspace: Mon-Sat 9:00-19:00 in India, with its three holidays.
ACME = Calendar("Asia/Kolkata", holidays=frozenset({DIWALI, CHRISTMAS, NEW_YEAR}))
ALWAYS = Calendar("Asia/Kolkata", holidays=frozenset({DIWALI}), always_open=True)


def ist(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


CREATED = ist(11, 6, 10)  # Friday 10:00


def high_email(**changes: object) -> SlaTicket:
    return replace(SlaTicket(priority="high", channel="email", created_at=CREATED), **changes)


def state(
    ticket: SlaTicket,
    now: datetime,
    *,
    policies: tuple[Policy, ...] = DEFAULT_POLICIES,
    calendar: Calendar = ACME,
) -> TicketSla:
    return sla_state(ticket, policies=policies, targets=DEFAULT_TARGETS, calendar=calendar, now=now)


# ── the business-hours clock ─────────────────────────────────────────────────


def test_minutes_inside_one_working_day() -> None:
    assert business_minutes_between(ist(11, 6, 10), ist(11, 6, 11, 30), ACME) == 90


def test_overnight_counts_only_the_open_hours() -> None:
    # Mon 18:00 to Tue 10:00: one hour before closing and one after opening.
    assert business_minutes_between(ist(11, 9, 18), ist(11, 10, 10), ACME) == 120
    assert due_at(ist(11, 9, 18), 120, ACME) == ist(11, 10, 10)


def test_sunday_is_skipped() -> None:
    # Saturday 18:30 with a 1-hour target: 30 minutes on Saturday, the rest on Monday.
    assert due_at(ist(11, 7, 18, 30), 60, ACME) == ist(11, 9, 9, 30)
    assert business_minutes_between(ist(11, 7, 18, 30), ist(11, 9, 9, 30), ACME) == 60


def test_a_ticket_created_at_night_waits_for_the_opening() -> None:
    assert due_at(ist(11, 6, 22), 60, ACME) == ist(11, 7, 10)
    assert business_minutes_between(ist(11, 6, 22), ist(11, 7, 9), ACME) == 0


def test_a_holiday_on_a_working_day_is_skipped() -> None:
    # Thursday 24 Dec 18:00 + 2 h: one hour Thursday, Christmas Friday skipped, Saturday 9-10.
    assert due_at(ist(12, 24, 18), 120, ACME) == ist(12, 26, 10)


def test_diwali_is_skipped_even_when_sunday_is_a_working_day() -> None:
    every_day = frozenset(range(1, 8))
    without = Calendar("Asia/Kolkata", days=every_day)
    with_diwali = Calendar("Asia/Kolkata", days=every_day, holidays=frozenset({DIWALI}))
    assert due_at(ist(11, 7, 18, 30), 60, without) == ist(11, 8, 9, 30)
    assert due_at(ist(11, 7, 18, 30), 60, with_diwali) == ist(11, 9, 9, 30)


def test_24x7_counts_every_minute_and_ignores_holidays() -> None:
    assert due_at(ist(11, 7, 18, 30), 60, ALWAYS) == ist(11, 7, 19, 30)
    assert business_minutes_between(ist(11, 8, 0), ist(11, 9, 0), ALWAYS) == 24 * 60
    assert is_open(ist(11, 8, 3), ALWAYS)


def test_the_clock_follows_daylight_saving_in_the_workspace_zone() -> None:
    # London moves to summer time on Sun 29 Mar 2026. Fri 16:00 + 2 h in a Mon-Fri 9-17
    # week is Mon 10:00 local time, which is 09:00 UTC.
    london = Calendar("Europe/London", days=frozenset(range(1, 6)), start=time(9), end=time(17))
    friday = datetime(2026, 3, 27, 16, tzinfo=LONDON)
    due = due_at(friday, 120, london)
    assert due == datetime(2026, 3, 30, 10, tzinfo=LONDON)
    assert due == datetime(2026, 3, 30, 9, tzinfo=UTC)
    assert business_minutes_between(friday, due, london) == 120


def test_the_same_moment_is_open_in_one_zone_and_closed_in_another() -> None:
    moment = datetime(2026, 11, 6, 4, tzinfo=UTC)  # 09:30 in India, 04:00 in London
    assert is_open(moment, ACME)
    assert not is_open(moment, Calendar("Europe/London"))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (ist(11, 7, 18, 59), True),
        (ist(11, 7, 19), False),
        (ist(11, 8, 12), False),  # Sunday
        (ist(11, 9, 8, 59), False),
        (ist(11, 9, 9), True),
        (ist(12, 25, 12), False),  # Christmas, a Friday
    ],
)
def test_is_open(moment: datetime, expected: bool) -> None:
    assert is_open(moment, ACME) is expected


def test_times_without_a_zone_are_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        business_minutes_between(datetime(2026, 11, 6, 10), ist(11, 6, 11), ACME)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"timezone": "Mars/Olympus"}, "Unknown time zone"),
        ({"timezone": "Asia/Kolkata", "days": frozenset()}, "ISO weekdays"),
        ({"timezone": "Asia/Kolkata", "days": frozenset({0})}, "ISO weekdays"),
        ({"timezone": "Asia/Kolkata", "start": time(19), "end": time(9)}, "end after"),
    ],
)
def test_a_bad_calendar_is_refused(settings: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Calendar(**settings)


# ── policies and targets ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("vip", "channel", "expected"),
    [
        (True, "email", "VIP customers"),
        (False, "chat", "Live chat & messaging"),
        (False, "email", "Default"),
        (True, "chat", "VIP customers"),  # both match; the first one wins
    ],
)
def test_the_first_matching_policy_wins(vip: bool, channel: str, expected: str) -> None:
    picked = pick_policy(high_email(vip=vip, channel=channel), DEFAULT_POLICIES)
    assert picked is not None
    assert picked.name == expected


def test_reordering_the_policies_changes_the_pick() -> None:
    vip, live, default = DEFAULT_POLICIES
    assert pick_policy(high_email(vip=True, channel="chat"), (live, vip, default)) is live


def test_a_switched_off_policy_is_passed_over() -> None:
    vip, live, default = DEFAULT_POLICIES
    off = (replace(vip, enabled=False), live, default)
    assert pick_policy(high_email(vip=True), off) is default


def test_a_policy_can_match_on_priority() -> None:
    urgent_only = Policy("Urgent", first_reply_minutes=5, priorities=frozenset({"urgent"}))
    assert not urgent_only.matches(high_email())
    assert urgent_only.matches(high_email(priority="urgent"))


def test_an_empty_policy_target_uses_the_priority_target() -> None:
    vip, live, _ = DEFAULT_POLICIES
    assert targets_for(high_email(channel="chat"), live, DEFAULT_TARGETS) == Target(10, 480)
    assert targets_for(high_email(vip=True), vip, DEFAULT_TARGETS) == Target(30, 240)
    assert targets_for(high_email(priority="urgent"), None, DEFAULT_TARGETS) == Target(15, 240)


def test_with_no_matching_policy_the_priority_targets_apply() -> None:
    sla = state(high_email(), CREATED, policies=())
    assert sla.policy is None
    assert (sla.first_reply.target_minutes, sla.resolve.target_minutes) == (60, 480)


def test_an_unknown_priority_is_refused() -> None:
    with pytest.raises(ValueError, match="No SLA target for priority 'p1'"):
        targets_for(high_email(priority="p1"), None, DEFAULT_TARGETS)


def test_targets_are_positive_minutes() -> None:
    with pytest.raises(ValueError, match="positive"):
        Target(0, 10)
    with pytest.raises(ValueError, match="positive"):
        Policy("Broken", first_reply_minutes=-1)


# ── ticket states ────────────────────────────────────────────────────────────


def test_a_new_ticket_is_on_track() -> None:
    sla = state(high_email(), ist(11, 6, 10, 20))
    assert sla.policy == "Default"
    assert sla.current.kind is ClockKind.first_reply
    assert sla.current.state is SlaState.on_track
    assert sla.current.minutes_left == 40
    assert sla.current.due_at == ist(11, 6, 11)
    assert sla.resolve.due_at == ist(11, 6, 18)
    assert not sla.paused


@pytest.mark.parametrize(
    ("now", "expected", "left"),
    [
        (ist(11, 6, 10, 45), SlaState.on_track, 15),  # exactly 25% left
        (ist(11, 6, 10, 50), SlaState.close, 10),
        (ist(11, 6, 11), SlaState.close, 0),  # at the deadline, not past it
        (ist(11, 6, 11, 30), SlaState.breached, -30),
    ],
)
def test_the_state_follows_the_time_left(now: datetime, expected: SlaState, left: int) -> None:
    clock = state(high_email(), now).first_reply
    assert (clock.state, clock.minutes_left) == (expected, left)


def test_a_reply_in_time_is_met_and_the_pill_moves_to_resolution() -> None:
    sla = state(high_email(first_reply_at=ist(11, 6, 10, 45)), ist(11, 6, 12))
    assert sla.first_reply.state is SlaState.met
    assert sla.first_reply.minutes_left == 15
    assert sla.first_reply.stopped_at == ist(11, 6, 10, 45)
    assert sla.current is sla.resolve
    assert (sla.resolve.state, sla.resolve.minutes_left) == (SlaState.on_track, 360)


def test_a_late_reply_stays_breached() -> None:
    clock = state(high_email(first_reply_at=ist(11, 6, 11, 10)), ist(11, 6, 12)).first_reply
    assert (clock.state, clock.minutes_left) == (SlaState.breached, -10)


def test_outside_business_hours_the_clock_is_paused() -> None:
    evening = high_email(created_at=ist(11, 6, 18, 30))
    night = state(evening, ist(11, 6, 22))
    assert night.paused
    assert (night.current.state, night.current.minutes_left) == (SlaState.on_track, 30)
    assert night.current.due_at == ist(11, 7, 9, 30)
    morning = state(evening, ist(11, 7, 9, 20))
    assert not morning.paused
    assert (morning.current.state, morning.current.minutes_left) == (SlaState.close, 10)


def test_a_deadline_missed_before_closing_stays_breached_while_paused() -> None:
    sla = state(high_email(created_at=ist(11, 7, 18)), ist(11, 8, 10))  # due Sat 19:00
    assert sla.paused
    assert sla.current.state is SlaState.breached
    assert sla.current.due_at == ist(11, 7, 19)


def test_resolving_without_a_reply_stops_both_clocks() -> None:
    sla = state(high_email(resolved_at=ist(11, 6, 10, 30)), ist(11, 7, 23))
    assert sla.first_reply.state is SlaState.met
    assert sla.first_reply.stopped_at == ist(11, 6, 10, 30)
    assert sla.resolve.state is SlaState.met
    assert sla.current is sla.resolve
    assert not sla.paused


def test_vip_and_live_chat_targets_reach_the_clocks() -> None:
    vip = state(high_email(vip=True), CREATED)
    assert vip.policy == "VIP customers"
    assert (vip.first_reply.due_at, vip.resolve.due_at) == (ist(11, 6, 10, 30), ist(11, 6, 14))
    chat = state(high_email(channel="chat"), CREATED)
    assert chat.policy == "Live chat & messaging"
    assert (chat.first_reply.due_at, chat.resolve.due_at) == (ist(11, 6, 10, 10), ist(11, 6, 18))


def test_a_24x7_workspace_never_pauses() -> None:
    sla = state(high_email(created_at=ist(11, 7, 18, 30)), ist(11, 8, 3), calendar=ALWAYS)
    assert not sla.paused
    assert sla.current.state is SlaState.breached
    assert sla.current.due_at == ist(11, 7, 19, 30)


def test_first_reply_met_agrees_with_the_full_clock() -> None:
    """The report's shortcut gives the same answer as the SLA pill for every case."""
    rng = random.Random(7)  # noqa: S311 - fixed test data, not security
    cal = Calendar("Asia/Kolkata", holidays=frozenset({date(2026, 11, 9)}))
    now = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
    for _ in range(2000):
        created = now - timedelta(minutes=rng.randint(0, 60 * 24 * 10))
        reply = created + timedelta(minutes=rng.randint(1, 60 * 24 * 3))
        ticket = SlaTicket(
            priority=rng.choice(["urgent", "high", "medium", "low"]),
            channel=rng.choice(["email", "chat", "web"]),
            created_at=created,
            vip=rng.random() < 0.2,
            first_reply_at=reply if reply < now and rng.random() < 0.6 else None,
            resolved_at=reply if reply < now and rng.random() < 0.3 else None,
        )
        full = sla_state(ticket, policies=DEFAULT_POLICIES, targets=DEFAULT_TARGETS,
                         calendar=cal, now=now).first_reply.state  # fmt: skip
        expected = {SlaState.met: True, SlaState.breached: False}.get(full)
        assert first_reply_met(ticket, policies=DEFAULT_POLICIES, targets=DEFAULT_TARGETS,
                               calendar=cal, now=now) is expected  # fmt: skip


def test_current_due_agrees_with_the_full_clock() -> None:
    """The stored `sla_due_at` shortcut gives the pill's deadline for every case."""
    rng = random.Random(11)  # noqa: S311 - fixed test data, not security
    cal = Calendar("Asia/Kolkata", holidays=frozenset({date(2026, 11, 9)}))
    now = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
    for _ in range(2000):
        created = now - timedelta(minutes=rng.randint(0, 60 * 24 * 10))
        later = created + timedelta(minutes=rng.randint(1, 60 * 24 * 3))
        ticket = SlaTicket(
            priority=rng.choice(["urgent", "high", "medium", "low"]),
            channel=rng.choice(["email", "chat", "web"]),
            created_at=created,
            vip=rng.random() < 0.2,
            first_reply_at=later if rng.random() < 0.5 else None,
            resolved_at=later if rng.random() < 0.2 else None,
        )
        full = sla_state(ticket, policies=DEFAULT_POLICIES, targets=DEFAULT_TARGETS,
                         calendar=cal, now=now)  # fmt: skip
        expected = None if ticket.resolved_at is not None else full.current.due_at
        assert current_due(ticket, policies=DEFAULT_POLICIES, targets=DEFAULT_TARGETS,
                           calendar=cal) == expected  # fmt: skip
