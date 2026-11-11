"""SQL for CSAT ratings and emailed survey links. RLS applies."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def survey_on(session: AsyncSession) -> bool:
    result = await session.execute(text("SELECT csat_survey FROM hd_portal_settings"))
    value = result.scalar_one_or_none()
    return True if value is None else bool(value)


async def link_made(session: AsyncSession, effect_key: str) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM hd_csat_link WHERE effect_key = :key"), {"key": effect_key}
    )
    return result.first() is not None


async def insert_link(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    ticket_id: UUID,
    token_hash: bytes,
    effect_key: str,
    expires_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_csat_link (workspace_id, ticket_id, token_hash, effect_key, "
            "expires_at) VALUES (:ws, :ticket, :hash, :key, :expires)"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "hash": token_hash,
            "key": effect_key,
            "expires": expires_at,
        },
    )


async def link_ticket(session: AsyncSession, token_hash: bytes) -> RowMapping | None:
    """The ticket behind a live survey link."""
    result = await session.execute(
        text(
            "SELECT t.id, t.number, t.subject, t.status, t.contact_id "
            "FROM hd_csat_link l JOIN hd_ticket t ON t.id = l.ticket_id "
            "WHERE l.token_hash = :hash AND l.expires_at > now()"
        ),
        {"hash": token_hash},
    )
    return result.mappings().first()


async def lock_ticket(session: AsyncSession, ticket_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT id, number, subject, status, contact_id, merged_into_id "
            "FROM hd_ticket WHERE id = :id FOR UPDATE"
        ),
        {"id": ticket_id},
    )
    return result.mappings().first()


async def save(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    ticket_id: UUID,
    contact_id: UUID,
    score: int,
    comment: str | None,
    source: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, comment, source) "
            "VALUES (:ws, :ticket, :contact, :score, :comment, :source) "
            "ON CONFLICT (workspace_id, ticket_id) DO UPDATE SET score = EXCLUDED.score, "
            "comment = EXCLUDED.comment, source = EXCLUDED.source, rated_at = now()"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "contact": contact_id,
            "score": score,
            "comment": comment,
            "source": source,
        },
    )


async def for_ticket(session: AsyncSession, ticket_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text("SELECT score, comment, source, rated_at FROM hd_csat WHERE ticket_id = :id"),
        {"id": ticket_id},
    )
    return result.mappings().first()
