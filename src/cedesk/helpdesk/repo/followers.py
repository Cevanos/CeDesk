"""SQL for ticket followers (feature-gap plan 1.5). Run inside a `workspace_session`."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping


async def add(
    session: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    member_ids: Sequence[UUID],
    added_by: UUID | None,
) -> list[UUID]:
    """Adds whoever isn't following yet; returns those who were added."""
    if not member_ids:
        return []
    result = await session.execute(
        text(
            "INSERT INTO hd_ticket_follower (workspace_id, ticket_id, member_id, added_by) "
            "SELECT :ws, :ticket, m, :by FROM unnest(CAST(:ids AS uuid[])) AS m "
            "ON CONFLICT DO NOTHING RETURNING member_id"
        ),
        {"ws": workspace_id, "ticket": ticket_id, "ids": list(member_ids), "by": added_by},
    )
    return [row[0] for row in result.all()]


async def remove(session: AsyncSession, ticket_id: UUID, member_id: UUID) -> bool:
    result = await session.execute(
        text(
            "DELETE FROM hd_ticket_follower WHERE ticket_id = :ticket AND member_id = :m "
            "RETURNING member_id"
        ),
        {"ticket": ticket_id, "m": member_id},
    )
    return result.first() is not None


async def list_for(session: AsyncSession, ticket_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT f.member_id, m.name, m.email, m.is_active, f.added_by, "
            "b.name AS added_by_name, f.created_at "
            "FROM hd_ticket_follower f JOIN member m ON m.id = f.member_id "
            "LEFT JOIN member b ON b.id = f.added_by "
            "WHERE f.ticket_id = :ticket ORDER BY f.created_at, m.name"
        ),
        {"ticket": ticket_id},
    )
    return result.mappings().all()


async def active_followers(session: AsyncSession, ticket_id: UUID) -> list[UUID]:
    """Who to tell: followers whose membership is active."""
    result = await session.execute(
        text(
            "SELECT f.member_id FROM hd_ticket_follower f JOIN member m ON m.id = f.member_id "
            "WHERE f.ticket_id = :ticket AND m.is_active ORDER BY f.member_id"
        ),
        {"ticket": ticket_id},
    )
    return [row[0] for row in result.all()]


async def is_following(session: AsyncSession, ticket_id: UUID, member_id: UUID) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM hd_ticket_follower WHERE ticket_id = :ticket AND member_id = :m"),
        {"ticket": ticket_id, "m": member_id},
    )
    return result.first() is not None
