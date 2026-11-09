"""SQL for ticket attachments. Run inside a `workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping


async def ticket_exists(session: AsyncSession, ticket_id: UUID) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM hd_ticket WHERE id = :id"), {"id": ticket_id}
    )
    return result.first() is not None


async def insert(
    session: AsyncSession,
    *,
    attachment_id: UUID,
    workspace_id: UUID,
    ticket_id: UUID,
    object_key: str,
    name: str,
    size: int,
    mime: str,
    uploaded_by: UUID,
) -> Row:
    result = await session.execute(
        text(
            "INSERT INTO hd_attachment (id, workspace_id, ticket_id, object_key, name, size, "
            "mime, uploaded_by) VALUES (:id, :ws, :ticket, :key, :name, :size, :mime, :by) "
            "RETURNING id, ticket_id, message_id, name, size, mime, scan_status, uploaded_by, "
            "created_at, object_key"
        ),
        {
            "id": attachment_id,
            "ws": workspace_id,
            "ticket": ticket_id,
            "key": object_key,
            "name": name,
            "size": size,
            "mime": mime,
            "by": uploaded_by,
        },
    )
    return result.mappings().one()


async def get(session: AsyncSession, attachment_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, ticket_id, message_id, name, size, mime, scan_status, uploaded_by, "
            "created_at, object_key FROM hd_attachment WHERE id = :id"
        ),
        {"id": attachment_id},
    )
    return result.mappings().first()


async def for_ticket(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    before: tuple[datetime, UUID] | None = None,
    limit: int,
) -> Sequence[Row]:
    """Newest first, starting after `before` (the last row of the previous page)."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT id, ticket_id, message_id, name, size, mime, scan_status, uploaded_by, "
            "created_at, object_key FROM hd_attachment WHERE ticket_id = :ticket "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (created_at, id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        {"ticket": ticket_id, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()
