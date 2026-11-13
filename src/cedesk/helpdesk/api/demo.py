"""Demo and sample data (md D1-D3): Admin only, off on a production server."""

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.service import demo
from cedesk.settings import Env

router = APIRouter(prefix="/demo", tags=["demo"])

_OFF: dict[int | str, dict[str, Any]] = {
    403: {"description": "Not an Admin, or a production server (CEDESK_ENV=production)"}
}


class DemoOut(BaseModel):
    created: int = Field(description="Tickets made.")
    deleted: int = Field(description="Tickets deleted first (reset only).")


def _out(got: demo.DemoResult) -> DemoOut:
    return DemoOut(created=got.created, deleted=got.deleted)


@router.post(
    "/seed",
    response_model=DemoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Load the demo",
    description="16 tickets: the 6 samples, 5 past tickets resolved and rated over the last "
    "week, 4 live ones assigned to you (a reply, a note, a task, time logged, one Pending) "
    "and 1 chat handover waiting to be accepted. Customers write in through a channel, so "
    "rules, triage, routing and SLA run. Audited.",
    responses=_OFF,
)
async def seed(caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep) -> DemoOut:
    return _out(await demo.seed(sm, caller, allowed=settings.env is not Env.production))


@router.post(
    "/samples",
    response_model=DemoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Load sample tickets",
    description="The 6 sample tickets only: new, from customers on email, chat and the web. "
    "Audited.",
    responses=_OFF,
)
async def samples(caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep) -> DemoOut:
    return _out(await demo.samples(sm, caller, allowed=settings.env is not Env.production))


@router.post(
    "/reset",
    response_model=DemoOut,
    summary="Reset the demo",
    description="Deletes every ticket in this workspace (with its messages, notes, tasks, "
    "time and ratings), then loads the demo again. Contacts, members and settings stay. "
    "Audited.",
    responses=_OFF,
)
async def reset(caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep) -> DemoOut:
    return _out(await demo.reset(sm, caller, allowed=settings.env is not Env.production))
