"""The mobile agent app's backend (md P8): the queue and the one-tap reply. Same rights
and team scope as the web app; everything else (the ticket screen, notes, notifications)
uses the ordinary routes.

- Queue: tickets not resolved that are the caller's or nobody's; theirs first, then chats
  waiting to be accepted, then by SLA time left. A `team_only` role sees its team's.
- Reply: a person who can reply replies; on a chat handover nobody has accepted, the
  reply accepts it first ("Neha joined the chat (mobile)"), so the customer is told a
  person joined. A light agent can't reply: their text is saved as a private note, and
  the answer says so.
- Push notifications (device tokens, APNs/FCM) are on the md cut line.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import mobile as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import messages, scope, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import NotFound
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.messages import MessageKind

QUEUE_MAX = 100
PREVIEW = 140  # characters of the last message


@dataclass(frozen=True)
class QueueItem:
    id: UUID
    number: int
    subject: str
    status: str
    priority: str
    channel: str
    waiting_to_accept: bool
    sla_due_at: datetime | None
    mine: bool
    contact_name: str
    last_kind: str | None
    last_body: str | None
    last_at: datetime | None
    updated_at: datetime


async def queue(sm: Sessionmaker, caller: Caller, *, limit: int = 50) -> list[QueueItem]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.queue(
            s, caller.member_id, scope.visible_team(caller), min(limit, QUEUE_MAX), PREVIEW
        )
    return [_item(r) for r in rows]


def _item(r: Any) -> QueueItem:
    return QueueItem(
        id=r["id"],
        number=int(r["number"]),
        subject=str(r["subject"]),
        status=str(r["status"]),
        priority=str(r["priority"]),
        channel=str(r["channel"]),
        waiting_to_accept=not bool(r["accepted"]),
        sla_due_at=r["sla_due_at"],
        mine=bool(r["mine"]),
        contact_name=str(r["contact_name"]),
        last_kind=r["last_kind"],
        last_body=r["last_body"],
        last_at=r["last_at"],
        updated_at=r["updated_at"],
    )


@dataclass(frozen=True)
class MobileReply:
    message: Any  # the saved message row
    saved_as: MessageKind  # reply, or note for a light agent
    accepted: bool  # this reply accepted a waiting chat first


async def reply(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    body: str,
    idem: Idempotency | None = None,
) -> MobileReply:
    if not caller.can(Action.reply):
        note = await messages.post_message(
            sm, caller, ticket_id, kind=MessageKind.note, body=body, idem=idem
        )
        return MobileReply(note, MessageKind.note, accepted=False)
    async with workspace_session(sm, caller.workspace_id) as s:
        detail = await ticket_repo.ticket_detail(s, ticket_id)
        if detail is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=True)
    accepted = False
    if not detail["accepted"]:
        await tickets.accept_ticket(sm, caller, ticket_id, mobile=True)
        accepted = True
    sent = await messages.post_message(
        sm, caller, ticket_id, kind=MessageKind.reply, body=body, idem=idem
    )
    return MobileReply(sent, MessageKind.reply, accepted=accepted)
