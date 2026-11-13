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
    slack: tuple[str, str] | None = None,
) -> UUID:
    """`slack`: (channel id, ts of the thread's first message) for one in Slack."""
    result = await session.execute(
        text(
            "INSERT INTO hd_side_conversation (workspace_id, ticket_id, subject, to_addresses, "
            "from_address, created_by, via, slack_channel, slack_ts) VALUES (:ws, :ticket, "
            ":subject, :to, :from_address, :by, :via, :channel, :ts) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "subject": subject,
            "to": to,
            "from_address": from_address,
            "by": by,
            "via": "slack" if slack else "email",
            "channel": slack[0] if slack else None,
            "ts": slack[1] if slack else None,
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
            "SELECT id, ticket_id, subject, to_addresses, from_address, created_at, via, "
            "slack_channel, slack_ts FROM hd_side_conversation WHERE id = :id"
        ),
        {"id": side_id},
    )
    return result.mappings().first()


async def for_ticket(session: AsyncSession, ticket_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT s.id, s.subject, s.via, s.to_addresses, s.from_address, s.created_at, "
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


async def name_message(session: AsyncSession, message_id: UUID, *, author_name: str,
                       body: str) -> None:  # fmt: skip
    """A Slack reply saved under the person's Slack id gets their name (and its mentions)."""
    await session.execute(
        text("UPDATE hd_side_message SET author_name = :name, body = :body WHERE id = :id"),
        {"id": message_id, "name": author_name[:120], "body": body},
    )


async def rename_line(session: AsyncSession, line_id: UUID, body: str) -> None:
    """The ticket's system line for a Slack reply, once the person's name is known."""
    await session.execute(
        text("UPDATE hd_message SET body = :body WHERE id = :id AND kind = 'system'"),
        {"id": line_id, "body": body},
    )


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
    """The email side conversation one of these Message-IDs belongs to (the first listed);
    never a Slack one, whatever an email's headers say."""
    if not ids:
        return None
    result = await session.execute(
        text(
            "SELECT s.id, s.ticket_id FROM hd_side_message m "
            "JOIN hd_side_conversation s ON s.id = m.side_id "
            "WHERE m.header_message_id = ANY(:ids) AND s.via = 'email' "
            "ORDER BY array_position(:ids, m.header_message_id) LIMIT 1"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().first()


async def by_slack_thread(session: AsyncSession, channel: str, ts: str) -> RowMapping | None:
    """The Slack side conversation whose thread starts at this message."""
    result = await session.execute(
        text(
            "SELECT id, ticket_id, to_addresses FROM hd_side_conversation "
            "WHERE via = 'slack' AND slack_channel = :channel AND slack_ts = :ts"
        ),
        {"channel": channel, "ts": ts},
    )
    return result.mappings().first()
