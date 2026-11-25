"""Tickets: create, list with filters, search and sort, open, change, resolve; and the
customer card behind them (VIP and the customer's other tickets).

Ticket responses carry an `ETag` (the ticket's `updated_at`). Send it back as `If-Match`
on a change or resolve, and the request gets 409 if someone changed the ticket since."""

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status

from cedesk.helpdesk.api.deps import (
    CallerDep,
    IdempotencyKey,
    SessionmakerDep,
    idempotency,
    mark_replayed,
)
from cedesk.helpdesk.api.ticket_schemas import (
    ContactCard,
    ContactOut,
    ContactPatch,
    ContactProfileOut,
    ContactTicketsOut,
    Counts,
    MessageOut,
    ResolveIn,
    SlaClockOut,
    SlaOut,
    TicketIn,
    TicketListOut,
    TicketOut,
    TicketPatch,
    TicketRowOut,
)
from cedesk.helpdesk.api.workflow_schemas import CustomerReplyBody
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import view_only_reason
from cedesk.helpdesk.domain.sla import Clock, TicketSla
from cedesk.helpdesk.domain.tickets import Priority, Sort, Status, etag, label
from cedesk.helpdesk.service import customer_replies, tickets
from cedesk.helpdesk.service.tickets import NewTicket, TicketView

router = APIRouter(tags=["tickets"])

IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=100,
        description="The ETag from reading the ticket. 409 if the ticket changed since.",
    ),
]
Cursor = Annotated[
    str | None, Query(max_length=200, description="`next_cursor` from the previous page.")
]
Limit = Annotated[int, Query(ge=1, le=200)]


def _clock(c: Clock) -> SlaClockOut:
    return SlaClockOut(
        target_minutes=c.target_minutes,
        due_at=c.due_at,
        stopped_at=c.stopped_at,
        state=c.state.value,
        minutes_left=round(c.minutes_left, 1),
    )


def sla_out(t: TicketSla | None) -> SlaOut | None:
    if t is None:
        return None
    return SlaOut(
        policy=t.policy,
        current=t.current.kind.value,
        state=t.current.state.value,
        paused=t.paused,
        first_reply=_clock(t.first_reply),
        resolve=_clock(t.resolve),
    )


def _row(r: Mapping[Any, Any], sla: TicketSla | None = None) -> dict[str, Any]:
    return {
        **dict(r),
        "sla": sla_out(sla),
        "label": label(r["number"]),
        "contact": ContactOut(
            id=r["contact_id"],
            name=r["contact_name"],
            email=r["contact_email"],
            vip=r["contact_vip"],
        ),
    }


def detail_response(view: TicketView, response: Response, caller: Caller) -> TicketOut:
    response.headers["ETag"] = etag(view.ticket["updated_at"])
    out = _ticket_out(view)
    reason = view_only_reason(caller.access, view.ticket["team_id"])
    return out.model_copy(update={"can_edit": reason is None, "view_only_reason": reason})


def _ticket_out(view: TicketView) -> TicketOut:
    t = view.ticket
    card = ContactCard(
        id=t["contact_id"],
        name=t["contact_name"],
        email=t["contact_email"],
        vip=t["contact_vip"],
        customer_since=t["contact_since"],
        other_tickets=view.other_tickets,
        csat_average=t["contact_csat_average"],
        csat_count=t["contact_csat_count"],
        phone=t.get("contact_phone"),
        organization=t.get("contact_organization"),
    )
    return TicketOut.model_validate(
        {
            **_row(t, view.sla),
            "contact": card,
            "reply_cc": list(view.reply_cc),
            "messages": [MessageOut.model_validate(dict(m)) for m in view.messages],
            "older_messages_cursor": view.older_messages_cursor,
            "resolution": dict(view.resolution) if view.resolution else None,
        }
    )


@router.post(
    "/tickets",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a ticket (gets the next T-number)",
)
async def create_ticket(
    body: TicketIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> TicketOut:
    idem = idempotency(idempotency_key, "POST /tickets", body.model_dump_json().encode())
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
        idem=idem,
    )
    mark_replayed(idem, response)
    return detail_response(view, response, caller)


