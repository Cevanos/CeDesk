"""Reports (md R1, R2): the team report. Supervisors and Admins."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.domain import reports as rules
from cedesk.helpdesk.service import reports

router = APIRouter(tags=["reports"])


class CsatSummaryOut(BaseModel):
    ratings: int
    average: float | None
    happy_percent: float | None = Field(description=f"Share of ratings {rules.HAPPY} or 5.")
    by_score: dict[int, int] = Field(description="Ratings per score, 5 down to 1 (bars).")


class SummaryOut(BaseModel):
    open: int = Field(description="Open or Pending now.")
    open_breaching: int = Field(description="Open or Pending now and past their SLA time.")
    created: int
    resolved: int
    avg_first_response_minutes: float | None = Field(
        description="Tickets created in the period: creation to the first agent reply."
    )
    avg_resolution_minutes: float | None = Field(
        description="Tickets resolved in the period: creation to resolution."
    )
    first_reply_sla_met_percent: float | None = Field(
        description="Of the tickets created in the period whose first-reply clock stopped "
        "or ran out, the share answered in time (business hours, as the SLA pill)."
    )
    first_reply_sla_measured: int = Field(description="How many tickets that % is out of.")
    csat: CsatSummaryOut


class TeamRowOut(BaseModel):
    team_id: UUID | None = Field(description='null: the "No team" row.')
    name: str
    open: int
    created: int
    resolved: int
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


class AgentRowOut(BaseModel):
    member_id: UUID
    name: str
    role: str
    status: str = Field(description="available, on_break, transfers_only or offline (now)")
    open: int = Field(description="Assigned to them, Open or Pending now.")
    resolved: int = Field(description="Assigned to them and resolved in the period.")
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


class TeamReportOut(BaseModel):
    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    team_id: UUID | None
    summary: SummaryOut
    teams: list[TeamRowOut]
    agents: list[AgentRowOut] = Field(description="Active members except light agents.")

    model_config = {"populate_by_name": True}


@router.get(
    "/reports/team",
    response_model=TeamReportOut,
    response_model_by_alias=True,
    summary="Team report (Supervisor, Admin)",
    description="Created, resolved and CSAT count within [from, to); open and breaching "
    "are as of now. Imported tickets are left out of the time metrics; their CSAT counts. "
    "Times are wall-clock minutes. Tickets merged into another are left out.",
    responses={
        403: {"description": "Supervisors and Admins only"},
        404: {"description": "No such team"},
        422: {"description": "from after to, no time zone, or over 366 days"},
    },
)
async def team_report(
    caller: CallerDep,
    sm: SessionmakerDep,
    start: Annotated[
        datetime | None,
        Query(alias="from", description="Default: 30 days before `to`. With a time zone."),
    ] = None,
    end: Annotated[datetime | None, Query(alias="to", description="Default: now.")] = None,
    team_id: Annotated[UUID | None, Query(description="Only this team.")] = None,
) -> TeamReportOut:
    found = await reports.team_report(sm, caller, start=start, end=end, team_id=team_id)
    return TeamReportOut(
        from_=found.start,
        to=found.end,
        team_id=found.team_id,
        summary=SummaryOut(
            **{k: v for k, v in found.summary.__dict__.items() if k != "csat"},
            csat=CsatSummaryOut(**found.summary.csat.__dict__),
        ),
        teams=[TeamRowOut(**r.__dict__) for r in found.teams],
        agents=[AgentRowOut(**r.__dict__) for r in found.agents],
    )
