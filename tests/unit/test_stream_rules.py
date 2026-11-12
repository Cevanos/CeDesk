"""Live updates (md C11, P7): which events are published, to whom, and what they may carry.

The one promise: a stream says that something changed, never what it says. Ids and the names
of what changed, nothing else. And `security.alert` (an email to the Admins) is not published."""

import json

import pytest

from cedesk.helpdesk.domain import stream as rules
from cedesk.helpdesk.domain.events import Event

TICKET = {
    "id": "7d4f0c1e-0000-4000-8000-000000000001",
    "number": 1042,
    "subject": "My card 4242 4242 4242 4242 was charged",
    "status": "open",
    "priority": "high",
    "team": "Billing",
    "topic": "Refund / return",
}


def outbox(event: Event, **extra: object) -> dict[str, object]:
    return {
        "event": event.value,
        "ticket": TICKET,
        "id": "e1",
        "occurred_at": "2026-11-12",
        **extra,
    }


def test_a_new_ticket_goes_to_agents_only_and_carries_ids() -> None:
    got = rules.from_outbox(Event.ticket_created.value, outbox(Event.ticket_created))
    assert got is not None and got.agent_kind == "ticket.created"
    assert got.agent_payload == {"ticket_id": TICKET["id"], "number": 1042}
    assert got.customer_kind is None


def test_a_change_goes_to_agents_with_what_changed_and_to_the_customer_only_for_status() -> None:
    both = rules.from_outbox(
        Event.ticket_updated.value, outbox(Event.ticket_updated, changes=["status", "priority"])
    )
    assert both is not None
    assert both.agent_payload["changes"] == ["status", "priority"]
    assert both.customer_kind == "ticket.updated"
    assert both.customer_payload == {
        "ticket_id": TICKET["id"],
        "number": 1042,
        "changes": ["status"],
    }  # fmt: skip  (the customer is not told about priority)

    internal = rules.from_outbox(
        Event.ticket_updated.value, outbox(Event.ticket_updated, changes=["priority", "tags"])
    )
    assert internal is not None and internal.customer_kind is None


def test_a_resolved_ticket_goes_to_both() -> None:
    got = rules.from_outbox(Event.ticket_resolved.value, outbox(Event.ticket_resolved))
    assert got is not None
    assert (got.agent_kind, got.customer_kind) == ("ticket.resolved", "ticket.resolved")


@pytest.mark.parametrize(("kind", "customer_sees"), [
    ("customer", True), ("agent", True), ("ai", True), ("note", False), ("system", False),
])  # fmt: skip
def test_a_customer_never_hears_of_a_note_or_a_system_line(kind: str, customer_sees: bool) -> None:
    message = {"id": "m1", "kind": kind, "author_member_id": "x"}
    got = rules.from_outbox(
        Event.message_created.value, outbox(Event.message_created, message=message)
    )
    assert got is not None and got.agent_kind == "message.created"  # agents see every kind
    assert got.agent_payload["message_kind"] == kind
    assert (got.customer_kind == "message.created") is customer_sees
    assert (got.customer_payload is not None) is customer_sees


def test_an_assignment_carries_who_moved_it_and_goes_to_agents_only() -> None:
    got = rules.from_outbox(
        Event.assignment_changed.value,
        outbox(Event.assignment_changed, **{"from": None, "to": "m2", "by": "m1"}),
    )
    assert got is not None and got.customer_kind is None
    assert got.agent_payload == {
        "ticket_id": TICKET["id"], "number": 1042, "from": None, "to": "m2", "by": "m1",
    }  # fmt: skip


def test_accepting_a_chat_is_a_chat_event() -> None:
    got = rules.from_outbox(Event.handover_accepted.value, outbox(Event.handover_accepted, by="m1"))
    assert got is not None and got.agent_kind == "chat.accepted" and got.customer_kind is None


@pytest.mark.parametrize(
    "event",
    ["security.alert", "contact.updated", "nonsense", "ticket.deleted"],
)
def test_events_that_are_not_ticket_events_are_never_published(event: str) -> None:
    assert rules.from_outbox(event, outbox(Event.ticket_created)) is None
    assert rules.from_outbox(event, {"what": "A member was added.", "by": "m1"}) is None


def test_an_event_with_no_ticket_is_never_published() -> None:
    assert rules.from_outbox(Event.ticket_created.value, {"event": "ticket.created"}) is None


def test_security_alerts_are_not_a_kind_anyone_can_receive() -> None:
    assert "security.alert" not in rules.AGENT_KINDS
    assert "security.alert" not in rules.CUSTOMER_KINDS
    assert rules.CUSTOMER_KINDS <= rules.AGENT_KINDS | {"message.created"}


@pytest.mark.parametrize("event", [e for e in Event if e is not Event.security_alert])
def test_nothing_published_ever_carries_a_subject_a_text_or_an_address(event: Event) -> None:
    payload = outbox(
        event,
        changes=["subject", "status"],
        message={"id": "m1", "kind": "agent", "author_member_id": "x", "body": "secret text"},
        **{"from": "a", "to": "b", "by": "c"},
    )
    got = rules.from_outbox(event.value, payload)
    if got is None:
        return
    published = json.dumps([dict(got.agent_payload), dict(got.customer_payload or {})])
    for leaked in ("4242", "charged", "Billing", "Refund", "secret text", "@"):
        assert leaked not in published
    assert got.agent_kind in rules.AGENT_KINDS
    assert got.customer_kind is None or got.customer_kind in rules.CUSTOMER_KINDS


# The wire


def test_a_frame_has_an_id_a_kind_and_compact_json() -> None:
    assert rules.frame(41, "ticket.updated", {"ticket_id": "t1", "changes": ["status"]}) == (
        'id: 41\nevent: ticket.updated\ndata: {"ticket_id":"t1","changes":["status"]}\n\n'
    )


def test_a_frame_never_contains_a_line_break_in_its_data() -> None:
    text = rules.frame(1, "notification", {"x": "a\nb\r\nc"})
    assert text.count("\n") == 4  # id, event, data, and the blank line that ends it
    assert text.endswith("\n\n")


def test_the_control_frames() -> None:
    assert rules.hello() == "retry: 3000\n\n"
    assert rules.heartbeat() == ": ping\n\n"
    assert rules.reset() == "event: reset\ndata: {}\n\n"
    assert rules.end("signed_out") == 'event: end\ndata: {"reason": "signed_out"}\n\n'


@pytest.mark.parametrize(
    ("sent", "expected"),
    [("41", 41), (" 7 ", 7), ("0", 0), (None, None), ("", None), ("abc", None), ("-1", None),
     ("1.5", None), ("1 or 1=1", None), ("9" * 30, None)],
)  # fmt: skip
def test_a_last_event_id_is_a_number_or_nothing(sent: str | None, expected: int | None) -> None:
    assert rules.parse_last_event_id(sent) == expected