@router.get(
    "/tickets",
    response_model=TicketListOut,
    summary="List tickets with status tabs, filters, search and sort (cursor pages)",
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
    sort: Annotated[
        Sort,
        Query(
            description="priority (default): Open and Pending most urgent first, then "
            "oldest; Resolved last, newest first. sla: Open and Pending by SLA time left, "
            "soonest due first; Resolved last, newest first. newest, oldest, or updated "
            "(most recently changed first)."
        ),
    ] = Sort.priority,
    imported: Annotated[
        str | None,
        Query(
            description="Only tickets brought from another help desk: `any`, or one source "
            "(intercom, zendesk, freshdesk, gorgias, helpscout, hubspot, zoho, csv). Each also has "
            "the tag `imported-<source>`.",
        ),
    ] = None,
    snoozed: Annotated[
        bool | None,
        Query(
            description="true: only snoozed tickets; false: leave snoozed tickets out. "
            "Not sent: both."
        ),
    ] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> TicketListOut:
    page = await tickets.list_tickets(
        sm,
        caller,
        status=status,
        scope=scope,
        priority=priority,
        team_id=team,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
        imported=imported,
        snoozed=snoozed,
    )
    return TicketListOut(
        tickets=[TicketRowOut.model_validate(_row(r, page.sla.get(r["id"]))) for r in page.tickets],
        counts=Counts.model_validate(page.counts),
        next_cursor=page.next_cursor,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketOut, summary="Open a ticket")
async def get_ticket(
    ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep, response: Response
) -> TicketOut:
    return detail_response(await tickets.get_ticket(sm, caller, ticket_id), response, caller)


@router.patch(
    "/tickets/{ticket_id}",
    response_model=TicketOut,
    summary="Change status, priority, type, fields, team, assignee, subject or tags",
)
async def change_ticket(
    ticket_id: UUID,
    body: TicketPatch,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    if_match: IfMatch = None,
) -> TicketOut:
    view = await tickets.change_ticket(sm, caller, ticket_id, body.changes(), if_match=if_match)
    return detail_response(view, response, caller)


@router.post(
    "/tickets/{ticket_id}/resolve",
    response_model=TicketOut,
    summary="Resolve a ticket, with a resolution receipt when asked for",
    description="Already resolved: no change (409 if a receipt is sent: change it with "
    "`PATCH .../resolution`). The body is optional. With the workspace's resolution "
    "receipts `required` for the ticket's team, resolving without `resolution` is 422, "
    "here and through `PATCH /tickets/{id}` or a bulk change; automatic closes don't need "
    "one.",
    responses={
        409: {"description": "Already resolved and a receipt was sent, or changed since read"},
        422: {"description": "A receipt is required, or receipts are off"},
    },
)
async def resolve_ticket(
    ticket_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    body: ResolveIn | None = None,
    if_match: IfMatch = None,
) -> TicketOut:
    receipt = body.resolution.model_dump() if body and body.resolution else None
    view = await tickets.resolve_ticket(
        sm, caller, ticket_id, if_match=if_match, resolution=receipt
    )
    return detail_response(view, response, caller)


@router.get(
    "/contacts/{contact_id}/tickets",
    response_model=ContactTicketsOut,
    tags=["contacts"],
    summary="A customer's card and all their tickets, newest first",
)
async def contact_tickets(
    contact_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ContactTicketsOut:
    page = await tickets.contact_tickets(sm, caller, contact_id, cursor=cursor, limit=limit)
    return ContactTicketsOut(
        contact=ContactProfileOut.model_validate(dict(page.contact)),
        tickets=[TicketRowOut.model_validate(_row(r, page.sla.get(r["id"]))) for r in page.tickets],
        next_cursor=page.next_cursor,
    )


@router.patch(
    "/contacts/{contact_id}/profile",
    response_model=ContactProfileOut,
    tags=["contacts"],
    summary="Change a customer's name or VIP flag",
)
async def change_contact(
    contact_id: UUID, body: ContactPatch, caller: CallerDep, sm: SessionmakerDep
) -> ContactProfileOut:
    row = await tickets.change_contact(sm, caller, contact_id, name=body.name, vip=body.vip)
    return ContactProfileOut.model_validate(dict(row))


@router.post(
    "/tickets/{ticket_id}/simulate-customer-reply",
    response_model=TicketOut,
    summary="Test only (Admin, not in production): add a message as the customer",
    description="Does what a real customer reply does: reopens a resolved ticket, keeps it "
    "resolved for a thank-you, runs the customer_replied rules and tells the assignee. "
    "Real replies come from the Cevanos connector, email and the portal.",
)
async def simulate_customer_reply(
    ticket_id: UUID,
    body: CustomerReplyBody,
    caller: CallerDep,
    sm: SessionmakerDep,
    request: Request,
    response: Response,
) -> TicketOut:
    allowed = request.app.state.settings.env.value != "production"
    await customer_replies.simulate(sm, caller, ticket_id, body.body, allowed=allowed)
    return detail_response(await tickets.get_ticket(sm, caller, ticket_id), response, caller)
