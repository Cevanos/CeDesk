"""The customer's own tickets in the portal (md P2-P4).

Every read and write is filtered by the signed-in customer's contact: another
customer's ticket is "not found", never "forbidden" (it must not seem to exist).

- P2 my tickets: subject, status label ("Waiting on you" for Pending), number, created.
- P3 new ticket: pick a type, fill its fields, describe the problem. The subject is the
  first 60 characters. It is made through `create_from_channel` (channel `portal`), so
  rules, routing, SLA and the outbox events run as for any new ticket.
- P4 the ticket: only customer, agent and AI messages ("You", "Acme assistant",
  "Neha · Acme Support"), never notes or system lines. A reply goes through
  `customer_reply`, so a reply on a resolved ticket reopens it (a plain thanks doesn't).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain import portal as rules
from cedesk.helpdesk.domain.tickets import Channel
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import catalog as catalog_repo
from cedesk.helpdesk.repo import portal as repo
from cedesk.helpdesk.service import attachments, customer_replies, tickets
from cedesk.helpdesk.service.attachments import Download
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound, TooManyAttempts
from cedesk.helpdesk.service.portal import Customer
from cedesk.storage import ObjectStore

TICKET_NOT_FOUND = "Ticket not found."
MESSAGE_MAX = 50_000


@dataclass(frozen=True)
class MyTickets:
    tickets: Sequence[RowMapping]
    next_cursor: str | None


@dataclass(frozen=True)
class Line:
    """One message as the customer sees it."""

    id: UUID
    author: str
    from_customer: bool
    body: str
    created_at: datetime
    attachments: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class MyTicket:
    ticket: RowMapping
    messages: Sequence[Line]  # oldest to newest
    older_messages_cursor: str | None


async def check_customer_type(s: AsyncSession, type_id: UUID | None) -> None:
    """A type hidden from customers is refused as if it didn't exist."""
    if type_id is None:
        return
    row = await catalog_repo.get_type(s, type_id)
    if row is None or not row["for_customers"]:
        raise Invalid("That ticket type does not exist.")


def _before(cursor: str | None, kind: str) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        return paging.decode_time(cursor, kind)
    except paging.CursorError as exc:
        raise Invalid(str(exc)) from exc


async def my_tickets(
    sm: Sessionmaker,
    customer: Customer,
    *,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> MyTickets:
    before = _before(cursor, "portal-ticket")
    async with workspace_session(sm, customer.workspace_id) as s:
        rows = await repo.my_tickets(s, customer.contact_id, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "portal-ticket")
    return MyTickets(page, next_cursor)


async def ticket_types(sm: Sessionmaker, customer: Customer) -> Sequence[RowMapping]:
    """The types a customer picks from on "New ticket", with their fields."""
    async with workspace_session(sm, customer.workspace_id) as s:
        return list(await catalog_repo.list_types(s, customers_only=True))


async def my_ticket(
    sm: Sessionmaker,
    customer: Customer,
    ticket_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> MyTicket:
    before = _before(cursor, "portal-message")
    async with workspace_session(sm, customer.workspace_id) as s:
        ticket = await repo.my_ticket(s, customer.contact_id, ticket_id)
        if ticket is None:
            raise NotFound(TICKET_NOT_FOUND)
        rows = await repo.visible_messages(s, ticket_id, before=before, limit=limit + 1)
        workspace = await auth_repo.workspace_row(s)
    name = str(workspace["name"]) if workspace else ""
    page, older = paging.time_page(rows, limit, "portal-message")
    lines = [
        Line(
            id=r["id"],
            author=rules.author_label(r["kind"], r["author_name"], customer.name, name),
            from_customer=r["kind"] == "customer",
            body=r["body"],
            created_at=r["created_at"],
            attachments=list(r["attachments"]),
        )
        for r in reversed(page)
    ]
    return MyTicket(ticket, lines, older)


async def open_ticket(
    sm: Sessionmaker,
    customer: Customer,
    *,
    message: str,
    type_id: UUID | None = None,
    fields: Mapping[str, object] | None = None,
) -> UUID:
    body = message.strip()
    if not body:
        raise Invalid("Describe the problem.")
    if len(body) > MESSAGE_MAX:
        raise Invalid(f"A message can be at most {MESSAGE_MAX} characters.")
    async with workspace_session(sm, customer.workspace_id) as s:
        await check_customer_type(s, type_id)
        since = datetime.now(UTC) - timedelta(hours=1)
        if await repo.tickets_since(s, customer.contact_id, since) >= rules.TICKETS_PER_HOUR:
            raise TooManyAttempts("You've opened a lot of tickets. Try again in an hour.", 3600)
    view = await tickets.create_from_channel(
        sm,
        customer.workspace_id,
        tickets.NewTicket(
            subject=rules.subject_from(body),  # remade after redaction
            subject_from_description=True,
            contact_email=customer.email,
            contact_name=customer.name,
            description=body,
            channel=Channel.portal,
            type_id=type_id,
            fields=fields or {},
        ),
    )
    ticket_id: UUID = view.ticket["id"]
    return ticket_id


async def reply(sm: Sessionmaker, customer: Customer, ticket_id: UUID, body: str) -> None:
    async with workspace_session(sm, customer.workspace_id) as s:
        if await repo.my_ticket(s, customer.contact_id, ticket_id) is None:
            raise NotFound(TICKET_NOT_FOUND)
    # The ticket's contact never changes, so the check above still holds here.
    await customer_replies.customer_reply(
        sm, customer.workspace_id, ticket_id, body, author_name=customer.name
    )


async def download(
    sm: Sessionmaker,
    store: ObjectStore,
    customer: Customer,
    attachment_id: UUID,
    *,
    seconds: int,
    scanning: bool,
) -> Download:
    """A file on a message the customer can see (never one only on a note)."""
    async with workspace_session(sm, customer.workspace_id) as s:
        row = await repo.visible_attachment(s, customer.contact_id, attachment_id)
    if row is None:
        raise NotFound("File not found.")
    return await attachments.link_for(store, row, seconds=seconds, scanning=scanning)


def status_label(status: str) -> str:
    return rules.status_label(status)
