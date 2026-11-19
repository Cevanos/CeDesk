"""Customer portal (md P1-P4, S7): sign-in by an emailed code, and Settings > Portal.

The portal routes use their own token (`POST /portal/{workspace}/session`); agent
tokens are refused there, and portal tokens are refused on every agent route.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.attachments import file_response
from cedesk.helpdesk.api.deps import (
    AttemptLimit,
    CallerDep,
    CustomerDep,
    PublicLimit,
    SessionmakerDep,
    StoreDep,
)
from cedesk.helpdesk.api.schemas import Body, Email
from cedesk.helpdesk.api.ticket_schemas import CsatOut, FieldDefOut, FieldValue
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.service import csat, portal, portal_tickets
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.settings import ScanBackend, Settings

router = APIRouter(tags=["portal"])

Slug = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


class CodeBody(Body):
    email: Email


class SessionBody(Body):
    email: Email
    code: Annotated[str, StringConstraints(min_length=1, max_length=20)] = Field(
        description="The 6 digits from the email (spaces or a dash are fine)."
    )


class CustomerOut(BaseModel):
    email: str
    name: str
    workspace_name: str


class PortalSignedInOut(BaseModel):
    token: str = Field(description="Send as `Authorization: Bearer <token>` on portal routes.")
    expires_at: datetime
    customer: CustomerOut


class PortalSettingsIn(Body):
    allowed_domains: list[Annotated[str, StringConstraints(max_length=253)]] = Field(
        max_length=50,
        description="Empty: anyone can sign in. Otherwise only these email domains "
        '(and their subdomains), e.g. ["acme.com"]. Saving a list signs out, at once, '
        "customers already signed in with another email.",
    )
    csat_survey: bool = Field(
        default=True, description="Email a 1-5 survey when a ticket is resolved."
    )
    web_form: bool = Field(
        default=False,
        description="The public contact form (`/public/{workspace}/form`) is open. Off: 404.",
    )


class PortalSettingsOut(BaseModel):
    allowed_domains: list[str]
    csat_survey: bool
    web_form: bool
    updated_at: datetime | None
    updated_by_name: str | None


@router.post(
    "/portal/{workspace}/code",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email me a sign-in code (customer)",
    description="Always 202 for a valid email, whether or not we know it (and for an "
    "unknown workspace), so it can't be used to find customers. The code has 6 digits "
    "and works for 10 minutes; a new code replaces the last one; at most 5 per hour.",
    responses={
        403: {
            "description": "This portal is limited to some email domains (the message "
            "says which: show it)"
        },
        422: {"description": "Not an email address"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def request_code(
    workspace: Slug, body: CodeBody, request: Request, sm: SessionmakerDep
) -> None:
    settings = _settings(request)
    fallback = (
        Sender(settings.mail_from_address, settings.mail_from_name)
        if settings.mail_from_address
        else None
    )
    await portal.request_code(
        sm,
        workspace=workspace,
        email=body.email,
        secret_key=settings.secret_key.get_secret_value(),
        fallback=fallback,
    )


@router.post(
    "/portal/{workspace}/session",
    response_model=PortalSignedInOut,
    summary="Sign in with the emailed code (customer)",
    responses={
        401: {
            "description": "Wrong, used or expired code (same answer for every reason). "
            "After 5 wrong tries the code stops working"
        },
        403: {"description": "The portal is now limited to other email domains"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def sign_in(
    workspace: Slug, body: SessionBody, request: Request, sm: SessionmakerDep
) -> PortalSignedInOut:
    signed = await portal.verify_code(
        sm,
        workspace=workspace,
        email=body.email,
        code=body.code,
        secret_key=_settings(request).secret_key.get_secret_value(),
    )
    name = await portal.workspace_name(sm, signed.customer)
    return PortalSignedInOut(
        token=signed.token,
        expires_at=signed.expires_at,
        customer=CustomerOut(
            email=signed.customer.email, name=signed.customer.name, workspace_name=name
        ),
    )


@router.get("/portal/me", response_model=CustomerOut, summary="The signed-in customer")
async def me(customer: CustomerDep, sm: SessionmakerDep) -> CustomerOut:
    name = await portal.workspace_name(sm, customer)
    return CustomerOut(email=customer.email, name=customer.name, workspace_name=name)


@router.post(
    "/portal/sign-out", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out (customer)"
)
async def sign_out(customer: CustomerDep, sm: SessionmakerDep) -> None:
    await portal.sign_out(sm, customer)


@router.get(
    "/portal-settings",
    response_model=PortalSettingsOut,
    summary="Portal settings (Admin)",
    responses={403: {"description": "Admins only"}},
)
async def get_settings(caller: CallerDep, sm: SessionmakerDep) -> PortalSettingsOut:
    found = await portal.get_settings(sm, caller)
    return PortalSettingsOut(**found.__dict__)


@router.put(
    "/portal-settings",
    response_model=PortalSettingsOut,
    summary="Save the portal settings (Admin)",
    responses={403: {"description": "Admins only"}, 422: {"description": "Not a domain"}},
)
async def save_settings(
    body: PortalSettingsIn, caller: CallerDep, sm: SessionmakerDep
) -> PortalSettingsOut:
    found = await portal.save_settings(
        sm, caller, body.allowed_domains, csat_survey=body.csat_survey, web_form=body.web_form
    )
    return PortalSettingsOut(**found.__dict__)


# My tickets (md P2-P4). Another customer's ticket is 404, never 403.


class PortalTicketRow(BaseModel):
    id: UUID
    label: str = Field(description="T-1001")
    subject: str
    status: str = Field(description="open, pending or resolved")
    status_label: str = Field(description='Show this: "Open", "Waiting on you", "Resolved"')
    type_name: str | None
    created_at: datetime
    updated_at: datetime


class PortalTicketsOut(BaseModel):
    tickets: list[PortalTicketRow] = Field(description="Newest first.")
    next_cursor: str | None


class PortalTypeOut(BaseModel):
    id: UUID
    name: str
    fields: list[FieldDefOut]


class PortalFileOut(BaseModel):
    id: UUID
    name: str
    size: int
    mime: str
    scan_status: str = Field(description="infected / scan_failed: show as blocked, no link.")


class PortalMessageOut(BaseModel):
    id: UUID
    author: str = Field(description='"You", "Acme assistant" or "Neha · Acme Support".')
    from_customer: bool = Field(description="true: show on the customer's side.")
    body: str
    created_at: datetime
    attachments: list[PortalFileOut]


class PortalTicketOut(PortalTicketRow):
    fields: dict[str, FieldValue]
    resolved_at: datetime | None
    csat: CsatOut | None = Field(
        description="The customer's rating. Offer 1-5 stars when `status` is resolved."
    )
    messages: list[PortalMessageOut] = Field(
        description="Customer, agent and AI messages only, oldest to newest (the latest page)."
    )
    older_messages_cursor: str | None = Field(
        description="Pass as `cursor` to load the page before these."
    )


class NewPortalTicketIn(Body):
    message: Annotated[str, StringConstraints(min_length=1, max_length=50_000)] = Field(
        description="What happened. Its first 60 characters become the subject."
    )
    type_id: UUID | None = Field(default=None, description="From GET /portal/ticket-types.")
    fields: dict[str, FieldValue] = Field(
        default={}, max_length=30, description="The chosen type's fields, by key."
    )


class PortalReplyIn(Body):
    body: Annotated[str, StringConstraints(min_length=1, max_length=50_000)]


class PortalDownloadOut(BaseModel):
    url: str = Field(
        description="Opens the file without signing in, until `expires_at`. With "
        "`needs_sign_in` it is GET /portal/attachments/{id}/content: fetch it with the "
        "portal token."
    )
    expires_at: datetime
    needs_sign_in: bool = False
    file: PortalFileOut


def _row(r: dict[str, object]) -> dict[str, object]:
    status_value = str(r["status"])
    return {
        **r,
        "label": label(int(str(r["number"]))),
        "status_label": portal_tickets.status_label(status_value),
    }


@router.get(
    "/portal/ticket-types",
    response_model=list[PortalTypeOut],
    summary="Ticket types to pick from on New ticket (customer)",
)
async def ticket_types(customer: CustomerDep, sm: SessionmakerDep) -> list[PortalTypeOut]:
    rows = await portal_tickets.ticket_types(sm, customer)
    return [PortalTypeOut(id=r["id"], name=r["name"], fields=r["fields"]) for r in rows]


@router.get("/portal/tickets", response_model=PortalTicketsOut, summary="My tickets (customer)")
async def my_tickets(
    customer: CustomerDep,
    sm: SessionmakerDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> PortalTicketsOut:
    page = await portal_tickets.my_tickets(sm, customer, cursor=cursor, limit=limit)
    return PortalTicketsOut(
        tickets=[PortalTicketRow.model_validate(_row(dict(r))) for r in page.tickets],
        next_cursor=page.next_cursor,
    )


async def _ticket_out(
    sm: SessionmakerDep,
    customer: portal.Customer,
    ticket_id: UUID,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> PortalTicketOut:
    found = await portal_tickets.my_ticket(sm, customer, ticket_id, cursor=cursor, limit=limit)
    return PortalTicketOut.model_validate(
        {
            **_row(dict(found.ticket)),
            "messages": [m.__dict__ for m in found.messages],
            "older_messages_cursor": found.older_messages_cursor,
        }
    )


@router.post(
    "/portal/tickets",
    response_model=PortalTicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a ticket (customer)",
    description="Rules, auto-assignment and SLA run as for any new ticket (channel "
    "`portal`). The subject is the first 60 characters of `message`.",
    responses={
        422: {"description": "Empty message, unknown type, or a field missing or wrong"},
        429: {"description": "Over 10 new tickets in an hour; see Retry-After"},
    },
)
async def open_ticket(
    body: NewPortalTicketIn, customer: CustomerDep, sm: SessionmakerDep
) -> PortalTicketOut:
    ticket_id = await portal_tickets.open_ticket(
        sm, customer, message=body.message, type_id=body.type_id, fields=body.fields
    )
    return await _ticket_out(sm, customer, ticket_id)


@router.get(
    "/portal/tickets/{ticket_id}",
    response_model=PortalTicketOut,
    summary="One of my tickets (customer)",
    responses={404: {"description": "Not found, or not this customer's"}},
)
async def my_ticket(
    ticket_id: UUID,
    customer: CustomerDep,
    sm: SessionmakerDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> PortalTicketOut:
    return await _ticket_out(sm, customer, ticket_id, cursor, limit)


@router.post(
    "/portal/tickets/{ticket_id}/messages",
    response_model=PortalTicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reply on my ticket (customer)",
    description="A reply on a resolved ticket reopens it, unless it is only a thank-you.",
    responses={404: {"description": "Not found, or not this customer's"}},
)
async def reply(
    ticket_id: UUID, body: PortalReplyIn, customer: CustomerDep, sm: SessionmakerDep
) -> PortalTicketOut:
    await portal_tickets.reply(sm, customer, ticket_id, body.body)
    return await _ticket_out(sm, customer, ticket_id)


@router.get(
    "/portal/attachments/{attachment_id}",
    response_model=PortalDownloadOut,
    summary="A short-lived link to a file on my ticket (customer)",
    description="Only files on messages the customer can see. 409 when the file failed "
    "the virus scan or (with scanning on) is still being checked.",
    responses={404: {"description": "Not found, or not on this customer's messages"}},
)
async def download(
    attachment_id: UUID,
    request: Request,
    customer: CustomerDep,
    sm: SessionmakerDep,
    store: StoreDep,
) -> PortalDownloadOut:
    settings = _settings(request)
    found = await portal_tickets.download(
        sm,
        store,
        customer,
        attachment_id,
        seconds=settings.download_link_seconds,
        scanning=settings.scan_backend is not ScanBackend.none,
    )
    url = found.url
    if url.startswith("/"):  # local disk: a path on this API
        url = str(request.base_url).rstrip("/") + url
    return PortalDownloadOut(
        url=url,
        expires_at=found.expires_at,
        needs_sign_in=found.needs_sign_in,
        file=PortalFileOut.model_validate(dict(found.attachment)),
    )


@router.get(
    "/portal/attachments/{attachment_id}/content",
    response_class=Response,
    summary="Download a file on my ticket, signed in (customer)",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def file_content(
    attachment_id: UUID,
    request: Request,
    customer: CustomerDep,
    sm: SessionmakerDep,
    store: StoreDep,
) -> Response:
    scanning = _settings(request).scan_backend is not ScanBackend.none
    found = await portal_tickets.file_content(sm, store, customer, attachment_id, scanning=scanning)
    row = found.attachment
    return file_response(found.data, str(row["name"]), str(row["mime"]))


# CSAT (md P5): 1-5 with an optional comment, from the portal or the emailed link.


class RatingIn(Body):
    score: int = Field(ge=1, le=5)
    comment: Annotated[str, StringConstraints(max_length=2000)] | None = None


class RatingPageOut(BaseModel):
    workspace_name: str
    ticket_label: str = Field(description="T-1001")
    subject: str
    rating: CsatOut | None = Field(description="Already rated: show it, allow a change.")


def _csat_out(r: csat.Rating) -> CsatOut:
    return CsatOut(score=r.score, comment=r.comment, source=r.source, rated_at=r.rated_at)


@router.post(
    "/portal/tickets/{ticket_id}/csat",
    response_model=CsatOut,
    summary="Rate my resolved ticket (customer)",
    description="Rating again changes it. Adds a line for the agents.",
    responses={
        404: {"description": "Not found, or not this customer's"},
        409: {"description": "The ticket is not resolved"},
    },
)
async def rate_my_ticket(
    ticket_id: UUID, body: RatingIn, customer: CustomerDep, sm: SessionmakerDep
) -> CsatOut:
    return _csat_out(await csat.rate_in_portal(sm, customer, ticket_id, body.score, body.comment))


_TOKEN = Annotated[str, StringConstraints(min_length=10, max_length=200)]
_LINK_GONE: dict[int | str, dict[str, Any]] = {
    410: {"description": "The link is invalid or expired (30 days)"}
}


@router.get(
    "/public/csat/{token}",
    response_model=RatingPageOut,
    summary="The rating page behind an emailed survey link (no sign-in)",
    description="The page reads the token from the link's `#token=` and an optional "
    "`?score=` (the star the customer clicked in the email).",
    responses=_LINK_GONE,
    dependencies=[PublicLimit],
)
async def rating_page(token: _TOKEN, sm: SessionmakerDep) -> RatingPageOut:
    found = await csat.link_info(sm, token)
    return RatingPageOut(
        workspace_name=found.workspace_name,
        ticket_label=found.ticket_label,
        subject=found.subject,
        rating=_csat_out(found.rating) if found.rating else None,
    )


@router.post(
    "/public/csat/{token}",
    response_model=CsatOut,
    summary="Rate a ticket from the emailed survey link (no sign-in)",
    description="Rating again (within 30 days) changes it.",
    responses={**_LINK_GONE, 409: {"description": "The ticket was reopened"}},
    dependencies=[PublicLimit],
)
async def rate_by_link(token: _TOKEN, body: RatingIn, sm: SessionmakerDep) -> CsatOut:
    return _csat_out(await csat.rate_by_link(sm, token, body.score, body.comment))
