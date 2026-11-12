"""The CSV importer (md M1-M8): one row per message grouped by ticket id, a bad header is
refused, a bad row is reported and left out, and the file is served in pages."""

import asyncio
from datetime import UTC, datetime

import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.service.imports.csv_source import BadFile, CsvConnector

HEADER = (
    "ticket_id,subject,requester_email,requester_name,status,priority,team,tags,"
    "message_from,message_body,message_created_at,author_name,author_email,"
    "csat_score,csat_comment,field.order_id,resolved_at,ticket_created_at"
)


def make(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def read(data: bytes) -> CsvConnector:
    return CsvConnector(data, filename="tickets.csv")


def test_rows_are_grouped_into_tickets_in_time_order() -> None:
    c = read(
        make(
            "T1,Refund,ann@example.org,Ann,solved,high,Billing,vip;refund,agent,"
            "Done,2025-03-01T11:00:00Z,Sam,sam@example.com,5,Thanks!,1042,"
            "2025-03-01T11:30:00Z,2025-03-01T10:00:00Z",
            "T1,,,,,,,,customer,Where is it?,2025-03-01T10:00:00Z,,,,,,,",
        )
    )
    assert c.bad_rows == []
    ticket = asyncio.run(c.page("tickets", None)).items[0]
    assert ticket.external_id == "T1" and ticket.subject == "Refund"
    assert [m.kind for m in ticket.messages] == ["customer", "agent"]  # oldest first
    assert ticket.status == "solved" and ticket.priority == "high"
    assert ticket.tags == ["vip", "refund"]
    assert ticket.fields == {"order_id": "1042"}
    assert ticket.csat is not None and ticket.csat.score == 5 and ticket.csat.comment == "Thanks!"
    assert ticket.created_at == datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    assert ticket.resolved_at == datetime(2025, 3, 1, 11, 30, tzinfo=UTC)
    assert ticket.requester.email == "ann@example.org" and ticket.requester.name == "Ann"
    assert ticket.messages[1].author_email == "sam@example.com"


def test_the_ticket_date_defaults_to_its_first_message_and_a_missing_date_zone_is_utc() -> None:
    c = read(make("T9,Hi,bo@example.org,,,,,,customer,Hello,2025-03-01 09:00:00,,,,,,,"))
    ticket = asyncio.run(c.page("tickets", None)).items[0]
    assert ticket.created_at == datetime(2025, 3, 1, 9, 0, tzinfo=UTC)
    assert ticket.requester.name == "bo"  # no name: the part before the @


def test_a_bad_header_is_refused_with_what_is_missing() -> None:
    with pytest.raises(BadFile, match="message_body"):
        read(b"ticket_id,subject,requester_email,message_from,message_created_at\n")
    with pytest.raises(BadFile, match="UTF-8"):
        read(b"\xff\xfe\x00")


def test_a_bad_row_is_reported_with_its_line_and_never_stops_the_rest() -> None:
    c = read(
        make(
            ",No id,a@example.org,,,,,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,",
            "T1,Ok,a@example.org,,,,,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,",
            "T2,No body,a@example.org,,,,,,customer,,2025-03-01T10:00:00Z,,,,,,,",
            "T3,Bad date,a@example.org,,,,,,customer,Hi,yesterday,,,,,,,",
            "T4,Bad from,a@example.org,,,,,,robot,Hi,2025-03-01T10:00:00Z,,,,,,,",
            "T5,Bad csat,a@example.org,,,,,,customer,Hi,2025-03-01T10:00:00Z,,,9,,,,",
        )
    )
    assert [b.line for b in c.bad_rows] == [2, 4, 5, 6, 7]
    assert asyncio.run(c.discover()).tickets == 1  # only T1 survived
    assert "5 rows" in c.warnings()[0] and "line 2: no ticket_id" in c.warnings()[0]


def test_a_ticket_with_no_requester_email_is_reported_not_imported() -> None:
    c = read(make("T1,Hi,,,,,,,customer,Hello,2025-03-01T10:00:00Z,,,,,,,"))
    assert asyncio.run(c.discover()).tickets == 0
    assert "no requester_email" in c.warnings()[0]


def test_groups_come_from_the_team_column() -> None:
    c = read(
        make(
            "T1,A,a@example.org,,,,Billing,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,",
            "T2,B,b@example.org,,,,Billing,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,",
            "T3,C,c@example.org,,,,Shipping,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,",
        )
    )
    groups = asyncio.run(c.groups())
    assert [g.name for g in groups] == ["Billing", "Shipping"]
    assert asyncio.run(c.discover()).groups == 2
    tickets = asyncio.run(c.page("tickets", None)).items
    assert tickets[0].group_id == "Billing" and tickets[2].group_id == "Shipping"


def test_contacts_are_the_distinct_requesters() -> None:
    c = read(
        make(
            "T1,A,Ann@Example.org,Ann,,,,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,",
            "T2,B,ann@example.org,Ann,,,,,customer,Hi,2025-03-02T10:00:00Z,,,,,,,",
        )
    )
    contacts = asyncio.run(c.page("contacts", None)).items
    assert [x.email for x in contacts] == ["ann@example.org"]
    assert asyncio.run(c.discover()).contacts == 1


def test_a_csv_has_no_articles_or_saved_replies() -> None:
    c = read(make("T1,A,a@example.org,,,,,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,"))
    for kind in ("articles", "replies"):
        page = asyncio.run(c.page(kind, None))
        assert page.items == [] and page.cursor is None


def test_pages_follow_the_cursor_to_the_end() -> None:
    rows = [
        f"T{i},S{i},u{i}@example.org,,,,,,customer,Hi,2025-03-01T10:00:00Z,,,,,,,"
        for i in range(rules.PAGE_MAX + 30)
    ]
    c = read(make(*rows))
    first = asyncio.run(c.page("tickets", None))
    assert len(first.items) == rules.PAGE_MAX and first.cursor == {"offset": rules.PAGE_MAX}
    second = asyncio.run(c.page("tickets", first.cursor))
    assert len(second.items) == 30 and second.cursor is None
    seen = {t.external_id for t in first.items} | {t.external_id for t in second.items}
    assert len(seen) == rules.PAGE_MAX + 30


def test_a_byte_order_mark_and_header_case_are_forgiven() -> None:
    header = "﻿Ticket ID,Subject,Requester Email,Message From,Message Body,Message Created At"
    data = (header + "\nT1,Hi,a@example.org,customer,Hello,2025-03-01T10:00:00Z\n").encode()
    assert asyncio.run(read(data).discover()).tickets == 1
