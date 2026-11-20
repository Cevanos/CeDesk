"""Backlog over time (feature-gap plan 3.1): `GET /reports/backlog`, from the daily
snapshots the worker writes. See domain/backlog.py for what a row means."""

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.reports import (
    CSV_OK,
    REPORT_ERRORS,
    TEAM_ERRORS,
    EndQuery,
    FormatQuery,
    StartQuery,
    TeamQuery,
    csv_response,
)
from cedesk.helpdesk.domain.report_csv import Table
from cedesk.helpdesk.service import backlog

router = APIRouter(tags=["reports"])


class BacklogDayOut(BaseModel):
    day: date = Field(description="The local day these numbers are about (about its end).")
    open: int
    pending: int
    breaching: int = Field(description="Open or Pending and past their SLA time.")
    taken_at: datetime = Field(
        description="When the snapshot was really taken: the first worker pass after the day "
        "ended, which can be hours later if the worker was down at midnight."
    )


class BacklogReportOut(BaseModel):
    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    team_id: UUID | None = None
    timezone: str = Field(description="The workspace's time zone: what a `day` means.")
    first_day: date | None = Field(
        description="Where this workspace's history starts; null: no snapshot yet. Days "
        "before it, and days the worker was down, are not in `days`."
    )
    days: list[BacklogDayOut] = Field(description="Oldest first; only days with a snapshot.")

    model_config = {"populate_by_name": True}


def _tables(found: backlog.BacklogReport) -> list[Table]:
    return [
        Table(
            "Backlog by day",
            ("Day", "Open", "Pending", "Breaching", "Taken at"),
            [
                (d.day.isoformat(), d.open, d.pending, d.breaching, d.taken_at.isoformat())
                for d in found.days
            ],
        )
    ]


@router.get(
    "/reports/backlog",
    response_model=BacklogReportOut,
    response_model_by_alias=True,
    summary="Open tickets at the end of each past day (Supervisor, Admin)",
    description="From daily snapshots the worker takes, so history starts the day this "
    "feature shipped and a day the worker was down is missing. The days a period touches "
    "are the workspace's local days (its SLA calendar's time zone). `breaching`: open or "
    "pending and past their SLA time at that moment; imported tickets never count. "
    "Tickets merged into another are left out.",
    responses=CSV_OK | REPORT_ERRORS | TEAM_ERRORS,
)
async def backlog_report(
    caller: CallerDep,
    sm: SessionmakerDep,
    start: StartQuery = None,
    end: EndQuery = None,
    team_id: TeamQuery = None,
    format_: FormatQuery = "json",
) -> BacklogReportOut | Response:
    found = await backlog.backlog_report(sm, caller, start=start, end=end, team_id=team_id)
    if format_ == "csv":
        return csv_response("backlog", "Backlog by day", found.start, found.end, found.team_id,
                            _tables(found))  # fmt: skip
    return BacklogReportOut(
        from_=found.start,
        to=found.end,
        team_id=found.team_id,
        timezone=found.timezone,
        first_day=found.first_day,
        days=[BacklogDayOut(**d.__dict__) for d in found.days],
    )
