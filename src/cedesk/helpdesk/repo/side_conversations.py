"""SQL for side conversations (md X5). RLS applies."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def create(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID,
    subject: str,
    to: list[str],
    from_address: str,
    by: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_side_conversation (workspace_id, ticket_id, subject, to_addresses, "
            "from_address, created_by) VALUES (:ws, :ticket, :subject, :to, :from_address, "
            ":by) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "subject": subject,
            "to": to,
            "from_address": from_address,
            "by": by,
        },
    )
    return UUID(str(result.scalar_one()))


async def count_for_ticket(session: AsyncSession, ticket_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_side_conversation WHERE ticket_id = :t"), {"t": ticket_id}
    )
    return int(result.scalar_one())


async def get(session: AsyncSession, side_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT id, ticket_id, subject, to_addresses, from_address, created_at "
            "FROM hd_side_conversation WHERE id = :id"
        ),
        {"id": side_id},
    )
    return result.mappings().first()


async def for_ticket(session: AsyncSession, ticket_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT s.id, s.subject, s.to_addresses, s.from_address, s.created_at, "
            "s.last_message_at, m.name AS created_by_name FROM hd_side_conversation s "
            "LEFT JOIN member m ON m.id = s.created_by WHERE s.ticket_id = :t "
            "ORDER BY s.created_at, s.id"
        ),
        {"t": ticket_id},
    )
    return result.mappings().all()


async def add_message(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    side_id: UUID,
    direction: str,
    body: str,
    author_member_id: UUID | None,
    author_name: str,
    from_address: str,
    header_message_id: str,
) -> UUID | None:
    """None when this email (its Message-ID) is already on a side conversation."""
    result = await session.execute(
        text(
            "INSERT INTO hd_side_message (workspace_id, side_id, direction, body, "
            "author_member_id, author_name, from_address, header_message_id) VALUES (:ws, "
            ":side, :direction, :body, :author, :name, :from_address, :mid) "
            "ON CONFLICT (workspace_id, header_message_id) DO NOTHING RETURNING id"
        ),
        {
            "ws": workspace_id,
            "side": side_id,
            "direction": direction,
            "body": body,
            "author": author_member_id,
            "name": author_name[:120],
            "from_address": from_address,
            "mid": header_message_id,
        },
    )
    found = result.scalar_one_or_none()
    if found is not None:
        await session.execute(
            text("UPDATE hd_side_conversation SET last_message_at = now() WHERE id = :id"),
            {"id": side_id},
        )
    return UUID(str(found)) if found is not None else None


async def messages(session: AsyncSession, side_ids: Sequence[UUID]) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT id, side_id, direction, body, author_name, from_address, "
            "header_message_id, created_at FROM hd_side_message "
            "WHERE side_id = ANY(CAST(:ids AS uuid[])) ORDER BY created_at, id"
        ),
        {"ids": list(side_ids)},
    )
    return result.mappings().all()


async def by_email_ids(session: AsyncSession, ids: Sequence[str]) -> RowMapping | None:
    """The side conversation one of these Message-IDs belongs to (the first listed)."""
    if not ids:
        return None
    result = await session.execute(
        text(
            "SELECT s.id, s.ticket_id FROM hd_side_message m "
            "JOIN hd_side_conversation s ON s.id = m.side_id "
            "WHERE m.header_message_id = ANY(:ids) "
            "ORDER BY array_position(:ids, m.header_message_id) LIMIT 1"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().first()
