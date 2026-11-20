"""Scheduled report emails (feature-gap plan 3.5): a report sent to team members on a schedule.

The recipients are members of the workspace, never free addresses. Each email is plain text
(the numbers and a link to the report). Anyone who gets one can leave the schedule."""

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain import report_schedules as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.service import report_schedules

router = APIRouter(prefix="/report-schedules", tags=["reports"])

ScheduleName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=rules.NAME_MAX)
]
Hour = Annotated[int, Field(ge=0, le=23, description="Hour of the workspace's day (0-23).")]
TIMING = (
    "`cadence` daily, weekly (with `weekday`, 1 Monday to 7 Sunday) or monthly (with "
    "`day_of_month`, 1-28); `hour` in the workspace's time zone (its SLA calendar's)."
)


class ScheduleIn(Body):
    name: ScheduleName
    report_id: UUID | None = Field(default=None, description="A saved report you may see.")
    preset: str | None = Field(
        default=None, description="Or a built-in report from `GET /reports/options`."
    )
    range: str | None = Field(
        default=None,
        description="The period each email covers, one of: "
        + ", ".join(defs.PRESET_RANGES)
        + ". Default: last_7d (last_month for monthly).",
    )
    cadence: str = Field(description=TIMING)
    weekday: int | None = None
    day_of_month: int | None = None
    hour: Hour
    recipient_ids: list[UUID] = Field(
        min_length=1,
        max_length=rules.MAX_RECIPIENTS,
        description="Members who hold the reports right and see at least as many teams' "
        "tickets as you do.",
    )


class SchedulePatch(Body):
    """Send only what changes. A new `report_id` or `preset` replaces the old target."""

    name: ScheduleName | None = None
    report_id: UUID | None = None
    preset: str | None = None
    range: str | None = None
    cadence: str | None = None
    weekday: int | None = None
    day_of_month: int | None = None
    hour: Hour | None = None
    enabled: bool | None = Field(default=None, description="Turning it on re-times it from now.")
    recipient_ids: list[UUID] | None = Field(
        default=None, min_length=1, max_length=rules.MAX_RECIPIENTS, description="The whole list."
    )

    @model_validator(mode="after")
    def not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        for name in ("name", "cadence", "hour", "enabled", "recipient_ids"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class RecipientOut(BaseModel):
    member_id: UUID
    name: str


class TargetOut(BaseModel):
    report_id: UUID | None
    preset: str | None
    label: str


class ScheduleOut(BaseModel):
    id: UUID
    name: str
    target: TargetOut
    range: str
    cadence: str
    weekday: int | None
    day_of_month: int | None
    hour: int
    timezone: str = Field(description="What `hour` is in: the workspace's time zone.")
    enabled: bool = Field(description="The worker switches it off if its owner loses the right.")
    next_run_at: datetime
    last_sent_at: datetime | None
    last_error: str | None = Field(description="Why the last run sent nothing, if it didn't.")
    owner_id: UUID
    owner_name: str
    recipients: list[RecipientOut]
    can_edit: bool = Field(description="Its owner, or an Admin.")
    is_recipient: bool = Field(description="You get it; `POST .../leave` stops that.")


def _label(found: report_schedules.Schedule) -> str:
    row = found.row
    if row["preset"] is not None:
        preset = defs.PRESETS.get(row["preset"])
        return preset.label if preset else str(row["preset"])
    return str(row["report_name"])


def _out(caller: Caller, found: report_schedules.Schedule) -> ScheduleOut:
    r = found.row
    return ScheduleOut(
        id=r["id"], name=r["name"],
        target=TargetOut(report_id=r["report_id"], preset=r["preset"], label=_label(found)),
        range=r["range_preset"], cadence=r["cadence"], weekday=r["weekday"],
        day_of_month=r["day_of_month"], hour=r["hour"], timezone=found.timezone,
        enabled=r["enabled"], next_run_at=r["next_run_at"], last_sent_at=r["last_sent_at"],
        last_error=r["last_error"], owner_id=r["owner_id"], owner_name=r["owner_name"],
        recipients=[
            RecipientOut(member_id=p["member_id"], name=p["name"]) for p in found.recipients
        ],
        can_edit=report_schedules.can_change(caller, found),
        is_recipient=report_schedules.is_recipient(caller, found),
    )  # fmt: skip


@router.get(
    "",
    response_model=list[ScheduleOut],
    summary="Schedules you made or receive (Admins see all)",
)
async def list_schedules(caller: CallerDep, sm: SessionmakerDep) -> list[ScheduleOut]:
    return [_out(caller, s) for s in await report_schedules.list_schedules(sm, caller)]


@router.post(
    "",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a report by email (audited)",
    description=f"At most {rules.MAX_PER_WORKSPACE} schedules per workspace and "
    f"{rules.MAX_RECIPIENTS} recipients each. The worker works the report out under *your* "
    "rights each time and checks every recipient again, so someone who lost the right "
    "gets nothing; if you lose it, the schedule switches off.",
    responses={
        403: {"description": "No reports right"},
        422: {
            "description": "A bad timing, a report you can't see, or a recipient who can't "
            "receive it"
        },
    },
)
async def create_schedule(body: ScheduleIn, caller: CallerDep, sm: SessionmakerDep) -> ScheduleOut:
    made = await report_schedules.create_schedule(
        sm, caller, name=body.name, report_id=body.report_id, preset=body.preset,
        range_preset=body.range, cadence=body.cadence, weekday=body.weekday,
        day_of_month=body.day_of_month, hour=body.hour, recipient_ids=body.recipient_ids,
    )  # fmt: skip
    return _out(caller, made)


@router.get("/{schedule_id}", response_model=ScheduleOut, summary="One schedule")
async def get_schedule(schedule_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ScheduleOut:
    return _out(caller, await report_schedules.get_schedule(sm, caller, schedule_id))


@router.patch(
    "/{schedule_id}",
    response_model=ScheduleOut,
    summary="Change a schedule (its owner or an Admin; audited)",
    responses={
        403: {"description": "Not its owner or an Admin"},
        404: {"description": "No such schedule, or not one you make or receive"},
    },
)
async def change_schedule(
    schedule_id: UUID, body: SchedulePatch, caller: CallerDep, sm: SessionmakerDep
) -> ScheduleOut:
    sent: dict[str, Any] = body.model_dump(exclude_unset=True)
    recipients = sent.pop("recipient_ids", None)
    made = await report_schedules.change_schedule(
        sm, caller, schedule_id, changes=sent, recipient_ids=recipients
    )
    return _out(caller, made)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule (its owner or an Admin; audited)",
    responses={403: {"description": "Not its owner or an Admin"}},
)
async def delete_schedule(schedule_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await report_schedules.delete_schedule(sm, caller, schedule_id)


@router.post(
    "/{schedule_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop getting this schedule's emails (any recipient; audited)",
    responses={404: {"description": "You are not a recipient of this schedule"}},
)
async def leave_schedule(schedule_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await report_schedules.leave_schedule(sm, caller, schedule_id)
