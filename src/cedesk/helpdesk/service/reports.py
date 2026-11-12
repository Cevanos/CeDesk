"""The team report (md R1, R2, R6): Supervisors and Admins.

Rules for what counts are in domain/reports.py. The first-reply SLA met % uses the same
SLA rules as each ticket's SLA pill (service/sla.py), so the two always agree: of the
tickets created in the period whose first-reply clock has stopped or run out, the share
answered in time.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import reports as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import reports as repo
from cedesk.helpdesk.service import scope, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_TEAM = "No team"


@dataclass(frozen=True)
class Csat:
    ratings: int
    average: float | None
    happy_percent: float | None
    by_score: dict[int, int]  # 5 down to 1


@dataclass(frozen=True)
class Summary:
    open: int
    open_breaching: int
    created: int
    resolved: int
    avg_first_response_minutes: float | None
    avg_resolution_minutes: float | None
    first_reply_sla_met_percent: float | None
    first_reply_sla_measured: int
    csat: Csat


@dataclass(frozen=True)
class TeamRow:
    team_id: UUID | None
    name: str
    open: int
    created: int
    resolved: int
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


@dataclass(frozen=True)
class AgentRow:
    member_id: UUID
    name: str
    role: str
    status: str
    open: int
    resolved: int
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


@dataclass(frozen=True)
class TeamReport:
    start: datetime
    end: datetime
    team_id: UUID | None
    summary: Summary
    teams: list[TeamRow]
    agents: list[AgentRow]


_EMPTY: Mapping[str, Any] = {
    "open": 0, "breaching": 0, "created": 0, "resolved": 0,
    "first_response_seconds": None, "resolution_seconds": None,
}  # fmt: skip
_NO_RATINGS: Mapping[str, Any] = {"ratings": 0, "average": None, "happy": 0}


def _avg(value: Any) -> float | None:
    return round(float(value), 2) if value is not None else None


def _split(
    rows: Sequence[Mapping[Any, Any]],
) -> tuple[Mapping[str, Any] | None, dict[Any, Mapping[str, Any]], dict[Any, Mapping[str, Any]]]:
    """GROUPING SETS rows → (the total, per team, per assignee)."""
    total = None
    by_team: dict[Any, Mapping[str, Any]] = {}
    by_agent: dict[Any, Mapping[str, Any]] = {}
    for r in rows:
        if r.get("by_all_scores", 1) == 0:
            continue  # a per-score row
        if r["by_all_teams"] and r["by_all_agents"]:
            total = r
        elif not r["by_all_teams"]:
            by_team[r["team_id"]] = r
        else:
            by_agent[r["assignee_id"]] = r
    return total, by_team, by_agent


async def team_report(
    sm: Sessionmaker,
    caller: Caller,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    team_id: UUID | None = None,
) -> TeamReport:
    require(caller, Action.reports)
    only = scope.visible_team(caller)
    if only is not None:
        # A `team_only` role reports on its own team (md A3).
        if team_id not in (None, only):
            raise NotFound("Team not found.")
        team_id = only
    now = datetime.now(UTC)
    try:
        start, end = rules.period(start, end, now)
    except rules.BadPeriod as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        if team_id is not None and not await repo.team_exists(s, team_id):
            raise NotFound("Team not found.")
        ticket_rows = await repo.tickets(s, start, end, now, team_id, rules.IMPORT_SOURCES)
        rating_rows = await repo.ratings(s, start, end, team_id, rules.HAPPY)
        facts = await repo.sla_facts(s, start, end, team_id, rules.IMPORT_SOURCES)
        cfg = await sla.config(s)
        teams = await repo.teams(s)
        agents = await repo.agents(s, team_id)

    total, ticket_by_team, ticket_by_agent = _split(ticket_rows)
    rated, rated_by_team, rated_by_agent = _split(rating_rows)
    by_score = {score: 0 for score in range(5, 0, -1)}
    for r in rating_rows:
        if r["by_all_scores"] == 0:
            by_score[int(r["score"])] = int(r["ratings"])
    t = total or _EMPTY
    c = rated or _NO_RATINGS

    met = missed = 0
    for row in facts:
        answer = sla.first_reply_met(row, cfg, now)
        met += answer is True
        missed += answer is False

    summary = Summary(
        open=int(t["open"]),
        open_breaching=int(t["breaching"]),
        created=int(t["created"]),
        resolved=int(t["resolved"]),
        avg_first_response_minutes=rules.minutes(t["first_response_seconds"]),
        avg_resolution_minutes=rules.minutes(t["resolution_seconds"]),
        first_reply_sla_met_percent=rules.percent(met, met + missed),
        first_reply_sla_measured=met + missed,
        csat=Csat(
            ratings=int(c["ratings"]),
            average=_avg(c["average"]),
            happy_percent=rules.percent(int(c["happy"]), int(c["ratings"])),
            by_score=by_score,
        ),
    )

    shown: list[tuple[UUID | None, str]] = [
        (r["id"], str(r["name"])) for r in teams if team_id is None or r["id"] == team_id
    ]
    if team_id is None and None in ticket_by_team:
        shown.append((None, NO_TEAM))
    team_rows = []
    for tid, name in shown:
        tr = ticket_by_team.get(tid, _EMPTY)
        cr = rated_by_team.get(tid, _NO_RATINGS)
        team_rows.append(
            TeamRow(
                team_id=tid,
                name=name,
                open=int(tr["open"]),
                created=int(tr["created"]),
                resolved=int(tr["resolved"]),
                avg_first_response_minutes=rules.minutes(tr["first_response_seconds"]),
                csat_ratings=int(cr["ratings"]),
                csat_average=_avg(cr["average"]),
            )
        )

    agent_rows = []
    for a in agents:
        ar = ticket_by_agent.get(a["id"], _EMPTY)
        cr = rated_by_agent.get(a["id"], _NO_RATINGS)
        agent_rows.append(
            AgentRow(
                member_id=a["id"],
                name=str(a["name"]),
                role=str(a["role"]),
                status=str(a["status"]),
                open=int(ar["open"]),
                resolved=int(ar["resolved"]),
                avg_first_response_minutes=rules.minutes(ar["first_response_seconds"]),
                csat_ratings=int(cr["ratings"]),
                csat_average=_avg(cr["average"]),
            )
        )
    return TeamReport(start, end, team_id, summary, team_rows, agent_rows)
