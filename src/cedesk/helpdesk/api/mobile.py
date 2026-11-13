"""The mobile agent app (md P8): the queue and the one-tap reply. The ticket screen, notes,
notifications and status use the ordinary routes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import (
    CallerDep,
    IdempotencyKey,
    SessionmakerDep,
    idempotency,
    mark_replayed,
)
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.ticket_schemas import MessageOut
from cedesk.helpdesk.service import mobile
from cedesk.helpdesk.service.messages import MessageKind

router = APIRouter(tags=["mobile"])


class QueueItemOut(BaseModel):
    id: UUID
    number: int
    subject: str
    status: str
    priority: str
    channel: str
    waiting_to_accept: bool = Field(description="A chat handover nobody has accepted yet.")
    sla_due_at: datetime | None
    mine: bool = Field(description="Assigned to you; false: nobody's yet.")
    contact_name: str
    last_kind: str | None = Field(description="customer, agent or ai (never notes).")
    last_body: str | None = Field(description="Its first 140 characters.")
    last_at: datetime | None
    updated_at: datetime


class MobileReplyIn(Body):
    body: str = Field(min_length=1, max_length=50_000)


class MobileReplyOut(BaseModel):
    message: MessageOut
    saved_as: MessageKind = Field(
        description="reply; note when you can't reply (a light agent): only the team sees it."
    )
    accepted: bool = Field(description="This reply accepted a waiting chat first.")


@router.get(
    "/me/queue",
    response_model=list[QueueItemOut],
    summary="Your mobile queue",
    description="Tickets not resolved that are yours or nobody's: yours first, then chats "
    "waiting to be accepted, then by SLA time left, then oldest. Team scope applies.",
)
async def my_queue(
    caller: CallerDep,
    sm: SessionmakerDep,
    limit: Annotated[int, Query(ge=1, le=mobile.QUEUE_MAX)] = 50,
) -> list[QueueItemOut]:
    return [QueueItemOut(**i.__dict__) for i in await mobile.queue(sm, caller, limit=limit)]


@router.post(
    "/tickets/{ticket_id}/mobile-reply",
    response_model=MobileReplyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reply from the mobile app",
    description="On a chat handover nobody has accepted, accepts it first (line '<you> "
    "joined the chat (mobile)'), so the customer hears a person joined. A light agent's "
    "text is saved as a private note instead. Idempotency-Key is honoured.",
    responses={
        403: {"description": "The ticket is another team's (view only)"},
        404: {"description": "No such ticket"},
    },
)
async def mobile_reply(
    ticket_id: UUID,
    body: MobileReplyIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> MobileReplyOut:
    idem = idempotency(
        idempotency_key, f"POST /tickets/{ticket_id}/mobile-reply", body.model_dump_json().encode()
    )
    got = await mobile.reply(sm, caller, ticket_id, body=body.body, idem=idem)
    mark_replayed(idem, response)
    return MobileReplyOut(
        message=MessageOut.model_validate(dict(got.message)),
        saved_as=got.saved_as,
        accepted=got.accepted,
    )
