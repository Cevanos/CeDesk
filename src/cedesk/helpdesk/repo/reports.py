"""SQL for the team report (md R1, R2). RLS applies. One pass over the tickets grouped
three ways (all, per team, per assignee) and one over the ratings."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo.auth import TAKES_TICKETS

_TEAM = "(CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))"
_LIVE = "t.merged_into_id IS NULL"
_IMPORTED = "coalesce(t.source = ANY(CAST(:imported AS text[])), false)"
_CREATED = "(t.created_at >= :start AND t.created_at < :end)"
_RESOLVED = "(t.resolved_at >= :start AND t.resolved_at < :end)"

_TICKETS = f"""
SELECT
    GROUPING(t.team_id) AS by_all_teams, GROUPING(t.assignee_id) AS by_all_agents,
    t.team_id, t.assignee_id,
    count(*) FILTER (WHERE t.status <> 'resolved') AS open,
    count(*) FILTER (WHERE t.status <> 'resolved' AND t.sla_due_at < :now
                     AND NOT {_IMPORTED}) AS breaching,
    count(*) FILTER (WHERE {_CREATED}) AS created,
    count(*) FILTER (WHERE {_RESOLVED}) AS resolved,
    avg(extract(epoch FROM t.first_reply_at - t.created_at))
        FILTER (WHERE {_CREATED} AND t.first_reply_at IS NOT NULL AND NOT {_IMPORTED})
        AS first_response_seconds,
    avg(extract(epoch FROM t.resolved_at - t.created_at))
        FILTER (WHERE {_RESOLVED} AND NOT {_IMPORTED}) AS resolution_seconds
FROM hd_ticket t
WHERE {_LIVE} AND {_TEAM} AND (t.status <> 'resolved' OR {_CREATED} OR {_RESOLVED})
GROUP BY GROUPING SETS ((), (t.team_id), (t.assignee_id))
"""  # noqa: S608 - fixed strings only

_RATINGS = f"""
SELECT
    GROUPING(t.team_id) AS by_all_teams, GROUPING(t.assignee_id) AS by_all_agents,
    GROUPING(r.score) AS by_all_scores, t.team_id, t.assignee_id, r.score,
    count(*) AS ratings, avg(r.score) AS average, count(*) FILTER (WHERE r.score >= :happy)
        AS happy
FROM hd_csat r JOIN hd_ticket t ON t.id = r.ticket_id
WHERE r.rated_at >= :start AND r.rated_at < :end AND {_LIVE} AND {_TEAM}
GROUP BY GROUPING SETS ((), (t.team_id), (t.assignee_id), (r.score))
"""  # noqa: S608 - fixed strings only

_SLA_FACTS = f"""
SELECT t.id, t.priority, t.channel, t.created_at, t.first_reply_at, t.resolved_at, c.vip
FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id
WHERE {_CREATED} AND {_LIVE} AND {_TEAM} AND NOT {_IMPORTED}
"""  # noqa: S608 - fixed strings only


def _params(
    start: datetime, end: datetime, team_id: UUID | None, imported: Sequence[str]
) -> dict[str, Any]:
    return {"start": start, "end": end, "team": team_id, "imported": list(imported)}


async def tickets(
    session: AsyncSession, start: datetime, end: datetime, now: datetime,
    team_id: UUID | None, imported: Sequence[str],
) -> Sequence[RowMapping]:  # fmt: skip
    result = await session.execute(
        text(_TICKETS), _params(start, end, team_id, imported) | {"now": now}
    )
    return result.mappings().all()


async def ratings(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None, happy: int
) -> Sequence[RowMapping]:
    result = await session.execute(
        text(_RATINGS), _params(start, end, team_id, ()) | {"happy": happy}
    )
    return result.mappings().all()


async def sla_facts(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None,
    imported: Sequence[str],
) -> Sequence[RowMapping]:  # fmt: skip
    """Tickets created in the period (not imported), for the first-reply SLA met %."""
    result = await session.execute(text(_SLA_FACTS), _params(start, end, team_id, imported))
    return result.mappings().all()


async def teams(session: AsyncSession) -> Sequence[RowMapping]:
    result = await session.execute(text("SELECT id, name FROM hd_team ORDER BY lower(name)"))
    return result.mappings().all()


async def agents(session: AsyncSession, team_id: UUID | None) -> Sequence[RowMapping]:
    """Active members who can take tickets (not light agents); with a team, its members."""
    result = await session.execute(
        text(
            "SELECT m.id, m.name, m.role, coalesce(st.status, 'offline') AS status "  # noqa: S608
            "FROM member m LEFT JOIN hd_agent_state st ON st.member_id = m.id "
            "WHERE m.is_active AND " + TAKES_TICKETS + " "
            "AND (CAST(:team AS uuid) IS NULL OR EXISTS (SELECT 1 FROM hd_team_member tm "
            "     WHERE tm.member_id = m.id AND tm.team_id = CAST(:team AS uuid))) "
            "ORDER BY lower(m.name), m.id"
        ),
        {"team": team_id},
    )
    return result.mappings().all()


async def team_exists(session: AsyncSession, team_id: UUID) -> bool:
    result = await session.execute(text("SELECT 1 FROM hd_team WHERE id = :id"), {"id": team_id})
    return result.first() is not None
