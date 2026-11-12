"""The inbound API channel (md C3): another system sends what its customer wrote, with a
`read_write` or `admin` API key. See service/inbound_api.py for what happens."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.ticket_schemas import ContactIn, FieldValue, Subject, Tag, Text
from cedesk.helpdesk.domain.tickets import Priority
from cedesk.helpdesk.service import inbound_api
from cedesk.helpdesk.service.customer_replies import Outcome
from cedesk.helpdesk.service.inbound_api import InboundTicket

router = APIRouter(tags=["inbound api"])

ExternalId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    Field(description="Your system's own id for it. Sending it again changes nothing."),
]

_KEY: dict[int | str, dict[str, Any]] = {
    401: {"description": "No API key, or a revoked one"},
    403: {"description": "Not an API key, or a read-only one"},
    429: {"description": "Over the workspace's API key budget (Retry-After)"},
}


class InboundTicketIn(Body):
    contact: ContactIn = Field(description="The customer: a ticket is theirs by email.")
    message: Text = Field(description="What the customer wrote (card numbers are removed).")
    subject: Subject | None = Field(
        default=None, description="Default: the first 60 characters of the message."
    )
    priority: Priority = Priority.medium
    type_id: UUID | None = None
    team_id: UUID | None = Field(default=None, description="Default: the rules and routing.")
    fields: dict[str, FieldValue] = Field(default={}, max_length=30)
    tags: list[Tag] = Field(default=[], max_length=20)
    external_id: ExternalId | None = None


class InboundTicketOut(BaseModel):
    ticket_id: UUID
    number: int
    created: bool = Field(description="false: this external_id made the ticket before.")


class InboundMessageIn(Body):
    message: Text
    external_id: ExternalId | None = None


class InboundMessageOut(BaseModel):
    message_id: UUID
    outcome: Outcome = Field(
        description="added, rules_ran, reopened (it was resolved), thanked (a thank-you on "
        "a resolved ticket: it stays resolved) or duplicate (this external_id was saved "
        "before; nothing changed)."
    )


@router.post(
    "/inbound/tickets",
    response_model=InboundTicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="A customer's new request from another system (API key)",
    description="Makes a ticket on the `api` channel as the customer: triage, rules, SLA "
    "and routing run. 200 instead of 201 when `external_id` made a ticket before. "
    "Agents' replies stay in CeDesk: read them with `GET /tickets/{id}`.",
    responses=_KEY | {200: {"description": "This external_id made the ticket before"}},
)
async def new_request(
    body: InboundTicketIn, caller: CallerDep, sm: SessionmakerDep, response: Response
) -> InboundTicketOut:
    got = await inbound_api.new_request(
        sm,
        caller,
        InboundTicket(
            contact_email=body.contact.email,
            contact_name=body.contact.name,
            message=body.message,
            subject=body.subject,
            priority=body.priority,
            type_id=body.type_id,
            team_id=body.team_id,
            fields=dict(body.fields),
            tags=tuple(body.tags),
            external_id=body.external_id,
        ),
    )
    if not got.created:
        response.status_code = status.HTTP_200_OK
    return InboundTicketOut(ticket_id=got.ticket_id, number=got.number, created=got.created)


@router.post(
    "/inbound/tickets/{ticket_id}/messages",
    response_model=InboundMessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="The customer's next message from another system (API key)",
    description="Only on a ticket that came in through the API. Works as the customer's "
    "reply: it reopens a resolved ticket, and reply rules run. 200 for a duplicate.",
    responses=_KEY
    | {
        200: {"description": "This external_id was saved before"},
        404: {"description": "No such ticket"},
        409: {"description": "The ticket came in another way, or was merged"},
    },
)
async def customer_message(
    ticket_id: UUID,
    body: InboundMessageIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
) -> InboundMessageOut:
    got = await inbound_api.customer_message(
        sm, caller, ticket_id, body.message, external_id=body.external_id
    )
    if got.outcome is Outcome.duplicate:
        response.status_code = status.HTTP_200_OK
    return InboundMessageOut(message_id=got.message_id, outcome=got.outcome)
