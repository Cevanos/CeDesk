"""Demo and sample data (md D1-D3): Admin only, and only on a server that turned it on
(CEDESK_DEMO_ENABLED=true) and is not production."""

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep, StoreDep
from cedesk.helpdesk.service import demo
from cedesk.settings import Env, Settings

router = APIRouter(prefix="/demo", tags=["demo"])

_OFF: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Not an Admin, demo data not turned on for this server "
        "(CEDESK_DEMO_ENABLED), or a production server (CEDESK_ENV=production)"
    }
}
_THERE: dict[int | str, dict[str, Any]] = {
    409: {"description": "The demo (or sample) tickets are already there: use reset"}
}


class DemoOut(BaseModel):
    created: int = Field(description="Tickets made.")
    deleted: int = Field(description="Old demo tickets deleted (reset only).")


def _out(got: demo.DemoResult) -> DemoOut:
    return DemoOut(created=got.created, deleted=got.deleted)


def _gate(settings: Settings) -> dict[str, bool]:
    return {"enabled": settings.demo_enabled, "production": settings.env is Env.production}


@router.post(
    "/seed",
    response_model=DemoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Load the demo",
    description="16 tickets: the 6 samples, 5 past tickets resolved and rated over the last "
    "week, 4 live ones assigned to you (a reply, a note, a task, time logged, one Pending) "
    "and 1 chat handover waiting to be accepted. Customers write in through a channel, so "
    'rules, triage, routing and SLA run. Demo tickets have `source: "demo"`; nothing about '
    "them is emailed or sent to webhooks or Slack. 409 when demo tickets are already there. "
    "Audited.",
    responses=_OFF | _THERE,
)
async def seed(caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep) -> DemoOut:
    return _out(await demo.seed(sm, caller, **_gate(settings)))


@router.post(
    "/samples",
    response_model=DemoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Load sample tickets",
    description="The 6 sample tickets only: new, from customers on email, chat and the web. "
    "409 when they are already there. Audited.",
    responses=_OFF | _THERE,
)
async def samples(caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep) -> DemoOut:
    return _out(await demo.samples(sm, caller, **_gate(settings)))


@router.post(
    "/reset",
    response_model=DemoOut,
    summary="Reset the demo",
    description='Loads the demo again, then deletes the old demo tickets (`source: "demo"`, '
    "with their messages, notes, tasks, time, ratings and files) and the demo customers "
    "left with no ticket. Tickets made by hand, imported or from a channel stay, and so do "
    "members and settings. If anything fails, the old demo stays as it was. Audited.",
    responses=_OFF,
)
async def reset(
    caller: CallerDep, sm: SessionmakerDep, store: StoreDep, settings: SettingsDep
) -> DemoOut:
    return _out(await demo.reset(sm, store, caller, **_gate(settings)))
