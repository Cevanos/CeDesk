"""The usage meter, cloud plan limits and retention (md O2, O4, O5). Settings right."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
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
    closed_ticket_days: int | None = Field(
        ge=rules.MIN_RETENTION_DAYS,
        le=rules.MAX_RETENTION_DAYS,
        description="Delete resolved tickets (and their files) this many days after they "
        "were resolved; null keeps them for good. A cloud plan may cap it.",
    )


class RetentionOut(BaseModel):
    closed_ticket_days: int | None
    plan_history_days: int | None = Field(description="The cloud plan's cap, if any.")
    effective_days: int | None = Field(description="What the nightly job uses.")
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
    view = await limits.set_retention(sm, caller, body.closed_ticket_days)
    return RetentionOut(**view.__dict__)
