"""CSAT (md P5, T15): the customer rates a resolved ticket 1-5 with an optional comment.

Three ways in, one rating per ticket (rating again changes it):
- the portal: `rate_in_portal`, on the customer's own resolved ticket;
- the emailed survey link: `CsatHandler` sends it after `ticket.resolved` (outbox, so a
  retry never sends two), then `link_info` / `rate_by_link` from the public page;
- the Cevanos widget asks in the chat itself (md P7), so chat customers get no email.

Each rating adds a system line "Customer rated 4/5: ..." for the agents. No survey for a
ticket merged into another (md X1), when the Admin turned it off, or with no sender
(then a warning in the worker log).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as auth_rules
from cedesk.helpdesk.domain import csat as rules
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain.email import new_message_id
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import csat as repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.errors import Conflict, Gone, Invalid, NotFound
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.helpdesk.service.portal import Customer

logger = logging.getLogger("cedesk.csat")

BAD_LINK = "This survey link is invalid or has expired."
NOT_RESOLVED = "You can rate a ticket once it is resolved."


@dataclass(frozen=True)
class Rating:
    score: int
    comment: str | None
    source: str
    rated_at: datetime


@dataclass(frozen=True)
class LinkInfo:
    """What the public rating page shows."""

    workspace_name: str
    ticket_label: str
    subject: str
    rating: Rating | None


def _rating(row: RowMapping | None) -> Rating | None:
    if row is None:
        return None
    return Rating(int(row["score"]), row["comment"], str(row["source"]), row["rated_at"])


def _checked(score: int, comment: str | None) -> tuple[int, str | None]:
    try:
        return rules.check(score, comment)
    except rules.BadRating as exc:
        raise Invalid(str(exc)) from exc


async def _save(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    score: int,
    comment: str | None,
    source: str,
) -> Rating:
    """Inside a transaction. The ticket must be resolved (and not merged away)."""
    ticket = await repo.lock_ticket(s, ticket_id)
    if ticket is None:
        raise NotFound("Ticket not found.")
    if ticket["status"] != "resolved" or ticket["merged_into_id"] is not None:
        raise Conflict(NOT_RESOLVED)
    before = await repo.for_ticket(s, ticket_id)
    await repo.save(
        s,
        workspace_id=workspace_id,
        ticket_id=ticket_id,
        contact_id=ticket["contact_id"],
        score=score,
        comment=comment,
        source=source,
    )
    # A line for the agents, unless the customer sent the same rating again.
    if before is None or (before["score"], before["comment"]) != (score, comment):
        await ticket_repo.insert_message(
            s,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            kind="system",
            body=rules.rated_line(score, comment, changed=before is not None),
            author_member_id=None,
            author_name=None,
        )
    saved = _rating(await repo.for_ticket(s, ticket_id))
    assert saved is not None
    return saved


async def rate_in_portal(
    sm: Sessionmaker, customer: Customer, ticket_id: UUID, score: int, comment: str | None
) -> Rating:
    score, text = _checked(score, comment)
    async with workspace_session(sm, customer.workspace_id) as s:
        ticket = await repo.lock_ticket(s, ticket_id)
        if ticket is None or ticket["contact_id"] != customer.contact_id:
            raise NotFound("Ticket not found.")
        return await _save(s, customer.workspace_id, ticket_id, score, text, "portal")


async def _by_link(s: AsyncSession, token: str) -> tuple[RowMapping, Rating | None]:
    ticket = await repo.link_ticket(s, auth_rules.token_hash(token))
    if ticket is None:
        raise Gone(BAD_LINK)
    return ticket, _rating(await repo.for_ticket(s, ticket["id"]))


async def link_info(sm: Sessionmaker, token: str) -> LinkInfo:
    workspace_id = auth_rules.token_workspace(token)
    if workspace_id is None:
        raise Gone(BAD_LINK)
    async with workspace_session(sm, workspace_id) as s:
        ticket, rating = await _by_link(s, token)
        workspace = await auth_repo.workspace_row(s)
    return LinkInfo(
        workspace_name=str(workspace["name"]) if workspace else "",
        ticket_label=label(int(ticket["number"])),
        subject=str(ticket["subject"]),
        rating=rating,
    )


async def rate_by_link(sm: Sessionmaker, token: str, score: int, comment: str | None) -> Rating:
    score, text = _checked(score, comment)
    workspace_id = auth_rules.token_workspace(token)
    if workspace_id is None:
        raise Gone(BAD_LINK)
    async with workspace_session(sm, workspace_id) as s:
        ticket, _ = await _by_link(s, token)
        ticket_id: UUID = ticket["id"]
        return await _save(s, workspace_id, ticket_id, score, text, "email")


class CsatHandler:
    """Emails the survey after `ticket.resolved` (outbox: after commit, retried). The
    link row and the email are written in one transaction keyed by the effect key, so a
    retried event never sends a second survey."""

    name = "csat"
    events = frozenset({events.Event.ticket_resolved.value})

    def __init__(self, sm: Sessionmaker, *, base_url: str, fallback: Sender | None) -> None:
        self._sm = sm
        self._base_url = base_url
        self._fallback = fallback

    async def handle(self, event: Event, effect_key: str) -> None:
        if event.ticket_id is None:
            return
        ws, ticket_id = event.workspace_id, event.ticket_id
        async with workspace_session(self._sm, ws) as s:
            if await repo.link_made(s, effect_key) or not await repo.survey_on(s):
                return
            ticket = await ticket_repo.ticket_detail(s, ticket_id)
            if ticket is None or ticket["status"] != "resolved":
                return  # reopened before the worker got here
            if ticket["merged_into_id"] is not None:
                return  # merged into another ticket: no survey (md X1)
            email = str(ticket["contact_email"])
            if not rules.deliverable(email) or await repo.for_ticket(s, ticket_id):
                return  # a chat customer, or already rated
            sender = await self._sender(s)
            if sender is None:
                # Skipped, not failed: like the portal code email (no failed-event noise).
                logger.warning(
                    "csat survey not sent: no support address and no CEDESK_MAIL_FROM_ADDRESS",
                    extra={"workspace_id": ws},
                )
                return
            workspace = await auth_repo.workspace_row(s)
            name = str(workspace["name"]) if workspace else ""
            token = auth_rules.new_token(ws)
            await repo.insert_link(
                s,
                workspace_id=ws,
                ticket_id=ticket_id,
                token_hash=auth_rules.token_hash(token),
                effect_key=effect_key,
                expires_at=datetime.now(UTC) + rules.LINK_LIFETIME,
            )
            ticket_label = label(int(ticket["number"]))
            await email_repo.enqueue(
                s,
                ws,
                ticket_id=ticket_id,
                message_id=None,
                from_address=sender.address,
                from_name=sender.name,
                to=[email],
                cc=[],
                subject=rules.survey_subject(ticket_label),
                body=rules.survey_email(
                    str(ticket["contact_name"]),
                    name,
                    ticket_label,
                    str(ticket["subject"]),
                    self._base_url,
                    token,
                ),
                header_message_id=new_message_id(sender.address),
                in_reply_to=None,
                references=[],
                sensitive=True,  # the link rates the ticket: wiped once sent
            )
        logger.info("csat survey queued", extra={"workspace_id": ws})

    async def _sender(self, s: AsyncSession) -> Sender | None:
        default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
        if default is not None:
            return Sender(str(default["address"]), str(default["name"]))
        return self._fallback
