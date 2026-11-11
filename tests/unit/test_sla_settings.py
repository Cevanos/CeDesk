"""The SLA settings helpers around `domain/sla.py` (md W9-W12)."""

from datetime import UTC, date, time

import pytest

from cedesk.helpdesk.domain.sla import DEFAULT_POLICIES, DEFAULT_TARGETS, Policy, Target
from cedesk.helpdesk.domain.sla_settings import (
    MAX_MINUTES,
    SettingsError,
    check_calendar,
    check_holiday,
    check_policies,
    duration,
    reads_as,
    target_problem,
    with_defaults,
)


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(15, "15 min"), (60, "1 h"), (90, "1 h 30 min"), (240, "4 h"), (4320, "72 h")],
)
def test_duration(minutes: int, text: str) -> None:
    assert duration(minutes) == text


def test_reads_as() -> None:
    assert reads_as("high", Target(60, 480)) == (
        "High tickets get a first reply within 1 h and are resolved within 8 h of business time."
    )


def test_target_problems() -> None:
    assert target_problem(15, 240) is None
    assert target_problem(240, 240) is None
    assert target_problem(241, 240) == "The first reply can't be due after the resolution."
    assert target_problem(0, 240) is not None
    assert target_problem(15, MAX_MINUTES + 1) is not None


def test_defaults_fill_what_was_not_saved() -> None:
    mine = Target(5, 60)
    merged = with_defaults({"urgent": mine})
    assert list(merged) == ["urgent", "high", "medium", "low"]
    assert merged["urgent"] == mine and merged["low"] == DEFAULT_TARGETS["low"]


# What an Admin may save (W10-W12)

DEFAULT = Policy("Default")


def test_the_built_in_policies_can_be_saved_back() -> None:
    check_policies(DEFAULT_POLICIES)


@pytest.mark.parametrize(
    ("policies", "problem"),
    [
        ([], "Keep 1 to 20 policies."),
        ([DEFAULT] * 21, "Keep 1 to 20 policies."),
        ([Policy("VIP", vip_only=True)], "The last policy must be on and have no conditions"),
        ([Policy("Default", enabled=False)], "The last policy must be on"),
        ([Policy("All"), DEFAULT], "“All” matches every ticket"),
        ([Policy("default", vip_only=True), DEFAULT], "Two policies are called"),
        ([Policy("Fax", channels=frozenset({"fax"})), DEFAULT], "Fax: unknown channel fax."),
        ([Policy("P", priorities=frozenset({"asap"})), DEFAULT], "P: unknown priority asap."),
        ([Policy("Slow", vip_only=True, first_reply_minutes=60, resolve_minutes=30), DEFAULT],
         "Slow: The first reply can't be due after the resolution."),
        ([Policy("Long", vip_only=True, resolve_minutes=43201), DEFAULT], "Long: Targets are 1"),
        ([Policy(" Pad", vip_only=True), DEFAULT], "A policy name is 1 to 80"),
    ],
)  # fmt: skip
def test_policies_that_cannot_be_saved(policies: list[Policy], problem: str) -> None:
    with pytest.raises(SettingsError, match=problem.replace("(", r"\(")):
        check_policies(policies)


def test_a_switched_off_catch_all_may_sit_above_the_default() -> None:
    check_policies([Policy("Old", enabled=False), Policy("Chat", channels=frozenset({"chat"})),
                    DEFAULT])  # fmt: skip


def test_business_hours_rules() -> None:
    cal = check_calendar("Europe/London", [1, 2, 3], time(8), time(17), False)
    assert cal.days == frozenset({1, 2, 3}) and cal.timezone == "Europe/London"
    for bad in (("Mars/Base", [1], time(8), time(17)), ("UTC", [1, 1], time(8), time(17)),
                ("UTC", [8], time(8), time(17)), ("UTC", [1], time(17), time(8))):  # fmt: skip
        with pytest.raises(SettingsError):
            check_calendar(*bad, False)


def test_holiday_rules() -> None:
    check_holiday(date(2026, 11, 8), "Diwali")
    for day, name in ((date(1999, 12, 31), "Old"), (date(2026, 1, 1), ""),
                      (date(2026, 1, 1), "x" * 81)):  # fmt: skip
        with pytest.raises(SettingsError):
            check_holiday(day, name)


def test_business_hours_carry_no_time_zone_of_their_own() -> None:
    with pytest.raises(SettingsError, match="without a time zone"):
        check_calendar("UTC", [1], time(9, tzinfo=UTC), time(17), False)
