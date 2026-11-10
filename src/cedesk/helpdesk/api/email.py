"""Support email addresses (md C9): the addresses customers write to and replies come
from, each with a name and a signature. Admin only."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.email import NAME_MAX, SIGNATURE_MAX
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.service import email

router = APIRouter(tags=["email"])

Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX),
    Field(description="Shown as the sender, e.g. 'Acme Support'."),
]
Signature = Annotated[
    str,
    StringConstraints(max_length=SIGNATURE_MAX),
    Field(
        description="Added under every reply from this address. Placeholders such as "
        "{{agent.name}} are filled when the reply is sent."
    ),
]


class EmailAddressIn(Body):
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] = Field(
        description="e.g. support@acme.in. Stored lower-case; unique across all workspaces."
    )
    name: Name
    signature: Signature = ""
    is_default: bool = Field(
        default=False,
        description="Replies come from it when the customer wrote elsewhere. "
        "The first address is always the default.",
    )


class EmailAddressPatch(Body):
    name: Name | None = None
    signature: Signature | None = None
    is_default: bool | None = Field(
        default=None, description="Only `true`: make another address the default to move it."
    )


class EmailAddressOut(BaseModel):
    id: UUID
    address: str
    name: str
    signature: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class TestEmailOut(BaseModel):
    sent_to: str = Field(description="Your own address; the email is queued, not yet sent.")


@router.get(
    "/email-addresses",
    response_model=list[EmailAddressOut],
    summary="Support email addresses, the default first (Admin)",
)
async def list_addresses(caller: CallerDep, sm: SessionmakerDep) -> list[EmailAddressOut]:
    rows = await email.list_addresses(sm, caller)
    return [EmailAddressOut.model_validate(dict(r)) for r in rows]


@router.post(
    "/email-addresses",
    response_model=EmailAddressOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a support email address (Admin)",
)
async def create_address(
    body: EmailAddressIn, caller: CallerDep, sm: SessionmakerDep
) -> EmailAddressOut:
    row = await email.create_address(
        sm,
        caller,
        address=body.address,
        name=body.name,
        signature=body.signature,
        is_default=body.is_default,
    )
    return EmailAddressOut.model_validate(dict(row))


@router.patch(
    "/email-addresses/{address_id}",
    response_model=EmailAddressOut,
    summary="Change an address's name or signature, or make it the default (Admin)",
)
async def change_address(
    address_id: UUID, body: EmailAddressPatch, caller: CallerDep, sm: SessionmakerDep
) -> EmailAddressOut:
    row = await email.update_address(
        sm,
        caller,
        address_id,
        name=body.name,
        signature=body.signature,
        is_default=body.is_default,
    )
    return EmailAddressOut.model_validate(dict(row))


@router.delete(
    "/email-addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a support email address (Admin)",
)
async def delete_address(address_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await email.delete_address(sm, caller, address_id)


@router.post(
    "/email-addresses/{address_id}/test",
    response_model=TestEmailOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email yourself a test from this address (Admin)",
)
async def send_test(address_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> TestEmailOut:
    return TestEmailOut(sent_to=await email.send_test(sm, caller, address_id))


# Failed emails (md 2B "Reliability": the dead-letter list)


class FailedEmailOut(BaseModel):
    id: UUID
    ticket_id: UUID | None = Field(description="null for account emails (password reset).")
    ticket_label: str | None = Field(description="T-1005, or null.")
    message_id: UUID | None = Field(description="The reply this email carried.")
    to: list[str]
    subject: str
    attempts: int
    last_error: str | None
    can_retry: bool = Field(
        description="false for account emails: their text is removed when they fail. "
        "The person asks for a new link instead."
    )
    created_at: datetime


class FailedEmailPageOut(BaseModel):
    emails: list[FailedEmailOut] = Field(description="Newest first.")
    next_cursor: str | None


@router.get(
    "/email/failed",
    response_model=FailedEmailPageOut,
    summary="Admin: emails that could not be sent after every retry",
)
async def failed_emails(
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> FailedEmailPageOut:
    page = await email.failed_emails(sm, caller, cursor=cursor, limit=limit)
    return FailedEmailPageOut(
        emails=[
            FailedEmailOut(
                id=r["id"],
                ticket_id=r["ticket_id"],
                ticket_label=label(r["ticket_number"]) if r["ticket_number"] else None,
                message_id=r["message_id"],
                to=list(r["to_addresses"]),
                subject=str(r["subject"]),
                attempts=int(r["attempts"]),
                last_error=r["last_error"],
                can_retry=not r["sensitive"],
                created_at=r["created_at"],
            )
            for r in page.emails
        ],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/email/{email_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Admin: send a failed email again",
    description="It goes back in the queue (tries from zero) and the worker sends it within "
    "seconds; its ticket gets a line saying who did it. 404 if it isn't failed or can't be "
    "retried (account emails).",
)
async def retry_email(email_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await email.retry_email(sm, caller, email_id)
