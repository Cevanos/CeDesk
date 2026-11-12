"""Import rules (md M1-M8): how a source's words map onto CeDesk's, the team suggested for a
group, the run options, and the small helpers the framework leans on."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import Message


def test_status_maps_and_an_unknown_word_is_open() -> None:
    mapping = {"solved": "resolved", "hold": "pending", "open": "open"}
    assert rules.status("Solved", mapping) == "resolved"
    assert rules.status(" hold ", mapping) == "pending"
    # A ticket nobody can see because its status was unknown would be worse than an open one.
    assert rules.status("weird", mapping) == "open"
    assert rules.status(None, mapping) == "open"


def test_priority_and_channel_fall_back_sensibly() -> None:
    assert rules.priority("Critical") == "urgent"
    assert rules.priority("normal") == "medium"
    assert rules.priority(None) == "medium"
    assert rules.channel("WhatsApp") == "chat"
    assert rules.channel("live chat") == "chat"
    assert rules.channel("Voice") == "phone"
    assert rules.channel("contact_form") == "web"  # Gorgias
    assert rules.channel("help-center") == "web"
    assert rules.channel("carrier pigeon") == "api"


def test_team_is_suggested_by_name_then_by_words_and_only_if_it_exists() -> None:
    teams = ["Billing", "Shipping", "Tech"]
    assert rules.suggest_team("billing", teams) == "Billing"
    assert rules.suggest_team("Payments & accounts", teams) == "Billing"
    assert rules.suggest_team("Delivery issues", teams) == "Shipping"
    assert rules.suggest_team("Returns", teams) == "Shipping"
    assert rules.suggest_team("Tier 2 support", teams) == "Tech"
    assert rules.suggest_team("Sales", teams) is None
    assert rules.suggest_team("Billing", ["Shipping"]) is None  # no such team: no guess


def test_tags_fit_cedesks_rule_and_carry_the_import_tag() -> None:
    tags = rules.clean_tags(["VIP Customer", "refund!!", "vip customer", "", "x" * 60], "zendesk")
    assert tags[0] == "imported-zendesk"
    assert "vip-customer" in tags and tags.count("vip-customer") == 1
    assert "refund" in tags
    assert all(len(t) <= 40 for t in tags)
    assert len(rules.clean_tags([f"t{i}" for i in range(50)], "csv")) == rules.TAG_MAX


def test_custom_fields_become_plain_text_under_safe_keys() -> None:
    got = rules.clean_fields({"Order ID": 1042, "bad key!": "x", "empty": "", "9lives": "no"})
    assert got == {"order_id": "1042", "bad_key": "x"}


def test_a_contact_without_an_email_gets_a_placeholder_that_is_never_real() -> None:
    assert rules.placeholder_email("intercom", "Abc 123") == "intercom-abc-123@intercom.invalid"
    assert rules.placeholder_email("csv", "!!!") == "csv-unknown@csv.invalid"


def test_text_helpers() -> None:
    assert rules.subject("  two   words ") == "two words"
    assert rules.subject("") == "(no subject)"
    assert len(rules.subject("x" * 400)) == rules.SUBJECT_MAX
    assert rules.unknown_agent("Sana K.", "zendesk") == "Sana K. (Zendesk, not in CeDesk)"
    line = rules.history_line("zendesk", "Billing", "123", "solved")
    assert line == "Imported from Zendesk with full history · Billing · source ID 123 · was solved"
    assert rules.history_line("csv", None, "7", "") == (
        "Imported from CSV file with full history · source ID 7 · was open"
    )


def test_options_default_to_everything_in_run_order() -> None:
    chosen = rules.options(None)
    assert chosen.kinds == rules.KINDS and chosen.team_map == {}
    team = uuid4()
    chosen = rules.options(
        {"kinds": ["replies", "tickets"], "team_map": {"g1": str(team), "g2": None}}
    )
    assert chosen.kinds == ("tickets", "replies")  # always in run order
    assert chosen.team_map == {"g1": team, "g2": None}
    assert chosen.as_json() == {
        "kinds": ["tickets", "replies"],
        "team_map": {"g1": str(team), "g2": None},
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"kinds": []},
        {"kinds": ["tickets", "nonsense"]},
        {"kinds": "tickets"},
        {"team_map": {"g": "not-a-uuid"}},
        {"team_map": ["g"]},
    ],
)
def test_bad_options_are_refused(raw: dict[str, object]) -> None:
    with pytest.raises(rules.BadOptions):
        rules.options(raw)


def test_a_run_goes_through_the_kinds_in_order() -> None:
    kinds = ("contacts", "tickets", "replies")
    assert rules.next_kind(kinds, []) == "contacts"
    assert rules.next_kind(kinds, ["contacts"]) == "tickets"
    assert rules.next_kind(kinds, ["contacts", "tickets", "replies"]) is None


def test_a_rate_limit_waits_for_retry_after_but_never_forever() -> None:
    assert rules.wait_seconds(30) == 30
    assert rules.wait_seconds(None) == rules.DEFAULT_RATE_WAIT
    assert rules.wait_seconds(0) == rules.DEFAULT_RATE_WAIT
    assert rules.wait_seconds(10**9) == rules.MAX_RATE_WAIT


# Files, syncing and history (M7, M9, M10): the rules that need no database


def test_a_file_kept_as_a_link_says_what_and_why() -> None:
    line = rules.link_line("movie.mp4", 31_457_280, "https://f.example/m", "over 25 MB")
    assert (
        line == "Attachment kept as a link (over 25 MB): movie.mp4 (30.0 MB) · https://f.example/m"
    )
    assert "MB)" not in rules.link_line("a.txt", None, "https://f.example/a", "gone")


def test_a_result_is_what_an_admin_reads() -> None:
    from cedesk.helpdesk.service.imports.framework import result_word

    assert result_word("queued", False) == "running" == result_word("running", True)
    assert result_word("done", False) == "finished" and result_word("done", True) == "syncing"
    assert result_word("failed", True) == "failed"


def test_a_message_is_new_by_its_source_id_else_by_kind_and_second() -> None:
    from cedesk.helpdesk.service.imports.framework import _is_new

    at = datetime(2025, 1, 1, 10, 0, 5, 700_000, tzinfo=UTC)
    known = ({"m1"}, {("agent", int(at.timestamp()))})
    assert _is_new(Message("agent", "x", at, "m1"), known) is False
    assert _is_new(Message("agent", "x", at, "m2"), known) is True
    assert _is_new(Message("agent", "x", at), known) is False  # no id: same kind and second
    assert _is_new(Message("customer", "x", at), known) is True
    assert _is_new(Message("agent", "x", at, "m1"), None) is True  # a ticket not here yet


def test_syncing_is_offered_for_every_source_but_a_csv_file() -> None:
    assert set(rules.SYNC_SOURCES) == set(rules.AVAILABLE_SOURCES)
    assert "csv" not in rules.SYNC_SOURCES
    assert rules.SYNC_EVERY_SECONDS == 300 and rules.FILE_MAX == 25 * 1024 * 1024
