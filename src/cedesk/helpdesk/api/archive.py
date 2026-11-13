"""The archive tier (md O3): resolved tickets older than the archive age, found by number
or customer and opened read only; and the archive age setting."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import archive as rules
from cedesk.helpdesk.service import archive

router = APIRouter(prefix="/archive", tags=["archive"])


class ArchivedOut(BaseModel):
    id: UUID
    number: int
    label: str
    subject: str
    status: str
    priority: str
    channel: str
    contact_id: UUID | None
    contact_email: str | None
    contact_name: str | None
    team_name: str | None
    assignee_name: str | None
    created_at: datetime
    resolved_at: datetime | None
    archived_at: datetime


class ArchivedMessageOut(BaseModel):
    kind: str = Field(description="customer, agent, ai, note or system.")
    author_name: str | None
    body: str
    created_at: str
    files: list[dict[str, Any]] = Field(description="name, size and type of each file.")


class ArchivedTicketOut(ArchivedOut):
    tags: list[str]
    fields: dict[str, Any]
    messages: list[ArchivedMessageOut]
    files: list[dict[str, Any]] = Field(description="Every file on the ticket: name, size, type.")
    rating: dict[str, Any] | None = Field(description="score, comment, source, rated_at.")


class SettingOut(BaseModel):
    after_days: int | None = Field(description=f"Null: the default ({rules.DEFAULT_DAYS}).")
    effective_days: int
    updated_at: datetime | None
    updated_by_name: str | None


class SettingIn(Body):
    after_days: int | None = Field(
        description=f"{rules.MIN_DAYS} to {rules.MAX_DAYS}; null for the default "
        f"({rules.DEFAULT_DAYS})."
    )


@router.get(
    "/tickets",
    response_model=list[ArchivedOut],
    summary="Find archived tickets by number or customer",
    description=f"Resolved tickets older than the archive age (default {rules.DEFAULT_DAYS} "
    "days) leave the ticket list and live here. Give `number` (1042 or T-1042), or the "
    f"customer's `email` or `contact_id`; newest first, at most {rules.SEARCH_LIMIT}. A "
    "team-only role sees its team's.",
    responses={422: {"description": "Neither a number nor a customer, or a bad number"}},
)
async def find(
    caller: CallerDep,
    sm: SessionmakerDep,
    number: Annotated[str | None, Query(max_length=20)] = None,
    email: Annotated[str | None, Query(max_length=254)] = None,
    contact_id: UUID | None = None,
) -> list[ArchivedOut]:
    found = await archive.search(sm, caller, number=number, contact_id=contact_id, email=email)
    return [ArchivedOut(**f.__dict__) for f in found]


@router.get(
    "/tickets/{ticket_id}",
    response_model=ArchivedTicketOut,
    summary="Open an archived ticket (read only)",
    description="Its messages (internal notes too), files' names and rating. It can't be "
    "changed, replied to or reopened.",
    responses={404: {"description": "Not in the archive, or another team's"}},
)
async def open_one(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ArchivedTicketOut:
    got = await archive.get(sm, caller, ticket_id)
    data = got.__dict__ | {"messages": [ArchivedMessageOut(**m.__dict__) for m in got.messages]}
    return ArchivedTicketOut(**data)


@router.get("/settings", response_model=SettingOut, summary="The archive age (Settings)")
async def get_setting(caller: CallerDep, sm: SessionmakerDep) -> SettingOut:
    return SettingOut(**(await archive.get_setting(sm, caller)).__dict__)


@router.put(
    "/settings",
    response_model=SettingOut,
    summary="Set the archive age (Settings; audited)",
    description="Resolved tickets older than this move to the archive at the next nightly "
    "run. Retention still deletes them when it says so.",
)
async def set_setting(body: SettingIn, caller: CallerDep, sm: SessionmakerDep) -> SettingOut:
    return SettingOut(**(await archive.set_setting(sm, caller, body.after_days)).__dict__)
