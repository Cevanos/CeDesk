"""Cevanos connector (md C4-C7, section 7.1).

- Settings > Cevanos (Admin): the API key and webhook secret (write-only), the team
  handovers go to, and "resolving a ticket closes the chat".
- `POST /cevanos/events/{workspace_id}`: Cevanos's signed webhooks. Not for the frontend.
  In Cevanos, add a webhook to this URL (the settings page shows the path) with the
  events `conversation.escalated`, `conversation.classified`, `conversation.reopened`
  and `conversation.closed`, and save its signing secret here.
- Accept chat, and turning a Cevanos chat into a ticket.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from cedesk.cevanos_client import Cevanos
from cedesk.helpdesk.api.deps import (
    CallerDep,
    IdempotencyKey,
    SessionmakerDep,
    idempotency,
    mark_replayed,
)
from cedesk.helpdesk.api.ticket_schemas import TicketOut
from cedesk.helpdesk.api.tickets import detail_response
from cedesk.helpdesk.domain import cevanos as rules
from cedesk.helpdesk.service import cevanos, tickets
from cedesk.helpdesk.service.errors import TooLarge

router = APIRouter(tags=["cevanos"])


def _cevanos(request: Request) -> Cevanos:
    found: Cevanos = request.app.state.cevanos
    return found


class ConnectionOut(BaseModel):
    connected: bool = Field(description="false: Cevanos features are hidden.")
    readable: bool = Field(
        description="false: the server's secret key changed since the secrets were saved; "
        "enter both again."
    )
    api_key_hint: str | None = Field(description="The key's last 4 characters.")
    close_on_resolve: bool
    team_id: UUID | None
    team_name: str | None
    updated_at: str | None
    webhook_path: str = Field(description="Add this path, on the API's address, in Cevanos.")


class ConnectionIn(BaseModel):
    api_key: SecretStr | None = Field(
        default=None,
        description="cvn_sk_... with scopes conversations:read and conversations:write. "
        "Leave out to keep the saved one (required the first time).",
    )
    webhook_secret: SecretStr | None = Field(
        default=None,
        description="The signing secret Cevanos showed for the webhook. Leave out to keep "
        "the saved one (required the first time).",
    )
    close_on_resolve: bool = False
    team_id: UUID | None = Field(
        default=None, description="The team handovers go to; routing picks the agent."
    )


class KeyCheckOut(BaseModel):
    ok: bool
    missing_scopes: list[str]
    message: str


class EventOut(BaseModel):
    outcome: Literal["created", "handed_over_again", "updated", "duplicate", "ignored"]
    ticket_id: UUID | None


def _out(view: cevanos.ConnectionView) -> ConnectionOut:
    return ConnectionOut(
        connected=view.connected,
        readable=view.readable,
        api_key_hint=view.api_key_hint,
        close_on_resolve=view.close_on_resolve,
        team_id=view.team_id,
        team_name=view.team_name,
        updated_at=view.updated_at.isoformat() if view.updated_at else None,
        webhook_path=view.webhook_path,
    )


@router.get(
    "/cevanos/connection", response_model=ConnectionOut, summary="The Cevanos connection (Admin)"
)
async def get_connection(request: Request, caller: CallerDep, sm: SessionmakerDep) -> ConnectionOut:
    return _out(await cevanos.get_connection(sm, _cevanos(request), caller))


@router.put(
    "/cevanos/connection",
    response_model=ConnectionOut,
    summary="Connect Cevanos, or change the connection (Admin)",
    description="The key and secret are stored encrypted and never returned.",
)
async def save_connection(
    body: ConnectionIn, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> ConnectionOut:
    view = await cevanos.save_connection(
        sm,
        _cevanos(request),
        caller,
        api_key=body.api_key.get_secret_value() if body.api_key else None,
        webhook_secret=body.webhook_secret.get_secret_value() if body.webhook_secret else None,
        close_on_resolve=body.close_on_resolve,
        team_id=body.team_id,
    )
    return _out(view)


@router.delete(
    "/cevanos/connection",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect Cevanos (Admin). Tickets stay; their chats stop syncing.",
)
async def remove_connection(caller: CallerDep, sm: SessionmakerDep) -> None:
    await cevanos.remove_connection(sm, caller)


@router.post(
    "/cevanos/connection/check",
    response_model=KeyCheckOut,
    summary="Check the saved key with Cevanos (Admin)",
)
async def check_key(request: Request, caller: CallerDep, sm: SessionmakerDep) -> KeyCheckOut:
    found = await cevanos.check_key(sm, _cevanos(request), caller)
    return KeyCheckOut(
        ok=found.ok, missing_scopes=list(found.missing_scopes), message=found.message
    )


@router.post(
    "/cevanos/events/{workspace_id}",
    response_model=EventOut,
    summary="Cevanos delivers a signed webhook (no sign-in; CXOps-Signature required)",
    description="401 for a missing, wrong or old signature; 503 when Cevanos's own API "
    "can't be reached to read the chat (Cevanos sends the event again).",
)
async def receive_event(workspace_id: UUID, request: Request, sm: SessionmakerDep) -> EventOut:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > rules.BODY_MAX:
        raise TooLarge("Event too large.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > rules.BODY_MAX:
            raise TooLarge("Event too large.")
    received = await cevanos.receive_event(
        sm, _cevanos(request), workspace_id, bytes(body),
        request.headers.get(rules.SIGNATURE_HEADER),
    )  # fmt: skip
    return EventOut(outcome=received.outcome.value, ticket_id=received.ticket_id)


@router.post(
    "/tickets/from-conversation/{conversation_id}",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Turn a Cevanos chat into a ticket (then Accept it)",
    description="409 if the chat already has a ticket; 404 if Cevanos has no such chat "
    "for the workspace's key.",
)
async def from_conversation(
    conversation_id: str, request: Request, caller: CallerDep, sm: SessionmakerDep,
    response: Response,
) -> TicketOut:  # fmt: skip
    view = await cevanos.convert(sm, _cevanos(request), caller, conversation_id)
    return detail_response(view, response)


@router.post(
    "/tickets/{ticket_id}/accept",
    response_model=TicketOut,
    summary="Accept a handed-over chat: it is yours, and replies are allowed",
    description="Assigns it to you (taking it from someone else needs the right to "
    "reassign). The customer sees in Cevanos that you joined. 409 if it isn't waiting.",
)
async def accept(
    ticket_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> TicketOut:
    idem = idempotency(idempotency_key, f"POST /tickets/{ticket_id}/accept")
    view = await tickets.accept_ticket(sm, caller, ticket_id, idem=idem)
    mark_replayed(idem, response)
    return detail_response(view, response)
