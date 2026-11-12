"""Privacy (md S11): export or erase one customer. Admins only."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import RowMapping

from cedesk.helpdesk.api.attachments import file_response
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, StoreDep
from cedesk.helpdesk.service import privacy

router = APIRouter(tags=["privacy"])


class PrivacyRequestOut(BaseModel):
    id: UUID
    kind: str = Field(description="export or erase")
    contact_id: UUID
    requested_by: UUID | None
    requested_by_name: str | None
    requested_at: datetime
    customer_erased: bool = Field(description="The customer has been erased since.")
    file_ready: bool = Field(
        description="Export only: the file can be downloaded (GET .../file) until "
        "`expires_at`. False after 7 days or once the customer is erased."
    )
    size: int | None
    expires_at: datetime | None
    counts: dict[str, int] = Field(
        description="Export: tickets and messages in the file. Erase: how many of each "
        "thing were changed (tickets, messages, files, emails, notifications, tasks, "
        "approvals, time_notes, rating_comments, portal_sessions, portal_codes, events, "
        "exports). Counts only, never content."
    )

    @classmethod
    def of(cls, row: RowMapping) -> "PrivacyRequestOut":
        return cls(
            id=row["id"],
            kind=row["kind"],
            contact_id=row["contact_id"],
            requested_by=row["requested_by"],
            requested_by_name=row["requested_by_name"],
            requested_at=row["requested_at"],
            customer_erased=row["erased_at"] is not None,
            file_ready=row["object_key"] is not None,
            size=row["size"],
            expires_at=row["expires_at"],
            counts=row["counts"],
        )


class ExportBody(BaseModel):
    contact_id: UUID | None = None
    email: str | None = Field(
        None, min_length=3, max_length=254, description="Instead of contact_id."
    )


class EraseBody(BaseModel):
    contact_id: UUID
    confirm_email: str = Field(
        min_length=3,
        max_length=254,
        description="The customer's email typed again. Erasing can't be undone.",
    )


@router.post(
    "/privacy/export",
    status_code=status.HTTP_201_CREATED,
    response_model=PrivacyRequestOut,
    summary="Export everything about one customer (Admin)",
    description="Makes a JSON file: the customer, every ticket with every message (internal "
    "notes too, marked `note`), file names and ratings. Download it with GET "
    "/privacy/requests/{id}/file within 7 days. Audited.",
)
async def export(
    body: ExportBody, caller: CallerDep, sm: SessionmakerDep, store: StoreDep
) -> PrivacyRequestOut:
    row = await privacy.export(sm, store, caller, contact_id=body.contact_id, email=body.email)
    return PrivacyRequestOut.of(row)


@router.post(
    "/privacy/erase",
    status_code=status.HTTP_201_CREATED,
    response_model=PrivacyRequestOut,
    summary="Erase one customer (Admin; can't be undone)",
    description="Replaces the customer's name and address, every message, subject and field "
    "on their tickets, rating comments, task and approval texts and queued emails with "
    "`(erased)`; deletes their files and exports from storage; signs them out of the "
    "portal; stops events not yet sent. Ticket counts, reply times, scores and time logged "
    "stay. 422 when `confirm_email` doesn't match; 409 when already erased. A chat kept in "
    "Cevanos must be erased in Cevanos too. Audited.",
)
async def erase(
    body: EraseBody, caller: CallerDep, sm: SessionmakerDep, store: StoreDep
) -> PrivacyRequestOut:
    row = await privacy.erase(sm, store, caller, body.contact_id, confirm_email=body.confirm_email)
    return PrivacyRequestOut.of(row)


@router.get(
    "/privacy/requests",
    response_model=list[PrivacyRequestOut],
    summary="Recent exports and erasures (Admin), newest first",
)
async def list_requests(
    caller: CallerDep,
    sm: SessionmakerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PrivacyRequestOut]:
    return [PrivacyRequestOut.of(r) for r in await privacy.requests(sm, caller, limit=limit)]


@router.get(
    "/privacy/requests/{request_id}/file",
    summary="Download an export (Admin, signed in)",
    description="409 when the file was removed (after 7 days, or the customer was erased).",
    responses={200: {"content": {"application/json": {}}}},
)
async def export_file(
    request_id: UUID, caller: CallerDep, sm: SessionmakerDep, store: StoreDep
) -> Response:
    found = await privacy.export_file(sm, store, caller, request_id)
    return file_response(found.data, found.name, "application/json")
