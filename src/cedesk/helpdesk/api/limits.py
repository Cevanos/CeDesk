"""The usage meter, cloud plan limits and retention (md O2, O4, O5). Settings right."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.attachments import file_response
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, StoreDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import limits as rules
from cedesk.helpdesk.service import limits

router = APIRouter(tags=["usage and retention"])


class UsageOut(BaseModel):
    plan: str | None = Field(
        description="The cloud plan; null: no limits (self-hosted, or none set)."
    )
    agents: int = Field(description="Active members who take tickets (light agents are free).")
    tickets_this_month: int = Field(description="Made this calendar month (UTC), not imported.")
    storage_bytes: int = Field(description="Files on tickets.")
    limits: dict[str, int | None] = Field(
        description="agents, tickets_month, storage_bytes; null: no limit."
    )
    retention_days: int | None = Field(
        description="Resolved tickets are deleted this many days after resolving; null: kept."
    )


class RetentionIn(Body):
    # The range is checked in the service, so a refusal reads "Keep resolved tickets 30 to
    # 3650 days, or for good (null)." and not the validator's "Input should be ...".
    closed_ticket_days: int | None = Field(
        description=f"{rules.MIN_RETENTION_DAYS} to {rules.MAX_RETENTION_DAYS}: delete "
        "resolved tickets (and their files) this many days after they were resolved; null "
        "keeps them for good. A cloud plan may cap it.",
    )
    export_before_delete: bool | None = Field(
        default=None,
        description="true: before deleting, write the tickets to a JSON file an Admin can "
        f"download for {rules.KEEP_EXPORT_DAYS} days (`GET /retention/exports`). Left out "
        "or null: unchanged.",
    )


class RetentionOut(BaseModel):
    closed_ticket_days: int | None
    plan_history_days: int | None = Field(description="The cloud plan's cap, if any.")
    effective_days: int | None = Field(description="What the nightly job uses.")
    export_before_delete: bool
    updated_at: datetime | None
    updated_by_name: str | None


@router.get(
    "/usage",
    response_model=UsageOut,
    summary="Usage and plan limits (Settings)",
    description="Over a limit, adding a member, uploading a file, or an agent or API key "
    "opening a ticket answers 402. A customer's own email, chat or portal request is never "
    "refused.",
)
async def get_usage(caller: CallerDep, sm: SessionmakerDep) -> UsageOut:
    return UsageOut(**(await limits.usage(sm, caller)).__dict__)


@router.get("/retention", response_model=RetentionOut, summary="Retention (Settings)")
async def get_retention(caller: CallerDep, sm: SessionmakerDep) -> RetentionOut:
    return RetentionOut(**(await limits.get_retention(sm, caller)).__dict__)


@router.put(
    "/retention",
    response_model=RetentionOut,
    summary="Set retention (Settings; audited)",
    description="The worker deletes once a day; each run is in the audit log (area "
    "retention). Deleted tickets can't be brought back.",
)
async def set_retention(body: RetentionIn, caller: CallerDep, sm: SessionmakerDep) -> RetentionOut:
    view = await limits.set_retention(
        sm, caller, body.closed_ticket_days, body.export_before_delete
    )
    return RetentionOut(**view.__dict__)


class RetentionExportOut(BaseModel):
    id: UUID
    tickets: int = Field(description="How many tickets it holds.")
    size: int = Field(description="Bytes.")
    created_at: datetime
    expires_at: datetime = Field(description="Deleted then.")


@router.get(
    "/retention/exports",
    response_model=list[RetentionExportOut],
    summary="What retention wrote before deleting (Admin)",
    description="One file per batch of deleted tickets, newest first, with export before "
    f"delete on. Kept {rules.KEEP_EXPORT_DAYS} days. Admins only: they hold customer data.",
)
async def retention_exports(caller: CallerDep, sm: SessionmakerDep) -> list[RetentionExportOut]:
    return [RetentionExportOut(**e.__dict__) for e in await limits.retention_exports(sm, caller)]


@router.get(
    "/retention/exports/{export_id}/file",
    summary="Download a retention export (Admin; audited)",
    description="The deleted tickets as JSON: each with its customer, messages (internal "
    "notes too), files' names and rating. 409 when the file is gone.",
    responses={200: {"content": {"application/json": {}}}},
)
async def retention_export_file(
    export_id: UUID, caller: CallerDep, sm: SessionmakerDep, store: StoreDep
) -> Response:
    found = await limits.retention_export_file(sm, store, caller, export_id)
    return file_response(found.data, found.name, "application/json")
