"""SQL for the SLA settings and each ticket's stored due time. Always inside a
`workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_FACTS = (
    "SELECT t.id, t.priority, t.channel, t.created_at, t.first_reply_at, t.resolved_at, "
    "c.vip FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id"
)


async def targets(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(
        text("SELECT priority, first_reply_min, resolve_min, updated_at FROM hd_sla_target")
    )
    return result.mappings().all()


async def policies(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT id, name, position, enabled, vip_only, channels, priorities, "
            "first_reply_min, resolve_min FROM hd_sla_policy ORDER BY position, created_at"
        )
    )
    return result.mappings().all()


async def calendar(session: AsyncSession) -> Row | None:
    result = await session.execute(
        text("SELECT timezone, days, opens, closes, always_open FROM hd_sla_calendar")
    )
    return result.mappings().first()


async def holidays(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text("SELECT day, name FROM hd_holiday ORDER BY day"))
    return result.mappings().all()


async def set_target(
    session: AsyncSession, workspace_id: UUID, priority: str, first_reply: int, resolve: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_sla_target (workspace_id, priority, first_reply_min, resolve_min) "
            "VALUES (:ws, :p, :f, :r) ON CONFLICT (workspace_id, priority) DO UPDATE "
            "SET first_reply_min = :f, resolve_min = :r, updated_at = now()"
        ),
        {"ws": workspace_id, "p": priority, "f": first_reply, "r": resolve},
    )


async def lock_settings(session: AsyncSession, workspace_id: UUID) -> None:
    """Settings changes and recalculations take turns per workspace."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('sla:' || CAST(:ws AS text), 0))"),
        {"ws": str(workspace_id)},
    )


# What the clocks read from tickets


async def facts_for_tickets(session: AsyncSession, ticket_ids: Sequence[UUID]) -> Sequence[Row]:
    result = await session.execute(
        text(_FACTS + " WHERE t.id = ANY(:ids)"), {"ids": list(ticket_ids)}
    )
    return result.mappings().all()


async def facts_for_contact(session: AsyncSession, contact_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(_FACTS + " WHERE t.contact_id = :c AND t.status <> 'resolved'"), {"c": contact_id}
    )
    return result.mappings().all()


async def facts_for_open(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_FACTS + " WHERE t.status <> 'resolved'"))
    return result.mappings().all()


async def set_due(
    session: AsyncSession, ids: Sequence[UUID], dues: Sequence[datetime | None]
) -> None:
    """Stores each ticket's due time. Leaves `updated_at` alone: this is derived data, and
    `updated_at` is the ticket's ETag."""
    if not ids:
        return
    await session.execute(
        text(
            "UPDATE hd_ticket t SET sla_due_at = d.due "
            "FROM unnest(CAST(:ids AS uuid[]), CAST(:dues AS timestamptz[])) AS d(id, due) "
            "WHERE t.id = d.id AND t.sla_due_at IS DISTINCT FROM d.due"
        ),
        {"ids": list(ids), "dues": list(dues)},
    )
