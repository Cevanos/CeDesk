"""What an import can't bring exactly as the source has it is counted, never silent (P0-3,
P0-6, P1-13; audit C8, C9). No database, no network."""

from datetime import UTC, datetime, timedelta

import pytest

from cedesk.helpdesk.domain import imports as rules


def counted(call: object) -> dict[str, int]:
    losses = rules.Losses()
    with rules.collecting(losses):
        assert callable(call)
        call()
    return losses.take()


def test_a_field_value_zero_or_false_is_a_value() -> None:
    got = rules.clean_fields({"Refunds": 0, "Vip": False, "Count": 0.0, "none": None})
    assert got == {"refunds": "0", "vip": "false", "count": "0.0"}


def test_a_field_name_without_latin_letters_is_kept_under_a_made_up_key() -> None:
    got = rules.clean_fields({"注文": "A-1", "Größe": "XL"})
    assert got == {"field_1": "A-1", "gr_e": "XL"}


def test_a_list_value_is_joined_not_shown_as_python() -> None:
    assert rules.clean_fields({"Products": ["a", "b"]}) == {"products": "a, b"}


def test_long_field_values_and_too_many_fields_are_counted() -> None:
    raw = {f"f{i}": "x" * (rules.FIELD_TEXT_MAX + 1 if i == 0 else 1) for i in range(205)}
    losses = rules.Losses()
    with rules.collecting(losses):
        got = rules.clean_fields(raw)
    assert len(got) == rules.FIELD_MAX and len(got["f0"]) == rules.FIELD_TEXT_MAX
    assert losses.take() == {"field_cut": 1, "fields_over_limit": 5}


def test_tags_past_twenty_are_kept_in_more_tags() -> None:
    tags = [f"t{i}" for i in range(25)]
    kept = rules.clean_tags(tags, "zendesk")
    assert len(kept) == rules.TAG_MAX and kept[0] == "imported-zendesk"
    assert rules.more_tags(tags, "zendesk") == "t19, t20, t21, t22, t23, t24"
    assert rules.more_tags(tags[:3], "zendesk") is None


def test_a_body_is_no_longer_cut_at_the_column_size() -> None:
    long = "x" * (rules.BODY_MAX + 10)
    assert rules.body(long) == long  # the framework keeps it whole as a file
    assert counted(lambda: rules.body("y" * (rules.TEXT_SAFETY_MAX + 1))) == {"text_cut": 1}


def test_a_cut_subject_is_counted() -> None:
    assert counted(lambda: rules.subject("s" * 300)) == {"subject_cut": 1}
    assert counted(lambda: rules.subject("short")) == {}


def test_loss_outside_a_run_does_nothing_and_unknown_keys_are_refused() -> None:
    rules.loss("subject_cut")  # nothing collecting: no error
    with pytest.raises(ValueError, match="Unknown loss"):
        rules.loss("made_up")


@pytest.mark.parametrize(
    "value",
    [datetime(1970, 1, 1, tzinfo=UTC), datetime(1979, 12, 31, tzinfo=UTC),
     datetime.now(UTC) + timedelta(days=3), None],
)  # fmt: skip
def test_impossible_dates_are_not_dates(value: datetime | None) -> None:
    assert rules.valid_date(value) is None


def test_a_naive_date_is_utc() -> None:
    assert rules.valid_date(datetime(2020, 5, 1)) == datetime(2020, 5, 1, tzinfo=UTC)


def test_dated_uses_the_record_date_when_valid() -> None:
    real = datetime(2019, 3, 4, tzinfo=UTC)
    noter = rules.RecordFailures()
    assert counted(lambda: rules.dated(noter, "ticket", "1", real)) == {}
    assert noter.take() == []


def test_dated_falls_back_to_the_nearest_source_date_and_counts_it() -> None:
    noter = rules.RecordFailures()
    stand_in = datetime(2018, 1, 1, tzinfo=UTC)
    losses = rules.Losses()
    with rules.collecting(losses):
        got = rules.dated(noter, "ticket", "7", datetime(1970, 1, 1, tzinfo=UTC), stand_in)
    assert got == stand_in and losses.take() == {"date_replaced": 1}
    assert noter.take() == []  # a source date is still the source's


def test_no_date_at_all_is_noted_never_1970_nor_silently_now() -> None:
    noter = rules.RecordFailures()
    losses = rules.Losses()
    before = datetime.now(UTC)
    with rules.collecting(losses):
        got = rules.dated(noter, "ticket", "9", None, datetime(1970, 1, 1, tzinfo=UTC))
    assert got >= before and losses.take() == {"date_missing": 1}
    [note] = noter.take()
    assert (note.kind, note.external_id, note.category) == ("ticket", "9", "incomplete")
    assert "no valid creation date" in note.error


@pytest.mark.parametrize(
    ("state", "failures", "losses", "gaps", "want"),
    [
        ("done", 0, {}, 0, "complete"),
        ("done", 0, {"body_as_file": 3}, 0, "complete"),  # nothing lost
        ("done", 1, {}, 0, "complete_with_exceptions"),
        ("done", 0, {"files_linked": 1}, 0, "complete_with_exceptions"),
        ("done", 0, {}, 2, "complete_with_exceptions"),
        ("failed", 0, {}, 0, "failed"),
        ("running", 0, {}, 0, "in_progress"),
        ("queued", 5, {}, 0, "in_progress"),
    ],
)
def test_outcome_never_says_complete_over_incomplete_data(
    state: str, failures: int, losses: dict[str, int], gaps: int, want: str
) -> None:
    assert rules.outcome(state, failures, losses, gaps) == want
