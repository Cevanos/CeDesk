"""SQL for ticket attachments. Run inside a `workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

# A message's files, for the message queries (repo.tickets, repo.messages): `m` is the
# hd_message row.
MESSAGE_FILES = (
    "coalesce((SELECT json_agg(json_build_object('id', f.id, 'name', f.name, "
    "'size', f.size, 'mime', f.mime, 'scan_status', f.scan_status) "
    "ORDER BY f.created_at, f.id) FROM hd_attachment f "
    "WHERE f.workspace_id = m.workspace_id AND f.message_id = m.id), '[]') AS attachments"
)


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
    uploaded_by: UUID | None,
    message_id: UUID | None = None,
) -> Row:
    result = await session.execute(
        text(
            "INSERT INTO hd_attachment (id, workspace_id, ticket_id, object_key, name, size, "
            "mime, uploaded_by, message_id) "
            "VALUES (:id, :ws, :ticket, :key, :name, :size, :mime, :by, :msg) "
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
            "msg": message_id,
        },
    )
    return result.mappings().one()


async def lock_many(session: AsyncSession, attachment_ids: Sequence[UUID]) -> Sequence[Row]:
    """Locked, so two messages can't both take the same file."""
    result = await session.execute(
        text(
            "SELECT id, ticket_id, message_id, name, size, mime, scan_status, uploaded_by, "
            "created_at, object_key FROM hd_attachment WHERE id = ANY(:ids) "
            "ORDER BY id FOR UPDATE"
        ),
        {"ids": list(attachment_ids)},
    )
    return result.mappings().all()


async def link_to_message(
    session: AsyncSession, attachment_ids: Sequence[UUID], message_id: UUID
) -> None:
    await session.execute(
        text("UPDATE hd_attachment SET message_id = :msg, updated_at = now() WHERE id = ANY(:ids)"),
        {"msg": message_id, "ids": list(attachment_ids)},
    )


async def for_messages(session: AsyncSession, message_ids: Sequence[UUID]) -> Sequence[Row]:
    """The files of these messages, oldest first (the order they were added)."""
    result = await session.execute(
        text(
            "SELECT id, ticket_id, message_id, name, size, mime, scan_status, uploaded_by, "
            "created_at, object_key FROM hd_attachment WHERE message_id = ANY(:ids) "
            "ORDER BY created_at, id"
        ),
        {"ids": list(message_ids)},
    )
    return result.mappings().all()


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


# Virus scan (the worker)


async def to_scan(session: AsyncSession, limit: int) -> Sequence[Row]:
    """Files not checked yet, oldest first."""
    result = await session.execute(
        text(
            "SELECT id, ticket_id, message_id, name, size, object_key FROM hd_attachment "
            "WHERE scan_status = 'not_scanned' ORDER BY created_at, id LIMIT :limit"
        ),
        {"limit": limit},
    )
    return result.mappings().all()


async def set_scan(
    session: AsyncSession, attachment_id: UUID, status: str, result: str | None
) -> bool:
    """Records the verdict once: False when another worker got there first."""
    done = await session.execute(
        text(
            "UPDATE hd_attachment SET scan_status = :s, scan_result = :r, scanned_at = now(), "
            "updated_at = now() WHERE id = :id AND scan_status = 'not_scanned' RETURNING id"
        ),
        {"s": status, "r": result, "id": attachment_id},
    )
    return done.first() is not None
