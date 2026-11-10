"""Agent replies, private notes with @mentions, and each member's notifications.

A reply goes to the customer (on an email ticket it is queued to be emailed; on a Cevanos
chat the worker sends it with the connector, after Accept); a note is for the team only.
Neither changes the ticket's status (T17).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events, paging
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.placeholders import render
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import catalog, outbox, tickets
from cedesk.helpdesk.repo import messages as repo
from cedesk.helpdesk.repo.messages import Row
from cedesk.helpdesk.service import attachments, email, idempotency, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.members import require

BODY_MAX = 50_000
MAX_MENTIONS = 20
NOT_ACCEPTED = "Accept the chat first. Notes are allowed before that."
NO_FILES_ON_CHAT = "Files can't be sent into a Cevanos chat. Add them to a note instead."


class MessageKind(StrEnum):
    reply = "reply"
    note = "note"


_STORED_KIND = {MessageKind.reply: "agent", MessageKind.note: "note"}


@dataclass(frozen=True)
class Inbox:
    notifications: Sequence[Row]
    unread: int
    next_cursor: str | None = None


@dataclass(frozen=True)
class MessagePage:
    messages: Sequence[Row]  # newest first
    next_cursor: str | None


async def add_message(
    s: AsyncSession,
    caller: Caller,
    detail: Mapping[Any, Any],
    *,
    kind: MessageKind,
    body: str,
    who: str,
    mentions: Sequence[UUID] = (),
    attachment_ids: Sequence[UUID] = (),
) -> UUID:
    """Saves a reply or note inside the caller's transaction, with the ticket locked:
    fills a reply's placeholders, sends the uploaded files with it, notifies the
    @mentioned and writes `message.created`. Permissions and the mention list are the
    caller's job (`post_message`, macros)."""
    ticket_id = detail["id"]
    if kind is MessageKind.reply and not detail["accepted"]:
        raise Conflict(NOT_ACCEPTED)  # md 7.1 point 3: a handover waits for Accept
    if kind is MessageKind.reply and attachment_ids and detail["source"] == "cevanos":
        raise Invalid(NO_FILES_ON_CHAT)  # Cevanos's reply takes text only
    if kind is MessageKind.reply:
        body = render(
            body,
            customer_name=str(detail["contact_name"]),
            agent_name=who,
            fields=detail["fields"],
        )
    if len(body) > BODY_MAX:
        raise Invalid(f"A message can be at most {BODY_MAX} characters.")

    message_id = await tickets.insert_message(
        s,
        workspace_id=caller.workspace_id,
        ticket_id=ticket_id,
        kind=_STORED_KIND[kind],
        body=body,
        author_member_id=caller.member_id,
        author_name=who,
    )
    emailed = kind is MessageKind.reply and detail["channel"] == "email"
    # Before the email is queued: the worker reads the files from the message.
    await attachments.attach_to_message(
        s, caller, ticket_id, message_id, attachment_ids, emailed=emailed
    )
    if kind is MessageKind.reply:
        await repo.ticket_replied(s, ticket_id)
        await sla.refresh(s, ticket_ids=[ticket_id])  # the first-reply clock may stop
        if emailed:
            await email.queue_reply(s, caller.workspace_id, detail, message_id, body, who)
    else:
        await repo.ticket_touched(s, ticket_id)

    if mentions:
        await repo.add_mentions(s, caller.workspace_id, message_id, mentions)
        others = [m for m in mentions if m != caller.member_id]
        if others:
            text = f"{who} mentioned you on {label(detail['number'])}: {detail['subject']}"
            await repo.notify(
                s,
                workspace_id=caller.workspace_id,
                member_ids=others,
                ticket_id=ticket_id,
                kind="mention",
                body=text[:500],
            )
    await outbox.add(
        s,
        caller.workspace_id,
        events.Event.message_created.value,
        ticket_id,
        events.payload(
            events.Event.message_created,
            detail,
            events.message(message_id, _STORED_KIND[kind], caller.member_id),
        ),
    )
    return message_id


def _after(cursor: str | None, kind: str) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        return paging.decode_time(cursor, kind)
    except paging.CursorError as exc:
        raise Invalid(str(exc)) from exc


async def post_message(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    kind: MessageKind,
    body: str,
    mention_ids: Sequence[UUID] = (),
    attachment_ids: Sequence[UUID] = (),
    idem: Idempotency | None = None,
) -> Row:
    """`idem`: with an Idempotency-Key, a repeat returns the message the first one made
    (and sends nothing again)."""
    require(caller, Action.reply if kind is MessageKind.reply else Action.private_note)
    body = body.strip()
    if not body and not attachment_ids:
        raise Invalid("Write something or add a file first.")
    mentions = list(dict.fromkeys(mention_ids))
    if mentions and kind is not MessageKind.note:
        raise Invalid("@mentions go in private notes, so the customer never sees them.")
    if len(mentions) > MAX_MENTIONS:
        raise Invalid(f"Mention at most {MAX_MENTIONS} people.")

    async with workspace_session(sm, caller.workspace_id) as s:
        made = await idempotency.begin(s, caller, idem)
        if made is not None:
            earlier = await repo.get_message(s, made)
            if earlier is None:
                raise NotFound("Message not found.")
            return earlier
        ticket = await tickets.lock_ticket(s, ticket_id)
        detail = await tickets.ticket_detail(s, ticket_id)
        if ticket is None or detail is None:
            raise NotFound("Ticket not found.")
        me = await tickets.member_brief(s, caller.member_id)
        who = str(me["name"]) if me else "someone"
        if mentions and set(mentions) - await catalog.active_members(s, mentions):
            raise Invalid("You can only mention active members of this workspace.")

        message_id = await add_message(
            s, caller, detail, kind=kind, body=body, who=who, mentions=mentions,
            attachment_ids=attachment_ids,
        )  # fmt: skip
        await idempotency.remember(s, caller, idem, message_id)
        row = await repo.get_message(s, message_id)
    assert row is not None
    return row


async def inbox(
    sm: Sessionmaker,
    caller: Caller,
    *,
    unread_only: bool,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> Inbox:
    """The caller's notifications, newest first, a page at a time."""
    before = _after(cursor, "notification")
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.notifications(
            s, caller.member_id, unread_only=unread_only, before=before, limit=limit + 1
        )
        unread = await repo.unread_count(s, caller.member_id)
    page, next_cursor = paging.time_page(rows, limit, "notification")
    return Inbox(notifications=page, unread=unread, next_cursor=next_cursor)


async def ticket_messages(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> MessagePage:
    """A ticket's messages, newest first, a page at a time (the ticket itself shows the
    latest page; this loads older ones)."""
    require(caller, Action.read_ticket)
    before = _after(cursor, "message")
    async with workspace_session(sm, caller.workspace_id) as s:
        if await tickets.ticket_detail(s, ticket_id) is None:
            raise NotFound("Ticket not found.")
        rows = await tickets.messages(s, ticket_id, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "message")
    return MessagePage(messages=page, next_cursor=next_cursor)


async def mark_read(sm: Sessionmaker, caller: Caller, notification_id: UUID) -> None:
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.mark_read(s, caller.member_id, notification_id):
            raise NotFound("Notification not found.")


async def mark_all_read(sm: Sessionmaker, caller: Caller) -> None:
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.mark_all_read(s, caller.member_id)
