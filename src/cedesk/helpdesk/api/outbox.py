"""Events that could not be delivered (md 2B "Reliability": the dead-letter list). Admin
only. Failed emails show on their ticket instead (a system line and `delivery`)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.service import delivery

router = APIRouter(tags=["outbox"])


class FailedEventOut(BaseModel):
    id: UUID
    event: str = Field(description="ticket.created, message.created, ...")
    ticket_id: UUID | None
    occurred_at: datetime
    failed_at: datetime
    attempts: int
    last_error: str | None = Field(description="Which handler failed, and why.")


class FailedPageOut(BaseModel):
    events: list[FailedEventOut] = Field(description="Newest failure first.")
    next_cursor: str | None


@router.get(
    "/outbox/failed",
    response_model=FailedPageOut,
    summary="Admin: events that could not be delivered after every retry",
)
async def failed(
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> FailedPageOut:
    page = await delivery.failed_events(sm, caller, cursor=cursor, limit=limit)
    return FailedPageOut(
        events=[
            FailedEventOut.model_validate({**dict(r), "failed_at": r["created_at"]})
            for r in page.events
        ],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/outbox/{event_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Admin: try a failed event again now",
    description="Its tries start from zero. Steps that already worked are not repeated.",
)
async def retry(event_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await delivery.retry_event(sm, caller, event_id)
