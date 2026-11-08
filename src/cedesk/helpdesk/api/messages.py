"""Replies and private notes on a ticket, and the signed-in member's notifications."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.ticket_schemas import MessageOut, Text
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.service import messages
from cedesk.helpdesk.service.messages import MessageKind

router = APIRouter()


class MessageIn(Body):
    kind: MessageKind = Field(description="reply: to the customer. note: team only.")
    body: Text = Field(
        description="Replies fill {{customer.first_name}}, {{customer.name}}, {{agent.name}} "
        'and {{fields.<key>}} ("—" when empty).'
    )
    mention_ids: list[UUID] = Field(
        default=[], max_length=20, description="Notes only: members to notify."
    )


class NotificationOut(BaseModel):
    id: UUID
    kind: str
    text: str
    ticket_id: UUID | None
    ticket_label: str | None
    read_at: datetime | None
    created_at: datetime


class InboxOut(BaseModel):
    notifications: list[NotificationOut]
    unread: int
    next_cursor: str | None = Field(description="Send as `cursor` for the next page.")


class MessagePageOut(BaseModel):
    messages: list[MessageOut] = Field(description="Newest first.")
    next_cursor: str | None = Field(description="Send as `cursor` for older messages.")


Cursor = Annotated[
    str | None, Query(max_length=200, description="`next_cursor` from the previous page.")
]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    tags=["tickets"],
    summary="Reply to the customer or add a private note (with @mentions)",
)
async def post_message(
    ticket_id: UUID, body: MessageIn, caller: CallerDep, sm: SessionmakerDep
) -> MessageOut:
    row = await messages.post_message(
        sm, caller, ticket_id, kind=body.kind, body=body.body, mention_ids=body.mention_ids
    )
    return MessageOut.model_validate(dict(row))


@router.get(
    "/tickets/{ticket_id}/messages",
    response_model=MessagePageOut,
    tags=["tickets"],
    summary="A ticket's messages, newest first (to load older ones)",
)
async def list_messages(
    ticket_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Cursor = None,
    limit: Limit = DEFAULT_LIMIT,
) -> MessagePageOut:
    page = await messages.ticket_messages(sm, caller, ticket_id, cursor=cursor, limit=limit)
    return MessagePageOut(
        messages=[MessageOut.model_validate(dict(m)) for m in page.messages],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/notifications",
    response_model=InboxOut,
    tags=["notifications"],
    summary="Your notifications, newest first",
)
async def list_notifications(
    caller: CallerDep,
    sm: SessionmakerDep,
    unread: bool = False,
    cursor: Cursor = None,
    limit: Limit = DEFAULT_LIMIT,
) -> InboxOut:
    box = await messages.inbox(sm, caller, unread_only=unread, cursor=cursor, limit=limit)
    return InboxOut(
        notifications=[
            NotificationOut.model_validate(
                {
                    **dict(n),
                    "ticket_label": label(n["ticket_number"]) if n["ticket_number"] else None,
                }
            )
            for n in box.notifications
        ],
        unread=box.unread,
        next_cursor=box.next_cursor,
    )


@router.post(
    "/notifications/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["notifications"],
    summary="Mark all your notifications read",
)
async def read_all(caller: CallerDep, sm: SessionmakerDep) -> None:
    await messages.mark_all_read(sm, caller)


@router.post(
    "/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["notifications"],
    summary="Mark one notification read",
)
async def read_one(notification_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await messages.mark_read(sm, caller, notification_id)
