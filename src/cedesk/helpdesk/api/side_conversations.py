"""Side conversations (md X5): email another team from a ticket; the customer never sees it,
and the reply comes back to the ticket."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import side_conversations as rules
from cedesk.helpdesk.service import side_conversations as sides
from cedesk.helpdesk.service.side_conversations import SideConversation

router = APIRouter(tags=["side conversations"])


class StartIn(Body):
    to: list[str] = Field(min_length=1, max_length=rules.MAX_TO, description="Email addresses.")
    subject: str | None = Field(
        default=None,
        max_length=rules.SUBJECT_MAX,
        description='Default: "About <ticket subject>". The ticket\'s label is added.',
    )
    body: str = Field(min_length=1, max_length=rules.BODY_MAX)


class AgainIn(Body):
    body: str = Field(min_length=1, max_length=rules.BODY_MAX)


class SideMessageOut(BaseModel):
    id: UUID
    direction: str = Field(description="out: sent from CeDesk; in: their reply.")
    body: str
    author_name: str
    from_address: str
    created_at: datetime


class SideOut(BaseModel):
    id: UUID
    subject: str
    to: list[str]
    from_address: str = Field(description="The support address it goes out from.")
    created_at: datetime
    last_message_at: datetime
    created_by_name: str | None
    messages: list[SideMessageOut]


def _out(side: SideConversation) -> SideOut:
    data = side.__dict__ | {"messages": [SideMessageOut(**m.__dict__) for m in side.messages]}
    return SideOut(**data)


_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Can't reply, or the ticket is another team's (view only)"},
    404: {"description": "No such ticket or side conversation"},
    409: {"description": "No support address yet, or 20 side conversations already"},
    422: {"description": "Not an address, the customer, one of our own addresses, empty"},
}


@router.get(
    "/tickets/{ticket_id}/side-conversations",
    response_model=list[SideOut],
    summary="Side conversations on a ticket (agents only; never shown to the customer)",
)
async def list_sides(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> list[SideOut]:
    return [_out(s) for s in await sides.conversations(sm, caller, ticket_id)]


@router.post(
    "/tickets/{ticket_id}/side-conversations",
    response_model=SideOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a side conversation by email",
    description="Sent from the ticket's support address by the worker. Their reply comes "
    "back here (matched by the email's reply headers), the assignee is told "
    "(notification side_reply), and the customer sees none of it. The ticket gets a "
    "system line.",
    responses=_ERRORS,
)
async def start_side(
    ticket_id: UUID, body: StartIn, caller: CallerDep, sm: SessionmakerDep
) -> SideOut:
    made = await sides.start(
        sm, caller, ticket_id, to=body.to, subject=body.subject, body=body.body
    )
    return _out(made)


@router.post(
    "/side-conversations/{side_id}/messages",
    response_model=SideOut,
    status_code=status.HTTP_201_CREATED,
    summary="Write again in a side conversation",
    description="Threaded under the earlier emails, to the same addresses.",
    responses=_ERRORS,
)
async def write_again(
    side_id: UUID, body: AgainIn, caller: CallerDep, sm: SessionmakerDep
) -> SideOut:
    return _out(await sides.write_again(sm, caller, side_id, body=body.body))
