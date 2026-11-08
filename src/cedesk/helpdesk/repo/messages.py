"""SQL for mentions, notifications and a ticket's reply clock. Run inside a `workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping


async def add_mentions(
    session: AsyncSession, workspace_id: UUID, message_id: UUID, member_ids: Sequence[UUID]
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_mention (workspace_id, message_id, member_id) "
            "SELECT :ws, :msg, unnest(CAST(:ids AS uuid[]))"
        ),
        {"ws": workspace_id, "msg": message_id, "ids": list(member_ids)},
    )


async def ticket_replied(session: AsyncSession, ticket_id: UUID) -> None:
    """The first agent reply starts `first_reply_at`; the status is left alone (T17)."""
    await session.execute(
        text(
            "UPDATE hd_ticket SET first_reply_at = coalesce(first_reply_at, now()), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": ticket_id},
    )


async def ticket_touched(session: AsyncSession, ticket_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_ticket SET updated_at = now() WHERE id = :id"), {"id": ticket_id}
    )


async def notify(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    member_ids: Sequence[UUID],
    ticket_id: UUID,
    kind: str,
    body: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_notification (workspace_id, member_id, ticket_id, kind, text) "
            "SELECT :ws, unnest(CAST(:ids AS uuid[])), :ticket, :kind, :text"
        ),
        {
            "ws": workspace_id,
            "ids": list(member_ids),
            "ticket": ticket_id,
            "kind": kind,
            "text": body,
        },
    )


async def get_message(session: AsyncSession, message_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT m.id, m.kind, m.body, m.author_member_id, m.author_name, m.created_at, "
            "coalesce((SELECT array_agg(x.member_id ORDER BY x.created_at, x.member_id) "
            "FROM hd_mention x WHERE x.message_id = m.id), '{}') AS mention_ids "
            "FROM hd_message m WHERE m.id = :id"
        ),
        {"id": message_id},
    )
    return result.mappings().first()


async def notifications(
    session: AsyncSession,
    member_id: UUID,
    *,
    unread_only: bool,
    before: tuple[datetime, UUID] | None = None,
    limit: int,
) -> Sequence[Row]:
    """Newest first, starting after `before` (the last row of the previous page)."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT n.id, n.kind, n.text, n.ticket_id, t.number AS ticket_number, "
            "n.read_at, n.created_at "
            "FROM hd_notification n LEFT JOIN hd_ticket t ON t.id = n.ticket_id "
            "WHERE n.member_id = :me AND (NOT :unread OR n.read_at IS NULL) "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (n.created_at, n.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY n.created_at DESC, n.id DESC LIMIT :limit"
        ),
        {"me": member_id, "unread": unread_only, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def unread_count(session: AsyncSession, member_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_notification WHERE member_id = :me AND read_at IS NULL"),
        {"me": member_id},
    )
    return int(result.scalar_one())


async def mark_read(session: AsyncSession, member_id: UUID, notification_id: UUID) -> bool:
    """Only the member's own notification; someone else's id is 'not found'."""
    result = await session.execute(
        text(
            "UPDATE hd_notification SET read_at = coalesce(read_at, now()) "
            "WHERE id = :id AND member_id = :me RETURNING id"
        ),
        {"id": notification_id, "me": member_id},
    )
    return result.first() is not None


async def mark_all_read(session: AsyncSession, member_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_notification SET read_at = now() WHERE member_id = :me AND read_at IS NULL"
        ),
        {"me": member_id},
    )
