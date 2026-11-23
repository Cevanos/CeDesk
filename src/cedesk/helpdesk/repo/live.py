"""SQL for the live view (feature-gap plan 3.4). RLS applies; see domain/live.py."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.reports import IMPORT_SOURCES

Row = RowMapping

_TEAM = "(CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))"
_LIVE = "t.merged_into_id IS NULL AND t.status <> 'resolved'"
_OURS = "NOT coalesce(t.source = ANY(CAST(:imported AS text[])), false)"

_COUNTS = f"""
SELECT GROUPING(t.team_id) AS by_all_teams, t.team_id,
    count(*) FILTER (WHERE t.status = 'open') AS open,
    count(*) FILTER (WHERE t.status = 'pending' AND t.snoozed_until IS NULL) AS pending,
    count(*) FILTER (WHERE t.status = 'pending' AND t.snoozed_until IS NOT NULL) AS snoozed,
    count(*) FILTER (WHERE t.sla_due_at < :now AND t.snoozed_until IS NULL AND {_OURS})
        AS breached,
    count(*) FILTER (WHERE t.sla_due_at >= :now AND t.sla_due_at < :until
                     AND t.snoozed_until IS NULL AND {_OURS}) AS at_risk
FROM hd_ticket t
WHERE {_LIVE} AND {_TEAM}
GROUP BY GROUPING SETS ((), (t.team_id))
"""  # noqa: S608 - fixed strings only

_WAITING = f"""
SELECT t.id, t.number, t.subject, t.priority, t.team_id, t.assignee_id, t.created_at
FROM hd_ticket t
WHERE {_LIVE} AND t.status = 'open' AND t.first_reply_at IS NULL AND t.snoozed_until IS NULL
  AND {_OURS} AND {_TEAM}
ORDER BY t.created_at, t.id LIMIT :limit
"""  # noqa: S608 - fixed strings only

_DUE = f"""
SELECT t.id, t.number, t.subject, t.priority, t.team_id, t.assignee_id, t.sla_due_at
FROM hd_ticket t
WHERE {_LIVE} AND t.snoozed_until IS NULL AND t.sla_due_at >= :now AND t.sla_due_at < :until
  AND {_OURS} AND {_TEAM}
ORDER BY t.sla_due_at, t.id LIMIT :limit
"""  # noqa: S608 - fixed strings only


def _p(team_id: UUID | None, **more: object) -> dict[str, object]:
    return {"team": team_id, "imported": list(IMPORT_SOURCES)} | more


async def counts(
    session: AsyncSession, team_id: UUID | None, now: datetime, window: timedelta
) -> Sequence[Row]:
    """The total (`by_all_teams` = 1) and one row per team (team_id NULL: no team)."""
    result = await session.execute(text(_COUNTS), _p(team_id, now=now, until=now + window))
    return result.mappings().all()


async def waiting(session: AsyncSession, team_id: UUID | None, limit: int) -> Sequence[Row]:
    result = await session.execute(text(_WAITING), _p(team_id, limit=limit))
    return result.mappings().all()


async def due_soon(
    session: AsyncSession, team_id: UUID | None, now: datetime, window: timedelta, limit: int
) -> Sequence[Row]:
    result = await session.execute(
        text(_DUE), _p(team_id, now=now, until=now + window, limit=limit)
    )
    return result.mappings().all()
