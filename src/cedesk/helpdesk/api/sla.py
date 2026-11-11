"""SLA settings (md W9-W12). Everyone signed in can read them (the ticket pill explains
itself with them); only an Admin changes them: targets per priority, the ordered policies,
business hours and holidays. Every change works out each open ticket's SLA again."""

from datetime import date, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.sla import Policy
from cedesk.helpdesk.domain.sla_settings import (
    MAX_MINUTES,
    MAX_POLICIES,
    NAME_MAX,
    POLICY_CHANNELS,
    PRIORITIES,
    reads_as,
)
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
    id: UUID
    day: date
    name: str


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX)]


class PolicyIn(Body):
    name: Name
    enabled: bool = True
    vip_only: bool = Field(default=False, description="Only tickets from VIP customers.")
    channels: list[str] = Field(
        default=[], max_length=len(POLICY_CHANNELS),
        description=f"Empty: every channel. Any of: {', '.join(POLICY_CHANNELS)}.",
    )  # fmt: skip
    priorities: list[PriorityName] = Field(default=[], max_length=4, description="Empty: all.")
    first_reply_minutes: Minutes | None = Field(default=None, description="null: priority target.")
    resolve_minutes: Minutes | None = Field(default=None, description="null: priority target.")


class PoliciesIn(Body):
    policies: list[PolicyIn] | None = Field(
        max_length=MAX_POLICIES,
        description="The whole list in order: the first enabled match wins. The last one must "
        "be on and have no conditions (Default). null: back to the three built-in policies.",
    )


class CalendarIn(Body):
    timezone: str = Field(max_length=64, description="IANA name, e.g. Asia/Kolkata.")
    days: list[int] = Field(min_length=1, max_length=7, description="ISO: 1 Monday ... 7 Sunday.")
    opens: time
    closes: time
    always_open: bool = Field(default=False, description="24/7: days, hours and holidays ignored.")


class HolidayIn(Body):
    day: date
    name: Name


class HolidayPatch(Body):
    day: date | None = None
    name: Name | None = None


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
            holidays=[HolidayOut(id=h.id, day=h.day, name=h.name) for h in settings.holidays],
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


@router.put(
    "/sla/policies",
    response_model=SlaSettingsOut,
    summary="Admin: save the ordered SLA policies (on/off, order, conditions, targets)",
    description="Send the whole list. 422 when a name repeats, a target is out of range, "
    "the last policy has a condition or is off, or another policy matches every ticket.",
)
async def set_policies(body: PoliciesIn, caller: CallerDep, sm: SessionmakerDep) -> SlaSettingsOut:
    policies = None
    if body.policies is not None:
        policies = [
            Policy(
                name=p.name, enabled=p.enabled, vip_only=p.vip_only,
                channels=frozenset(p.channels), priorities=frozenset(p.priorities),
                first_reply_minutes=p.first_reply_minutes, resolve_minutes=p.resolve_minutes,
            )
            for p in body.policies
        ]  # fmt: skip
        if any(len(set(p.channels)) != len(p.channels) for p in body.policies):
            raise Invalid("Each channel once.")
    return _out(await sla.set_policies(sm, caller, policies))


@router.put(
    "/sla/calendar",
    response_model=SlaSettingsOut,
    summary="Admin: business hours (days, hours, time zone) or 24/7",
    description="Hours are the same each working day, closing after opening (no overnight "
    "shifts). 422 for an unknown time zone.",
)
async def set_calendar(body: CalendarIn, caller: CallerDep, sm: SessionmakerDep) -> SlaSettingsOut:
    settings = await sla.set_calendar(
        sm,
        caller,
        timezone=body.timezone,
        days=body.days,
        opens=body.opens,
        closes=body.closes,
        always_open=body.always_open,
    )
    return _out(settings)


@router.post(
    "/holidays",
    response_model=SlaSettingsOut,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: add a holiday (the SLA clock doesn't run that day)",
    responses={409: {"description": "Already a holiday on that day"}},
)
async def add_holiday(body: HolidayIn, caller: CallerDep, sm: SessionmakerDep) -> SlaSettingsOut:
    return _out(await sla.add_holiday(sm, caller, body.day, body.name))


@router.patch(
    "/holidays/{holiday_id}",
    response_model=SlaSettingsOut,
    summary="Admin: rename a holiday or move it to another day",
    responses={404: {"description": "No such holiday"}, 409: {"description": "Day taken"}},
)
async def change_holiday(
    holiday_id: UUID, body: HolidayPatch, caller: CallerDep, sm: SessionmakerDep
) -> SlaSettingsOut:
    return _out(await sla.change_holiday(sm, caller, holiday_id, day=body.day, name=body.name))


@router.delete(
    "/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Admin: remove a holiday",
    responses={404: {"description": "No such holiday"}},
)
async def remove_holiday(holiday_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await sla.remove_holiday(sm, caller, holiday_id)
