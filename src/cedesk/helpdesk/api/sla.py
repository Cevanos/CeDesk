"""SLA settings (md W9-W12). Everyone signed in can read them (the ticket pill explains
itself with them); only an Admin changes the targets. Policies, the calendar and holidays
are the defaults until their editors arrive (Phase 2)."""

from datetime import date, time
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.sla_settings import MAX_MINUTES, PRIORITIES, reads_as
from cedesk.helpdesk.service import sla
from cedesk.helpdesk.service.errors import Invalid
from cedesk.helpdesk.service.sla import Settings

router = APIRouter(tags=["sla"])

Minutes = Annotated[int, Field(ge=1, le=MAX_MINUTES)]
PriorityName = Literal["urgent", "high", "medium", "low"]


class TargetOut(BaseModel):
    priority: PriorityName
    first_reply_minutes: int
    resolve_minutes: int
    reads_as: str = Field(description="The sentence under the row, e.g. 'Urgent tickets get ...'")
    is_default: bool = Field(description="true: never changed by an Admin.")


class TargetIn(Body):
    priority: PriorityName
    first_reply_minutes: Minutes
    resolve_minutes: Minutes


class TargetsIn(Body):
    targets: list[TargetIn] = Field(min_length=1, max_length=4)


class PolicyOut(BaseModel):
    position: int = Field(description="First enabled match wins, from 1.")
    name: str
    enabled: bool
    vip_only: bool
    channels: list[str] = Field(description="Empty: every channel.")
    priorities: list[str] = Field(description="Empty: every priority.")
    first_reply_minutes: int | None = Field(description="null: the priority target.")
    resolve_minutes: int | None = Field(description="null: the priority target.")


class HolidayOut(BaseModel):
    day: date
    name: str


class CalendarOut(BaseModel):
    timezone: str
    days: list[int] = Field(description="ISO weekdays: 1 Monday ... 7 Sunday.")
    opens: time
    closes: time
    always_open: bool = Field(description="24/7: every minute counts, holidays too.")
    holidays: list[HolidayOut]
    is_default: bool


class SlaSettingsOut(BaseModel):
    targets: list[TargetOut]
    policies: list[PolicyOut]
    policies_are_default: bool
    calendar: CalendarOut


def _out(settings: Settings) -> SlaSettingsOut:
    cfg = settings.config
    cal = cfg.calendar
    return SlaSettingsOut(
        targets=[
            TargetOut(
                priority=p,
                first_reply_minutes=cfg.targets[p].first_reply_minutes,
                resolve_minutes=cfg.targets[p].resolve_minutes,
                reads_as=reads_as(p, cfg.targets[p]),
                is_default=p not in settings.saved_targets,
            )
            for p in PRIORITIES
        ],
        policies=[
            PolicyOut(
                position=i,
                name=p.name,
                enabled=p.enabled,
                vip_only=p.vip_only,
                channels=sorted(p.channels),
                priorities=sorted(p.priorities),
                first_reply_minutes=p.first_reply_minutes,
                resolve_minutes=p.resolve_minutes,
            )
            for i, p in enumerate(cfg.policies, 1)
        ],
        policies_are_default=not settings.custom_policies,
        calendar=CalendarOut(
            timezone=cal.timezone,
            days=sorted(cal.days),
            opens=cal.start,
            closes=cal.end,
            always_open=cal.always_open,
            holidays=[HolidayOut(day=d, name=n) for d, n in sorted(cfg.holiday_names.items())],
            is_default=not settings.custom_calendar,
        ),
    )


@router.get("/sla", response_model=SlaSettingsOut, summary="SLA targets, policies and hours")
async def read_sla(caller: CallerDep, sm: SessionmakerDep) -> SlaSettingsOut:
    return _out(await sla.read_settings(sm, caller))


@router.put(
    "/sla/targets",
    response_model=SlaSettingsOut,
    summary="Admin: change the first-reply and resolve targets per priority",
    description="Only the priorities sent change. The first reply can't be due after the "
    "resolution (422). Every open ticket's SLA is worked out again.",
)
async def set_targets(body: TargetsIn, caller: CallerDep, sm: SessionmakerDep) -> SlaSettingsOut:
    targets: dict[str, tuple[int, int]] = {
        t.priority: (t.first_reply_minutes, t.resolve_minutes) for t in body.targets
    }
    if len(targets) != len(body.targets):
        raise Invalid("Each priority once.")
    return _out(await sla.set_targets(sm, caller, targets))
