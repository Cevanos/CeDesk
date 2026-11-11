"""SQL for the SLA settings and each ticket's stored due time. Always inside a
`workspace_session`."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
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
    result = await session.execute(text("SELECT id, day, name FROM hd_holiday ORDER BY day"))
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


async def replace_policies(
    session: AsyncSession, workspace_id: UUID, policies: Sequence[Mapping[str, Any]]
) -> None:
    """The whole ordered list at once; an empty list means "the defaults" again."""
    await session.execute(text("DELETE FROM hd_sla_policy"))
    for position, p in enumerate(policies, 1):
        await session.execute(
            text(
                "INSERT INTO hd_sla_policy (workspace_id, name, position, enabled, vip_only, "
                "channels, priorities, first_reply_min, resolve_min) VALUES (:ws, :name, :pos, "
                ":enabled, :vip_only, :channels, :priorities, :first_reply, :resolve)"
            ),
            {"ws": workspace_id, "pos": position, **p},
        )


async def set_calendar(
    session: AsyncSession, workspace_id: UUID, values: Mapping[str, Any]
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_sla_calendar (workspace_id, timezone, days, opens, closes, "
            "always_open) VALUES (:ws, :timezone, :days, :opens, :closes, :always_open) "
            "ON CONFLICT (workspace_id) DO UPDATE SET timezone = :timezone, days = :days, "
            "opens = :opens, closes = :closes, always_open = :always_open, updated_at = now()"
        ),
        {"ws": workspace_id, **values},
    )


async def add_holiday(session: AsyncSession, workspace_id: UUID, day: date, name: str) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_holiday (workspace_id, day, name) VALUES (:ws, :day, :name) "
            "RETURNING id"
        ),
        {"ws": workspace_id, "day": day, "name": name},
    )
    return UUID(str(result.scalar_one()))


async def change_holiday(
    session: AsyncSession, holiday_id: UUID, day: date | None, name: str | None
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_holiday SET day = coalesce(CAST(:day AS date), day), "
            "name = coalesce(CAST(:name AS text), name) "
            "WHERE id = :id RETURNING id"
        ),
        {"id": holiday_id, "day": day, "name": name},
    )
    return result.first() is not None


async def remove_holiday(session: AsyncSession, holiday_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_holiday WHERE id = :id RETURNING id"), {"id": holiday_id}
    )
    return result.first() is not None


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
    `updated_at` is the ticket's ETag.

    Breach alerts (W14) are for a clock running out, not for a change: when a change (a
    settings save, a reopen, a new priority, an import) puts the due time in the past,
    that breach counts as alerted. The pill still shows it. A breach that time made and
    the worker hasn't alerted yet stays to be alerted."""
    if not ids:
        return
    await session.execute(
        text(
            "UPDATE hd_ticket t SET sla_due_at = d.due, sla_alerted_due = CASE "
            "WHEN d.due <= now() AND (t.sla_due_at IS NULL OR t.sla_due_at > now() "
            "     OR t.sla_alerted_due IS NOT DISTINCT FROM t.sla_due_at) THEN d.due "
            "ELSE t.sla_alerted_due END "
            "FROM unnest(CAST(:ids AS uuid[]), CAST(:dues AS timestamptz[])) AS d(id, due) "
            "WHERE t.id = d.id AND t.sla_due_at IS DISTINCT FROM d.due"
        ),
        {"ids": list(ids), "dues": list(dues)},
    )


# Breach alerts (W14)


async def newly_breached(session: AsyncSession, now: datetime, limit: int) -> Sequence[Row]:
    """Open tickets whose current clock ran out and haven't been alerted for it. Locked
    (SKIP LOCKED), so two workers never alert the same ticket."""
    result = await session.execute(
        text(
            "SELECT t.id, t.number, t.subject, t.sla_due_at, t.first_reply_at, t.assignee_id, "
            "t.team_id FROM hd_ticket t WHERE t.status <> 'resolved' "
            "AND t.merged_into_id IS NULL AND t.sla_due_at <= :now "
            "AND t.sla_alerted_due IS DISTINCT FROM t.sla_due_at "
            "ORDER BY t.sla_due_at LIMIT :limit FOR UPDATE OF t SKIP LOCKED"
        ),
        {"now": now, "limit": limit},
    )
    return result.mappings().all()


async def team_supervisors(session: AsyncSession, team_ids: Sequence[UUID]) -> Sequence[Row]:
    """Active Supervisors in each of these teams: (team_id, member_id)."""
    result = await session.execute(
        text(
            "SELECT tm.team_id, m.id AS member_id FROM hd_team_member tm "
            "JOIN member m ON m.id = tm.member_id "
            "WHERE tm.team_id = ANY(:teams) AND m.is_active AND m.role = 'supervisor'"
        ),
        {"teams": list(team_ids)},
    )
    return result.mappings().all()


async def active_members(session: AsyncSession, member_ids: Sequence[UUID]) -> set[UUID]:
    result = await session.execute(
        text("SELECT id FROM member WHERE id = ANY(:ids) AND is_active"), {"ids": list(member_ids)}
    )
    return {UUID(str(v)) for v in result.scalars().all()}


async def mark_alerted(session: AsyncSession, ticket_ids: Sequence[UUID]) -> None:
    """Leaves `updated_at` alone (an alert is not a change to the ticket)."""
    await session.execute(
        text("UPDATE hd_ticket SET sla_alerted_due = sla_due_at WHERE id = ANY(:ids)"),
        {"ids": list(ticket_ids)},
    )
