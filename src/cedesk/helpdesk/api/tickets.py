"""Tickets: create, list with filters and search, open, change."""

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.ticket_schemas import (
    ContactCard,
    ContactOut,
    Counts,
    MessageOut,
    TicketIn,
    TicketListOut,
    TicketOut,
    TicketPatch,
    TicketRowOut,
)
from cedesk.helpdesk.domain.tickets import Priority, Status, label
from cedesk.helpdesk.service import tickets
from cedesk.helpdesk.service.tickets import NewTicket, TicketView

router = APIRouter(tags=["tickets"])


def _row(r: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        **dict(r),
        "label": label(r["number"]),
        "contact": ContactOut(
            id=r["contact_id"],
            name=r["contact_name"],
            email=r["contact_email"],
            vip=r["contact_vip"],
        ),
    }


def _detail(view: TicketView) -> TicketOut:
    t = view.ticket
    card = ContactCard(
        id=t["contact_id"],
        name=t["contact_name"],
        email=t["contact_email"],
        vip=t["contact_vip"],
        customer_since=t["contact_since"],
        other_tickets=view.other_tickets,
    )
    return TicketOut.model_validate(
        {
            **_row(t),
            "contact": card,
            "messages": [MessageOut.model_validate(dict(m)) for m in view.messages],
        }
    )


@router.post(
    "/tickets",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a ticket (gets the next T-number)",
)
async def create_ticket(body: TicketIn, caller: CallerDep, sm: SessionmakerDep) -> TicketOut:
    view = await tickets.create_ticket(
        sm,
        caller,
        NewTicket(
            subject=body.subject,
            contact_email=body.contact.email,
            contact_name=body.contact.name,
            description=body.description,
            priority=body.priority,
            channel=body.channel,
            type_id=body.type_id,
            team_id=body.team_id,
            assignee_id=body.assignee_id,
            fields=body.fields,
            tags=body.tags,
        ),
    )
    return _detail(view)


@router.get(
    "/tickets",
    response_model=TicketListOut,
    summary="List tickets with status tabs, filters and search",
)
async def list_tickets(
    caller: CallerDep,
    sm: SessionmakerDep,
    status: Status | None = None,
    scope: Literal["everyone", "mine", "unassigned"] = "everyone",
    priority: Priority | None = None,
    team: UUID | None = None,
    q: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Number (T-1005 or 1005), subject, customer name or email, "
            "any field value, or tag. Not case-sensitive.",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> TicketListOut:
    page = await tickets.list_tickets(
        sm,
        caller,
        status=status,
        scope=scope,
        priority=priority,
        team_id=team,
        q=q,
        limit=limit,
        offset=offset,
    )
    return TicketListOut(
        tickets=[TicketRowOut.model_validate(_row(r)) for r in page.tickets],
        counts=Counts.model_validate(page.counts),
        next_offset=page.next_offset,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketOut, summary="Open a ticket")
async def get_ticket(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> TicketOut:
    return _detail(await tickets.get_ticket(sm, caller, ticket_id))


@router.patch(
    "/tickets/{ticket_id}",
    response_model=TicketOut,
    summary="Change status, priority, type, fields, team, assignee, subject or tags",
)
async def change_ticket(
    ticket_id: UUID, body: TicketPatch, caller: CallerDep, sm: SessionmakerDep
) -> TicketOut:
    return _detail(await tickets.change_ticket(sm, caller, ticket_id, body.changes()))
