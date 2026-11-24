"""The pre-migration report and the cutover checklist (differentiator plan #4), as pure rules."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cedesk.helpdesk.domain import import_readiness as r
from cedesk.helpdesk.domain.imports import AVAILABLE_SOURCES

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
COUNTS = {"contacts": 40, "tickets": 120, "articles": 9, "replies": 6}


def by_key(report: r.PreMigrationReport) -> dict[str, r.ReportItem]:
    return {i.key: i for i in report.items}


def test_every_source_says_what_it_can_read() -> None:
    assert set(r.SUPPORTED_KINDS) == {*AVAILABLE_SOURCES, "csv"}
    for kinds in r.SUPPORTED_KINDS.values():
        assert {"contacts", "tickets"} <= set(kinds)


def test_a_full_source_imports_every_kind_with_its_count() -> None:
    items = by_key(r.pre_migration_report("zendesk", COUNTS, 2, [], []))
    for kind, n in COUNTS.items():
        assert items[kind].status == "imports" and items[kind].count == n
    assert items["groups"].status == "imports" and items["groups"].count == 2
    assert items["history"].status == "cannot_move"
    for key in ("agents", "automation", "views", "email", "business_hours"):
        assert items[key].status == "needs_setup" and items[key].count is None


def test_what_a_source_cannot_give_is_cannot_move() -> None:
    items = by_key(r.pre_migration_report("hubspot", COUNTS, 1, [], []))
    assert items["articles"].status == "cannot_move" and items["articles"].count is None
    assert items["replies"].status == "cannot_move"
    assert "HubSpot" in items["replies"].note
    assert items["tickets"].status == "imports"


def test_unmatched_groups_need_setup_and_are_named() -> None:
    names = [f"Group {i}" for i in range(12)]
    report = r.pre_migration_report("helpscout", COUNTS, 15, names, ["Spam is not imported."])
    groups = by_key(report)["groups"]
    assert groups.status == "needs_setup" and groups.count == 12
    assert groups.name == "Mailboxes to teams"
    assert "Group 0" in groups.note and "Group 9" in groups.note
    assert "Group 10" not in groups.note and "(and 2 more)" in groups.note
    assert report.warnings == ["Spam is not imported."]
    assert report.total("needs_setup") == 6 and report.total("cannot_move") == 1


GOOD = r.CutoverFacts(
    source="zendesk", state="done", sync_on=True, last_sync_at=NOW - timedelta(minutes=4),
    open_failures=0, missing=0, unknown_agent_messages=0, open_without_team=0,
    verified_addresses=1,
)  # fmt: skip


def statuses(facts: r.CutoverFacts) -> dict[str, str]:
    return {c.key: c.status for c in r.cutover_checks(facts, NOW)}


def test_a_clean_syncing_import_is_ready() -> None:
    checks = r.cutover_checks(GOOD, NOW)
    assert [c.key for c in checks] == [
        "import_finished", "counts_match", "no_open_failures", "sync_fresh",
        "agents_matched", "teams_set", "email_ready",
    ]  # fmt: skip
    assert all(c.status == "ok" for c in checks) and r.ready(checks)


def test_each_problem_is_found_and_blocks_go_live() -> None:
    bad = replace(
        GOOD, state="failed", missing=3, open_failures=1, unknown_agent_messages=2,
        open_without_team=5, verified_addresses=0, last_sync_at=NOW - timedelta(minutes=16),
    )  # fmt: skip
    found = statuses(bad)
    assert set(found.values()) == {"problem"}
    checks = r.cutover_checks(bad, NOW)
    assert not r.ready(checks)
    detail = {c.key: c.detail for c in checks}
    assert detail["counts_match"].startswith("3 records")
    assert detail["no_open_failures"].startswith("1 record couldn't")
    assert detail["agents_matched"].startswith("2 messages are")
    assert detail["teams_set"].startswith("5 open imported tickets have")


def test_a_running_import_is_not_ready() -> None:
    assert statuses(replace(GOOD, state="running"))["import_finished"] == "problem"


def test_sync_is_skipped_when_off_or_for_a_csv_file_but_never_blocks() -> None:
    off = replace(GOOD, sync_on=False, last_sync_at=None)
    assert statuses(off)["sync_fresh"] == "skipped"
    assert r.ready(r.cutover_checks(off, NOW))
    assert statuses(replace(off, source="csv"))["sync_fresh"] == "skipped"
    assert statuses(replace(GOOD, last_sync_at=None))["sync_fresh"] == "problem"
