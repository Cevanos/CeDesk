"""SQL for the daily backlog snapshots (feature-gap plan 3.1). Run inside a
`workspace_session`."""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.reports import IMPORT_SOURCES

Row = RowMapping

_ZERO = "'00000000-0000-0000-0000-000000000000'"

# Counts of what is open now: one row for the workspace (`total`, always there, so "was a
# snapshot taken" is one lookup), one for every team (zeros included, so a quiet team shows 0
# and not a gap) and one for tickets with no team. Merged tickets are left out, as in every
# report; a ticket from another help desk never counts as breaching (its times are not ours).
_CAPTURE = f"""
WITH c AS (
    SELECT t.team_id,
           count(*) FILTER (WHERE t.status = 'open') AS open,
           count(*) FILTER (WHERE t.status = 'pending') AS pending,
           count(*) FILTER (WHERE t.sla_due_at < :now
                            AND NOT coalesce(t.source = ANY(CAST(:imported AS text[])), false))
               AS breaching
    FROM hd_ticket t
    WHERE t.status <> 'resolved' AND t.merged_into_id IS NULL
    GROUP BY t.team_id
)
INSERT INTO hd_backlog_snapshot (workspace_id, day, kind, team_id, open, pending, breaching)
SELECT CAST(:ws AS uuid), CAST(:day AS date), 'total', CAST(NULL AS uuid),
       CAST(coalesce(sum(open), 0) AS integer), CAST(coalesce(sum(pending), 0) AS integer),
       CAST(coalesce(sum(breaching), 0) AS integer)
FROM c
UNION ALL
SELECT CAST(:ws AS uuid), CAST(:day AS date), 'team', tm.id,
       CAST(coalesce(c.open, 0) AS integer), CAST(coalesce(c.pending, 0) AS integer),
       CAST(coalesce(c.breaching, 0) AS integer)
FROM hd_team tm LEFT JOIN c ON c.team_id = tm.id
UNION ALL
SELECT CAST(:ws AS uuid), CAST(:day AS date), 'no_team', CAST(NULL AS uuid),
       CAST(c.open AS integer), CAST(c.pending AS integer), CAST(c.breaching AS integer)
FROM c WHERE c.team_id IS NULL
ON CONFLICT (workspace_id, day, kind, (coalesce(team_id, {_ZERO}::uuid)))
DO UPDATE SET open = EXCLUDED.open, pending = EXCLUDED.pending,
              breaching = EXCLUDED.breaching, taken_at = now()
"""  # noqa: S608 - fixed strings only


async def has_day(session: AsyncSession, day: date) -> bool:
    """Whether the workspace's total row for `day` exists."""
    result = await session.execute(
        text("SELECT 1 FROM hd_backlog_snapshot WHERE day = :day AND kind = 'total'"),
        {"day": day},
    )
    return result.first() is not None


async def capture(session: AsyncSession, workspace_id: UUID, day: date, now: datetime) -> None:
    """Writes (or rewrites) the rows for `day` from the tickets as they are now."""
    await session.execute(
        text(_CAPTURE),
        {"ws": workspace_id, "day": day, "now": now, "imported": list(IMPORT_SOURCES)},
    )


async def first_day(session: AsyncSession) -> date | None:
    """The earliest day with a snapshot: where this workspace's history starts."""
    result = await session.execute(
        text("SELECT min(day) FROM hd_backlog_snapshot WHERE kind = 'total'")
    )
    found = result.scalar_one()
    return found if isinstance(found, date) else None


async def days(
    session: AsyncSession, first: date, last: date, team_id: UUID | None
) -> Sequence[Row]:
    """One row per day in [first, last] that has a snapshot: the workspace, or one team."""
    if team_id is None:
        result = await session.execute(
            text(
                "SELECT day, open, pending, breaching, taken_at FROM hd_backlog_snapshot "
                "WHERE kind = 'total' AND day BETWEEN :first AND :last ORDER BY day"
            ),
            {"first": first, "last": last},
        )
    else:
        result = await session.execute(
            text(
                "SELECT day, open, pending, breaching, taken_at FROM hd_backlog_snapshot "
                "WHERE kind = 'team' AND team_id = :team AND day BETWEEN :first AND :last "
                "ORDER BY day"
            ),
            {"team": team_id, "first": first, "last": last},
        )
    return result.mappings().all()


async def prune(session: AsyncSession, before: date) -> int:
    result = await session.execute(
        text("DELETE FROM hd_backlog_snapshot WHERE day < :before"), {"before": before}
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
