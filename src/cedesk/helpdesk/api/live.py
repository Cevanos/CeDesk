"""The live view (feature-gap plan 3.4): `GET /reports/live`, the queue as of now. See
domain/live.py for what "waiting", "at risk" and "online" mean."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.reports import (
    CSV_OK,
    REPORT_ERRORS,
    TEAM_ERRORS,
    FormatQuery,
    TeamQuery,
)
from cedesk.helpdesk.domain import live as rules
from cedesk.helpdesk.domain import report_csv
from cedesk.helpdesk.domain.report_csv import Table
from cedesk.helpdesk.service import live

router = APIRouter(tags=["reports"])


class TeamCountsOut(BaseModel):
    team_id: UUID | None
    name: str
    open: int
    pending: int = Field(description="Pending and not snoozed.")
    snoozed: int
    breached: int = Field(description="Past their SLA time now.")
    at_risk: int = Field(description="Their SLA time falls within `window_minutes`.")


class WaitingOut(BaseModel):
    id: UUID
    label: str
    subject: str
    priority: str
    team_id: UUID | None
    assignee_id: UUID | None
    created_at: datetime
    waiting_minutes: int


class DueOut(BaseModel):
    id: UUID
    label: str
    subject: str
    priority: str
    team_id: UUID | None
    assignee_id: UUID | None
    sla_due_at: datetime
    minutes_left: int


class AgentCountsOut(BaseModel):
    available: int
    on_break: int
    transfers_only: int
    offline: int


class LiveOut(BaseModel):
    generated_at: datetime = Field(description="Shared for a few seconds by everyone watching.")
    team_id: UUID | None
    window_minutes: int
    total: TeamCountsOut
    teams: list[TeamCountsOut]
    waiting: list[WaitingOut] = Field(
        description=f"Open, not snoozed, no agent reply yet: the oldest {rules.WAITING_LIMIT}."
    )
    due_soon: list[DueOut] = Field(
        description=f"SLA time within the window: the soonest {rules.DUE_LIMIT}."
    )
    agents: AgentCountsOut = Field(
        description="By the status each agent set (there is no heartbeat)."
    )


def _tables(found: live.Live) -> list[Table]:
    c = found.total
    return [
        Table(
            "Queue now",
            ("Team", "Open", "Pending", "Snoozed", "Past SLA", "SLA within window"),
            [
                (t.name, t.open, t.pending, t.snoozed, t.breached, t.at_risk)
                for t in [c, *found.teams]
            ],
        ),
        Table(
            "Waiting for a first reply",
            ("Ticket", "Subject", "Priority", "Waiting (minutes)"),
            [(w.label, w.subject, w.priority, w.waiting_minutes) for w in found.waiting],
        ),
        Table(
            "SLA due soon",
            ("Ticket", "Subject", "Priority", "Due at", "Minutes left"),
            [
                (d.label, d.subject, d.priority, d.sla_due_at.isoformat(), d.minutes_left)
                for d in found.due_soon
            ],
        ),
        Table(
            "Agents",
            ("Status", "Agents"),
            list(found.agents.__dict__.items()),
        ),
    ]


@router.get(
    "/reports/live",
    response_model=LiveOut,
    summary="The queue right now: open, waiting, SLA due soon, agents (Supervisor, Admin)",
    description="Refreshed by the page on the live stream's ticket events and every 30 s; "
    "many viewers of the same scope share one answer for about 5 seconds. A role limited "
    "to one team gets that team's queue. Imported tickets never count as waiting, at risk "
    "or breached; merged tickets are left out.",
    responses=CSV_OK | REPORT_ERRORS | TEAM_ERRORS,
)
async def live_view(
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    team_id: TeamQuery = None,
    window_minutes: Annotated[
        int,
        Query(
            ge=rules.MIN_WINDOW_MINUTES,
            le=rules.MAX_WINDOW_MINUTES,
            description="'SLA due soon' means within this many minutes.",
        ),
    ] = rules.DEFAULT_WINDOW_MINUTES,
    fmt: FormatQuery = "json",
) -> LiveOut | Response:
    found = await live.live_view(
        sm,
        caller,
        team_id=team_id,
        window_minutes=window_minutes,
        cache=request.app.state.live_cache,
        timeout_ms=settings.report_timeout_seconds * 1000,
    )
    if fmt == "csv":
        body = report_csv.render(
            "CeDesk live queue",
            [
                ("As of", found.generated_at.isoformat()),
                ("SLA window (minutes)", found.window_minutes),
            ],
            _tables(found),
        )
        name = f"cedesk-live-{datetime.now(UTC):%Y-%m-%d-%H%M}.csv"
        return Response(
            content=body.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    return LiveOut.model_validate(
        {
            "generated_at": found.generated_at, "team_id": found.team_id,
            "window_minutes": found.window_minutes, "total": found.total.__dict__,
            "teams": [t.__dict__ for t in found.teams],
            "waiting": [w.__dict__ for w in found.waiting],
            "due_soon": [d.__dict__ for d in found.due_soon],
            "agents": found.agents.__dict__,
        }
    )  # fmt: skip
