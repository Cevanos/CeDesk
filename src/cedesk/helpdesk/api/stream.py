"""Live updates (md C11, P7): a Server-Sent Events stream for each signed-in agent, and one for
each portal customer.

A browser's EventSource can't send an Authorization header, so opening a stream takes two
steps: `POST /stream/ticket` (with the usual token) answers a ticket that lasts a minute and
works once, then `GET /stream?ticket=...` opens the stream. The session token never goes in an
address. A client that can send headers may use the ticket the same way.

What a stream sends is that something changed, never what it says: ids and the names of what
changed. Fetch the ticket through the normal API (it applies the same permissions). See
`domain/stream.py` for the kinds.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cedesk import logs
from cedesk.helpdesk.api.deps import CallerDep, CustomerDep, SessionmakerDep
from cedesk.helpdesk.domain import stream as rules
from cedesk.helpdesk.service import stream
from cedesk.helpdesk.service.stream import Audience

router = APIRouter(tags=["live updates"])


class StreamTicketOut(BaseModel):
    ticket: str = Field(description="Open the stream with this: `GET /stream?ticket=...`.")
    expires_in: int = Field(description="Seconds it can still be used. Works once.")


_SSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "An endless `text/event-stream`. Each event has an `id` (send it back as "
            "`Last-Event-ID`, or `last_event_id`, to carry on without losing one), an `event` "
            "kind and a JSON `data` of ids. `reset`: your last id is older than what is kept, "
            "refetch what you show. `end`: you were signed out. A line starting with `:` is a "
            "heartbeat. An id may repeat after a reconnect: ignore one you have seen."
        ),
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
    401: {"description": "The ticket is unknown, used, expired, or its sign-in ended."},
    429: {"description": "You already have 5 live streams open."},
}
_HEADERS = {
    "Cache-Control": "no-cache, no-transform",  # and never compressed on the way
    "X-Accel-Buffering": "no",
}


def _stream(request: Request, who: Audience, last_event_id: str | None) -> StreamingResponse:
    slots: stream.Slots = request.app.state.stream_slots
    slots.check(who)
    sm = request.app.state.sessionmaker
    logs.bind(workspace_id=who.workspace_id)

    async def body() -> AsyncIterator[str]:
        with slots.hold(who):
            async for chunk in stream.run(
                sm, who, last_event_id=rules.parse_last_event_id(last_event_id)
            ):
                yield chunk

    return StreamingResponse(body(), media_type="text/event-stream", headers=_HEADERS)


@router.post(
    "/stream/ticket",
    response_model=StreamTicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="A one-use ticket to open the live stream",
)
async def agent_ticket(caller: CallerDep, sm: SessionmakerDep) -> StreamTicketOut:
    issued = await stream.issue_agent_ticket(sm, caller)
    return StreamTicketOut(ticket=issued.ticket, expires_in=issued.expires_in)


@router.get(
    "/stream",
    summary="Live updates for an agent (Server-Sent Events)",
    responses=_SSE,
    response_class=StreamingResponse,
)
async def agent_stream(
    request: Request,
    sm: SessionmakerDep,
    ticket: Annotated[str, Query(min_length=20, max_length=200)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id: Annotated[str | None, Query(max_length=20)] = None,
) -> StreamingResponse:
    who = await stream.open_agent(sm, ticket)
    return _stream(request, who, last_event_id_header or last_event_id)


@router.post(
    "/portal/stream/ticket",
    response_model=StreamTicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="A one-use ticket to open the customer's live stream",
)
async def customer_ticket(customer: CustomerDep, sm: SessionmakerDep) -> StreamTicketOut:
    issued = await stream.issue_customer_ticket(sm, customer)
    return StreamTicketOut(ticket=issued.ticket, expires_in=issued.expires_in)


@router.get(
    "/portal/stream",
    summary="Live updates for a portal customer (Server-Sent Events)",
    description=(
        "Only a customer, agent or AI message on the customer's own tickets, and a change of "
        "their status: never a note, a system line or anything else."
    ),
    responses=_SSE,
    response_class=StreamingResponse,
)
async def customer_stream(
    request: Request,
    sm: SessionmakerDep,
    ticket: Annotated[str, Query(min_length=20, max_length=200)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id: Annotated[str | None, Query(max_length=20)] = None,
) -> StreamingResponse:
    who = await stream.open_customer(sm, ticket)
    return _stream(request, who, last_event_id_header or last_event_id)
