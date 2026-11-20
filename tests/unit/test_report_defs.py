"""Report definitions, ranges, time buckets, snapshots' day rule, the live window and the
schedule rules (feature-gap plan phase 3): everything here is pure, no database."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import backlog as backlog_rules
from cedesk.helpdesk.domain import live as live_rules
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain import report_schedules as schedule_rules
from cedesk.helpdesk.domain.permissions import Access, Action, Role, Scope, built_in
from cedesk.helpdesk.repo import report_engine as engine_repo

KOLKATA = "Asia/Kolkata"
NEW_YORK = "America/New_York"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


# parse -------------------------------------------------------------------------------------


def test_a_minimal_definition_gets_defaults() -> None:
    d = defs.parse({"metric": "created"})
    assert (d.metric, d.group_by, dict(d.filters), d.range.preset) == (
        "created", "none", {}, "last_30d",
    )  # fmt: skip


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"metric": "revenue"},
        {"metric": "created", "group_by": "customer"},
        {"metric": "created", "extra": 1},
        {"metric": "created", "filters": {"status": "open"}},
        {"metric": "created", "filters": {"priority": "extreme"}},
        {"metric": "created", "filters": {"team": "not-an-id"}},
        {"metric": "created", "filters": {"tag": ""}},
        {"metric": "created", "filters": {"imported": "maybe"}},
        {"metric": "created", "range": {"preset": "last_year"}},
        {"metric": "created", "range": {"preset": "last_7d", "from": "2026-01-01T00:00:00Z"}},
        {"metric": "created", "range": {"from": "2026-01-01T00:00:00Z"}},
        {"metric": "created", "range": {"from": "2026-01-01", "to": "2026-02-01"}},
        {
            "metric": "created",
            "range": {"from": "2026-01-01T00:00:00Z", "to": "2027-06-01T00:00:00Z"},
        },
        {
            "metric": "created",
            "range": {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        },
    ],
)
def test_a_bad_definition_is_refused(raw: object) -> None:
    with pytest.raises(defs.DefinitionError):
        defs.parse(raw)


def test_open_now_cannot_be_grouped_by_time() -> None:
    for group in defs.TIME_GROUPS:
        with pytest.raises(defs.DefinitionError, match="open now"):
            defs.parse({"metric": "open_now", "group_by": group})
    assert defs.parse({"metric": "open_now", "group_by": "priority"}).group_by == "priority"


def test_the_backlog_is_by_day_and_only_filtered_by_team() -> None:
    assert defs.parse({"metric": "backlog", "group_by": "day"}).group_by == "day"
    with pytest.raises(defs.DefinitionError):
        defs.parse({"metric": "backlog", "group_by": "week"})
    with pytest.raises(defs.DefinitionError):
        defs.parse({"metric": "backlog", "group_by": "day", "filters": {"priority": "high"}})
    assert defs.parse(
        {"metric": "backlog", "group_by": "day", "filters": {"team": str(uuid4())}}
    ).filters["team"]


def test_stored_keeps_a_preset_range_and_drops_empty_filters() -> None:
    team = str(uuid4())
    d = defs.parse(
        {"metric": "resolved", "group_by": "team", "filters": {"team": team, "tag": None}}
    )
    assert d.stored() == {
        "metric": "resolved", "group_by": "team", "filters": {"team": team},
        "range": {"preset": "last_30d"},
    }  # fmt: skip
    assert defs.parse(d.stored()) == d


def test_an_absolute_range_round_trips() -> None:
    raw = {
        "metric": "created",
        "range": {"from": "2026-01-01T00:00:00+05:30", "to": "2026-02-01T00:00:00+05:30"},
    }
    d = defs.parse(raw)
    assert d.range.preset is None
    assert d.stored()["range"] == raw["range"] | {}


def test_every_preset_is_a_valid_definition() -> None:
    assert len(defs.PRESETS) >= 15
    for key in defs.PRESETS:
        assert defs.preset_definition(key).metric in defs.METRICS
    with pytest.raises(defs.DefinitionError):
        defs.preset_definition("nope")


# the SQL maps cover every allowed key ------------------------------------------------------


def test_every_metric_has_sql_except_the_snapshot_one() -> None:
    assert set(engine_repo.metric_keys()) == set(defs.METRICS) - {"backlog"}


def test_every_allowed_metric_and_grouping_builds_a_query() -> None:
    for metric in engine_repo.metric_keys():
        kind = defs.METRICS[metric].kind
        for group in defs.GROUP_BYS:
            if kind == defs.NOW and group in defs.TIME_GROUPS:
                with pytest.raises(ValueError):
                    engine_repo.sql_for(metric, group)
                continue
            sql = engine_repo.sql_for(metric, group)
            assert "SELECT" in sql and ":scope_team" in sql
            assert "merged_into_id IS NULL" in sql


def test_sql_is_never_built_from_unchecked_text() -> None:
    with pytest.raises(ValueError):
        engine_repo.sql_for("created", "day'; DROP TABLE hd_ticket; --")
    with pytest.raises(ValueError):
        engine_repo.sql_for("created; DROP TABLE hd_ticket", "none")


def test_a_field_grouping_is_capped_and_a_time_grouping_is_not() -> None:
    assert f"LIMIT {defs.MAX_GROUPS + 1}" in engine_repo.sql_for("created", "priority")
    assert "LIMIT" not in engine_repo.sql_for("created", "day")


# ranges and buckets ------------------------------------------------------------------------


def test_last_n_days_are_whole_local_days_ending_now() -> None:
    now = at("2026-11-20T10:00:00+00:00")  # 15:30 in Kolkata
    start, end = defs.resolve_range(defs.Range(preset="last_7d"), now, KOLKATA)
    assert end == now
    assert start == at("2026-11-14T00:00:00+05:30")
    assert defs.resolve_range(defs.Range(preset="last_30d"), now, KOLKATA)[0] == at(
        "2026-10-22T00:00:00+05:30"
    )


def test_the_local_day_decides_where_a_range_starts() -> None:
    now = at("2026-11-20T20:00:00+00:00")  # already 21 Nov in Kolkata
    start, _ = defs.resolve_range(defs.Range(preset="last_7d"), now, KOLKATA)
    assert start == at("2026-11-15T00:00:00+05:30")


def test_this_month_and_last_month() -> None:
    now = at("2026-03-10T12:00:00+00:00")
    assert defs.resolve_range(defs.Range(preset="this_month"), now, KOLKATA) == (
        at("2026-03-01T00:00:00+05:30"), now,
    )  # fmt: skip
    assert defs.resolve_range(defs.Range(preset="last_month"), now, KOLKATA) == (
        at("2026-02-01T00:00:00+05:30"), at("2026-03-01T00:00:00+05:30"),
    )  # fmt: skip
    january = at("2026-01-05T12:00:00+00:00")
    assert defs.resolve_range(defs.Range(preset="last_month"), january, KOLKATA)[0] == at(
        "2025-12-01T00:00:00+05:30"
    )


def test_a_365_day_preset_fits_the_366_day_cap() -> None:
    now = at("2026-11-20T10:00:00+00:00")
    start, end = defs.resolve_range(defs.Range(preset="last_365d"), now, KOLKATA)
    assert end - start <= timedelta(days=366)


def test_day_buckets_are_zero_filled_across_the_range() -> None:
    days = defs.buckets(
        at("2026-11-01T00:00:00+05:30"), at("2026-11-04T00:00:00+05:30"), KOLKATA, "day"
    )
    assert days == [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]


def test_a_range_ending_at_midnight_does_not_add_the_next_day() -> None:
    days = defs.buckets(
        at("2026-11-01T00:00:00+05:30"), at("2026-11-02T00:00:00+05:30"), KOLKATA, "day"
    )
    assert days == [date(2026, 11, 1)]


def test_week_buckets_start_on_monday() -> None:
    # 20 Nov 2026 is a Friday; its week starts Monday 16 Nov.
    weeks = defs.buckets(
        at("2026-11-04T09:00:00+05:30"), at("2026-11-20T12:00:00+05:30"), KOLKATA, "week"
    )
    assert weeks == [date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)]
    assert all(w.weekday() == 0 for w in weeks)


def test_month_buckets_cross_a_year_end() -> None:
    months = defs.buckets(
        at("2026-11-15T00:00:00+00:00"), at("2027-02-10T00:00:00+00:00"), KOLKATA, "month"
    )
    assert months == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]


def test_buckets_follow_the_workspace_zone_not_utc() -> None:
    # 18:30 UTC on the 1st is already the 2nd in Kolkata.
    days = defs.buckets(
        at("2026-11-01T18:30:00+00:00"), at("2026-11-02T20:00:00+00:00"), KOLKATA, "day"
    )
    assert days == [date(2026, 11, 2), date(2026, 11, 3)]


def test_a_daylight_saving_day_is_still_one_bucket() -> None:
    # New York springs forward on 8 Mar 2026: that local day has 23 hours.
    days = defs.buckets(
        at("2026-03-07T00:00:00-05:00"), at("2026-03-10T00:00:00-04:00"), NEW_YORK, "day"
    )
    assert days == [date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9)]


def test_a_leap_day_has_its_own_bucket() -> None:
    days = defs.buckets(
        at("2028-02-27T00:00:00+05:30"), at("2028-03-02T00:00:00+05:30"), KOLKATA, "day"
    )
    assert date(2028, 2, 29) in days and len(days) == 4


# backlog snapshot day ----------------------------------------------------------------------


def test_a_pass_writes_yesterday_in_the_workspace_zone() -> None:
    now = at("2026-11-20T20:00:00+00:00")  # 21 Nov 01:30 in Kolkata
    assert backlog_rules.day_to_capture(now, KOLKATA) == date(2026, 11, 20)
    assert backlog_rules.day_to_capture(now, "UTC") == date(2026, 11, 19)


def test_days_covering_a_period_include_both_end_days() -> None:
    first, last = backlog_rules.days_covering(
        at("2026-11-01T10:00:00+05:30"), at("2026-11-04T00:00:00+05:30"), KOLKATA
    )
    assert (first, last) == (date(2026, 11, 1), date(2026, 11, 3))


def test_snapshots_are_kept_three_years() -> None:
    now = at("2026-11-20T10:00:00+00:00")
    assert backlog_rules.keep_from(now, "UTC") == date(2026, 11, 20) - timedelta(days=3 * 365)
    with pytest.raises(ValueError):
        backlog_rules.local_date(datetime(2026, 1, 1), "UTC")


# live window -------------------------------------------------------------------------------


def test_the_live_window_is_bounded() -> None:
    assert live_rules.check_window(60) == 60
    for bad in (0, 4, 24 * 60 + 1):
        with pytest.raises(live_rules.LiveError):
            live_rules.check_window(bad)


# schedules ---------------------------------------------------------------------------------


def test_timing_must_fit_the_cadence() -> None:
    assert schedule_rules.check_timing("daily", None, None, 9, None) == "last_7d"
    assert schedule_rules.check_timing("weekly", 1, None, 9, None) == "last_7d"
    assert schedule_rules.check_timing("monthly", None, 1, 9, None) == "last_month"
    assert schedule_rules.check_timing("daily", None, None, 9, "last_90d") == "last_90d"
    for bad in [
        ("hourly", None, None, 9, None),
        ("weekly", None, None, 9, None),
        ("daily", 3, None, 9, None),
        ("weekly", 8, None, 9, None),
        ("monthly", None, None, 9, None),
        ("monthly", None, 29, 9, None),
        ("daily", None, None, 24, None),
        ("daily", None, None, 9, "last_year"),
    ]:
        with pytest.raises(schedule_rules.ScheduleError):
            schedule_rules.check_timing(*bad)


def test_a_daily_run_is_the_next_nine_in_the_workspace_zone() -> None:
    after = at("2026-11-20T02:00:00+00:00")  # 07:30 Kolkata: today 09:00 is still ahead
    run = schedule_rules.next_run(after, "daily", None, None, 9, KOLKATA)
    assert run == at("2026-11-20T09:00:00+05:30")
    later = schedule_rules.next_run(run, "daily", None, None, 9, KOLKATA)  # exactly due: tomorrow
    assert later == at("2026-11-21T09:00:00+05:30")


def test_a_weekly_run_waits_for_its_weekday() -> None:
    after = at("2026-11-20T10:00:00+00:00")  # Friday
    assert schedule_rules.next_run(after, "weekly", 1, None, 8, KOLKATA) == at(
        "2026-11-23T08:00:00+05:30"
    )
    assert schedule_rules.next_run(after, "weekly", 5, None, 23, KOLKATA) == at(
        "2026-11-20T23:00:00+05:30"
    )


def test_a_monthly_run_rolls_over_the_year() -> None:
    after = at("2026-12-28T10:00:00+00:00")
    assert schedule_rules.next_run(after, "monthly", None, 15, 9, KOLKATA) == at(
        "2027-01-15T09:00:00+05:30"
    )


def test_a_run_at_a_skipped_hour_moves_on_by_the_clock() -> None:
    after = at("2026-03-08T00:00:00-05:00")
    run = schedule_rules.next_run(after, "daily", None, None, 2, NEW_YORK)  # 02:30 doesn't exist
    assert run.astimezone(UTC) > after.astimezone(UTC)
    assert run.astimezone(UTC) - after.astimezone(UTC) < timedelta(hours=4)


def _access(scope: Scope, team: object = None, *, reports: bool = True) -> Access:
    actions = frozenset({Action.reports}) if reports else frozenset()
    return Access(Role.custom, actions, scope, team)  # type: ignore[arg-type]


def test_a_reader_must_hold_the_right_and_see_as_many_teams_as_the_owner() -> None:
    team_a, team_b = uuid4(), uuid4()
    everyone = _access(Scope.all)
    only_a = _access(Scope.team_only, team_a)
    assert schedule_rules.may_receive(everyone, everyone)
    assert schedule_rules.may_receive(only_a, everyone)
    assert schedule_rules.may_receive(only_a, only_a)
    assert not schedule_rules.may_receive(everyone, only_a)  # narrower reader, wider owner
    assert not schedule_rules.may_receive(only_a, _access(Scope.team_only, team_b))
    assert not schedule_rules.may_receive(everyone, _access(Scope.all, reports=False))
    assert schedule_rules.may_receive(built_in(Role.admin), built_in(Role.supervisor))
    assert not schedule_rules.may_receive(built_in(Role.admin), built_in(Role.agent))


def test_numbers_are_written_for_a_plain_text_email() -> None:
    f = schedule_rules.format_value
    assert f(None, "count") == "-"
    assert f(1234, "count") == "1,234"
    assert f(0, "minutes") == "0 min"
    assert f(125, "minutes") == "2 h 5 min"
    assert f(12.34, "percent") == "12.3%"
    assert f(4.256, "score") == "4.26 / 5"


def test_the_email_says_how_to_stop_and_links_the_report() -> None:
    body = schedule_rules.email_body(
        schedule="Weekly summary", report="Tickets created", period="2026-11-14 to 2026-11-20",
        total="42", rows=[("2026-11-19", "5"), ("2026-11-20", "7")], more=3, notes=["A note."],
        link="https://desk.example/reports",
    )  # fmt: skip
    for expected in ("Weekly summary", "Total: 42", "2026-11-20: 7", "and 3 more", "A note.",
                     "https://desk.example/reports", "Leave"):  # fmt: skip
        assert expected in body
    assert body.endswith("\n")
    assert schedule_rules.subject("Acme", "Weekly summary") == "Acme: Weekly summary"
    assert len(schedule_rules.subject("A" * 300, "x")) <= 200


def test_a_schedule_name_is_trimmed_and_bounded() -> None:
    assert schedule_rules.clean_name("  Weekly   summary ") == "Weekly summary"
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(schedule_rules.ScheduleError):
            schedule_rules.clean_name(bad)
