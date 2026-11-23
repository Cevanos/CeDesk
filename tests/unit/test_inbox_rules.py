"""The inbox tools' rules that need no database (feature-gap plan phase 1)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import auto_close, bulk, events, snooze, transcript
from cedesk.helpdesk.domain.tickets import (
    FieldError,
    ListFilters,
    Sort,
    Status,
    filters_from_json,
    list_filters,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_list_filters_and_view_json_share_one_set_of_rules() -> None:
    team = uuid4()
    f = filters_from_json({"status": "pending", "team": str(team), "q": " a ", "snoozed": True})
    assert f == ListFilters(status=Status.pending, team=team, q="a", snoozed=True)
    assert f.stored() == {"status": "pending", "team": str(team), "q": "a", "snoozed": True}
    assert filters_from_json({"sort": "priority", "scope": "everyone"}).stored() == {}
    assert filters_from_json({"imported": "any"}).sources is not None
    assert list_filters(q="   ").q is None and list_filters(sort=Sort.sla).sort is Sort.sla
    for raw in ({"owner": "me"}, {"status": "closed"}, {"snoozed": 1}, {"team": 5},
                {"scope": "all"}, {"imported": "nope"}, {"q": "x" * 201}):  # fmt: skip
        with pytest.raises(FieldError):
            filters_from_json(raw)


def test_snooze_times() -> None:
    assert snooze.check_until(NOW + timedelta(hours=1), NOW) == NOW + timedelta(hours=1)
    for bad in (NOW, NOW + timedelta(seconds=30), NOW + timedelta(days=366),
                datetime(2027, 1, 1)):  # fmt: skip
        with pytest.raises(snooze.SnoozeError):
            snooze.check_until(bad, NOW)
    assert snooze.snoozed_line(NOW, "Neha") == "Snoozed until 20 Nov 2026 12:00 UTC by Neha"


def test_snooze_changes_show_in_ticket_updated_only_when_both_sides_know() -> None:
    before = {"status": "open", "snoozed_until": None}
    assert events.changed(before, {"status": "pending", "snoozed_until": NOW}) == [
        "status", "snoozed_until"]  # fmt: skip
    assert events.changed({"status": "open"}, {"status": "open", "snoozed_until": NOW}) == []


def test_bulk_actions_need_exactly_their_value() -> None:
    a = uuid4()
    assert bulk.changes(bulk.BulkAction.assign, {"assignee_id": None}) == {"assignee_id": None}
    assert bulk.changes(bulk.BulkAction.add_tags, {"tags": ["x"]}) == {"add_tags": ["x"]}
    assert bulk.changes(bulk.BulkAction.unsnooze, {}) == {"snooze_until": None}
    assert bulk.changes(bulk.BulkAction.snooze, {"until": NOW}) == {"snooze_until": NOW}
    for action, given in ((bulk.BulkAction.assign, {}),
                          (bulk.BulkAction.set_status, {"status": None}),
                          (bulk.BulkAction.set_team, {"team_id": a, "priority": "low"}),
                          (bulk.BulkAction.remove_tags, {"tags": []}),
                          (bulk.BulkAction.unsnooze, {"until": NOW})):  # fmt: skip
        with pytest.raises(bulk.BulkError):
            bulk.changes(action, given)
    assert bulk.unique_ids([a, a]) == [a]
    with pytest.raises(bulk.BulkError):
        bulk.unique_ids([uuid4() for _ in range(bulk.MAX_TICKETS + 1)])
    with pytest.raises(bulk.BulkError):
        bulk.unique_ids([])


def test_auto_close_setting() -> None:
    assert auto_close.check(3, "  hi ") == (3, "hi")
    assert auto_close.check(1, "   ") == (1, None)
    for days, notice in ((0, None), (366, None), (3, "x" * 5001)):
        with pytest.raises(auto_close.AutoCloseError):
            auto_close.check(days, notice)
    assert auto_close.closed_line(1).endswith("for 1 day")


def test_transcript_keeps_the_newest_when_too_long() -> None:
    def msg(kind: str, body: str, minute: int) -> dict[str, object]:
        return {"kind": kind, "author_name": None, "body": body, "files": ["a.pdf"],
                "created_at": NOW + timedelta(minutes=minute)}  # fmt: skip

    text, count = transcript.body(
        ticket_label="T-1",
        ticket_subject="S",
        customer_name="Priya",
        messages=[msg("customer", "hi", 0), msg("ai", "hello", 1)],
    )
    assert count == 2 and "Priya · 20 Nov 2026 12:00 UTC\nhi\nFiles: a.pdf" in text
    assert "AI agent" in text
    big = "x" * (transcript.MAX_CHARS // 2)
    text, count = transcript.body(ticket_label="T-1", ticket_subject="S", customer_name="P",
                                  messages=[msg("customer", big, i) for i in range(4)])  # fmt: skip
    assert count == 1 and "(3 earlier messages are not included.)" in text
    assert transcript.domain_of("Boss@Partner.Example") == "partner.example"
