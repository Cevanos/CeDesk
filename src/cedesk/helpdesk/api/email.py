"""Support email addresses (md C9, C10): the addresses customers write to and replies come
from, each with a name and a signature, and the checks that they work: forwarding (a
one-time code emailed to the address must come back in) and DNS (SPF, DKIM, DMARC).
Admin only."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, DnsDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.email import NAME_MAX, SIGNATURE_MAX
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.service import email
from cedesk.settings import Settings

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
    dkim_selector: Annotated[str, StringConstraints(max_length=63)] | None = Field(
        default=None,
        description='Where the mail service keeps the DKIM key (s1, google, ...). "" clears.',
    )


class DnsCheckOut(BaseModel):
    status: Literal["ok", "missing", "wrong", "not_checked", "error"]
    record: str | None = Field(description="The record found, as published (cut to 500).")
    note: str = Field(description="What to do, for the page.")


class DnsOut(BaseModel):
    checked_at: datetime
    domain: str
    spf: DnsCheckOut
    dkim: DnsCheckOut
    dmarc: DnsCheckOut


class EmailAddressOut(BaseModel):
    id: UUID
    address: str
    name: str
    signature: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    forwarding_verified: bool = Field(
        description="A forwarding-check email came back in: emails to it reach CeDesk."
    )
    forwarding_verified_at: datetime | None
    forwarding_check_sent_at: datetime | None = Field(
        description="The last check sent; still waiting while this is newer than "
        "`forwarding_verified_at` (a check works for 7 days)."
    )
    forwarding_check_email: Literal["pending", "sent", "failed"] | None = Field(
        description="The last check email: pending (queued or retrying), sent, or failed "
        "(the mail service refused it, e.g. the domain isn't verified there; see "
        "`forwarding_check_error`). null: none sent."
    )
    forwarding_check_error: str | None = Field(
        description="Why the last check email failed or is being retried."
    )
    forward_to: str | None = Field(
        description="Where to forward this address to (set by the server); null: not "
        "needed or not set up (e.g. CeDesk reads the mailbox itself)."
    )
    dkim_selector: str | None
    dns: DnsOut | None = Field(description="The last DNS check; null: never checked.")


def _out(row: Any, settings: Settings) -> EmailAddressOut:
    found = row["dns_result"]
    return EmailAddressOut(
        id=row["id"],
        address=row["address"],
        name=row["name"],
        signature=row["signature"],
        is_default=row["is_default"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        forwarding_verified=row["forwarding_verified_at"] is not None,
        forwarding_verified_at=row["forwarding_verified_at"],
        forwarding_check_sent_at=row["verify_sent_at"],
        forwarding_check_email=row["verify_email_status"],
        forwarding_check_error=row["verify_email_error"],
        forward_to=settings.mail_forward_to or None,
        dkim_selector=row["dkim_selector"],
        dns=DnsOut(checked_at=row["dns_checked_at"], **found) if found else None,
    )


class TestEmailOut(BaseModel):
    sent_to: str = Field(description="Your own address; the email is queued, not yet sent.")


@router.get(
    "/email-addresses",
    response_model=list[EmailAddressOut],
    summary="Support email addresses, the default first (Admin)",
)
async def list_addresses(
    caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep
) -> list[EmailAddressOut]:
    rows = await email.list_addresses(sm, caller)
    return [_out(r, settings) for r in rows]


@router.post(
    "/email-addresses",
    response_model=EmailAddressOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a support email address (Admin)",
)
async def create_address(
    body: EmailAddressIn, caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep
) -> EmailAddressOut:
    row = await email.create_address(
        sm,
        caller,
        address=body.address,
        name=body.name,
        signature=body.signature,
        is_default=body.is_default,
    )
    return _out(row, settings)


@router.patch(
    "/email-addresses/{address_id}",
    response_model=EmailAddressOut,
    summary="Change an address's name, signature or DKIM selector, or make it the default (Admin)",
)
async def change_address(
    address_id: UUID,
    body: EmailAddressPatch,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
) -> EmailAddressOut:
    row = await email.update_address(
        sm,
        caller,
        address_id,
        name=body.name,
        signature=body.signature,
        is_default=body.is_default,
        dkim_selector=body.dkim_selector,
    )
    return _out(row, settings)


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


@router.post(
    "/email-addresses/{address_id}/forwarding-check",
    response_model=EmailAddressOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email the address a one-time code to check it forwards to CeDesk (Admin)",
    description="The email comes from the address itself, to itself. When it comes back in "
    "(forwarding, or the mailbox CeDesk reads), `forwarding_verified` turns true; it never "
    "makes a ticket. 409 when the last check was sent under a minute ago.",
)
async def forwarding_check(
    address_id: UUID, caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep
) -> EmailAddressOut:
    return _out(await email.send_forwarding_check(sm, caller, address_id), settings)


@router.post(
    "/email-addresses/{address_id}/dns-check",
    response_model=EmailAddressOut,
    summary="Check the address's domain for SPF, DKIM and DMARC now (Admin)",
    description="Each part is ok, missing, wrong, not_checked (DKIM without a selector) or "
    "error (DNS didn't answer: try again), with a note saying what to do. Kept on the "
    "address (`dns`). Takes up to a few seconds.",
)
async def dns_check(
    address_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    lookup: DnsDep,
) -> EmailAddressOut:
    row = await email.check_dns(
        sm, caller, address_id, lookup, spf_include=settings.mail_spf_include or None
    )
    return _out(row, settings)


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
