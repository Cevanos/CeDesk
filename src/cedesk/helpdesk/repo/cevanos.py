"""SQL for the Cevanos connector. Always inside a `workspace_session` (forced RLS)."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo import stream as stream_repo

Row = RowMapping

_CONNECTION = (
    "SELECT c.workspace_id, c.api_key_sealed, c.api_key_hint, c.webhook_secret_sealed, "
    "c.close_on_resolve, c.team_id, t.name AS team_name, c.updated_at "
    "FROM hd_cevanos_connection c LEFT JOIN hd_team t "
    "ON t.workspace_id = c.workspace_id AND t.id = c.team_id"
)


async def connection(session: AsyncSession) -> Row | None:
    result = await session.execute(text(_CONNECTION))
    return result.mappings().first()


async def save_connection(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    api_key_sealed: bytes,
    api_key_hint: str,
    webhook_secret_sealed: bytes,
    close_on_resolve: bool,
    team_id: UUID | None,
    updated_by: UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_cevanos_connection (workspace_id, api_key_sealed, api_key_hint, "
            "webhook_secret_sealed, close_on_resolve, team_id, updated_by) "
            "VALUES (:ws, :key, :hint, :secret, :close, :team, :by) "
            "ON CONFLICT (workspace_id) DO UPDATE SET api_key_sealed = EXCLUDED.api_key_sealed, "
            "api_key_hint = EXCLUDED.api_key_hint, "
            "webhook_secret_sealed = EXCLUDED.webhook_secret_sealed, "
            "close_on_resolve = EXCLUDED.close_on_resolve, team_id = EXCLUDED.team_id, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {
            "ws": workspace_id,
            "key": api_key_sealed,
            "hint": api_key_hint,
            "secret": webhook_secret_sealed,
            "close": close_on_resolve,
            "team": team_id,
            "by": updated_by,
        },
    )


async def delete_connection(session: AsyncSession) -> bool:
    result = await session.execute(text("DELETE FROM hd_cevanos_connection RETURNING 1"))
    return result.first() is not None


async def team_exists(session: AsyncSession, team_id: UUID) -> bool:
    result = await session.execute(text("SELECT 1 FROM hd_team WHERE id = :id"), {"id": team_id})
    return result.first() is not None


# Chats


async def chat_by_conversation(session: AsyncSession, conversation_id: str) -> Row | None:
    result = await session.execute(
        text("SELECT * FROM hd_cevanos_chat WHERE conversation_id = :c"), {"c": conversation_id}
    )
    return result.mappings().first()


async def chat_by_ticket(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(
        text("SELECT * FROM hd_cevanos_chat WHERE ticket_id = :t"), {"t": ticket_id}
    )
    return result.mappings().first()


async def ticket_by_source(session: AsyncSession, conversation_id: str) -> UUID | None:
    result = await session.execute(
        text("SELECT id FROM hd_ticket WHERE source = 'cevanos' AND source_id = :c"),
        {"c": conversation_id},
    )
    found = result.scalar_one_or_none()
    return None if found is None else UUID(str(found))


async def add_chat(
    session: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    *,
    conversation_id: str,
    agent_id: str | None,
    channel_kind: str | None,
    last_message_id: str | None,
    handover_event_id: str | None,
) -> None:
    """Idempotent: a repeated handover keeps the row it has."""
    await session.execute(
        text(
            "INSERT INTO hd_cevanos_chat (workspace_id, ticket_id, conversation_id, agent_id, "
            "channel_kind, last_message_id, handover_event_id) "
            "VALUES (:ws, :t, :c, :agent, :kind, :last, :event) "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "ws": workspace_id,
            "t": ticket_id,
            "c": conversation_id,
            "agent": agent_id,
            "kind": channel_kind,
            "last": last_message_id,
            "event": handover_event_id,
        },
    )


async def lock_chat(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(
        text("SELECT * FROM hd_cevanos_chat WHERE ticket_id = :t FOR UPDATE"), {"t": ticket_id}
    )
    return result.mappings().first()


async def update_chat(session: AsyncSession, ticket_id: UUID, **values: Any) -> None:
    allowed = {
        "last_message_id", "next_poll_at", "poll_failures", "taken_over_at", "closed_at",
        "handover_event_id",
    }  # fmt: skip
    if not values or not set(values) <= allowed:
        raise ValueError(f"Not chat columns: {sorted(set(values) - allowed)}")
    sets = ", ".join(f"{k} = :{k}" for k in sorted(values))
    await session.execute(
        text(f"UPDATE hd_cevanos_chat SET {sets} WHERE ticket_id = :ticket_id"),  # noqa: S608
        {**values, "ticket_id": ticket_id},
    )
    # The state of the chat, for the live streams (P7): taken over, closed, or reopened.
    state = None
    if values.get("taken_over_at") is not None:
        state = "chat.taken_over"
    elif "closed_at" in values:
        state = "chat.reopened" if values["closed_at"] is None else "chat.closed"
    if state is not None:
        await stream_repo.publish_for_ticket(
            session, ticket_id, agent=(state, {"ticket_id": str(ticket_id)})
        )


async def claim_due(session: AsyncSession, limit: int, lease_seconds: int) -> Sequence[Row]:
    """Open chats whose next read is due, longest-waiting first. Each is pushed
    `lease_seconds` ahead, so two workers don't read the same chat at once."""
    result = await session.execute(
        text(
            "UPDATE hd_cevanos_chat c SET next_poll_at = now() + make_interval(secs => :lease) "
            "FROM (SELECT ticket_id FROM hd_cevanos_chat "
            "      WHERE closed_at IS NULL AND next_poll_at <= now() "
            "      ORDER BY next_poll_at LIMIT :limit FOR UPDATE SKIP LOCKED) due "
            "WHERE c.ticket_id = due.ticket_id "
            "RETURNING c.ticket_id, c.conversation_id, c.last_message_id, c.poll_failures"
        ),
        {"limit": limit, "lease": lease_seconds},
    )
    return result.mappings().all()


async def reopen_for_handover(session: AsyncSession, ticket_id: UUID, event_id: str) -> None:
    """A chat handed over again: the ticket waits for Accept once more and is open."""
    await session.execute(
        text(
            "UPDATE hd_ticket SET accepted = false, "
            "status = CASE WHEN status = 'resolved' THEN 'open' ELSE status END, "
            "reopen_count = reopen_count + CASE WHEN status = 'resolved' THEN 1 ELSE 0 END, "
            "resolved_at = CASE WHEN status = 'resolved' THEN NULL ELSE resolved_at END, "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": ticket_id},
    )
    await session.execute(
        text(
            "UPDATE hd_cevanos_chat SET taken_over_at = NULL, closed_at = NULL, "
            "next_poll_at = now(), poll_failures = 0, handover_event_id = :event "
            "WHERE ticket_id = :id"
        ),
        {"id": ticket_id, "event": event_id},
    )


async def set_sentiment(session: AsyncSession, ticket_id: UUID, sentiment: str) -> None:
    await session.execute(
        text(
            "UPDATE hd_ticket SET sentiment = :s, sentiment_by_cevanos = true, "
            "updated_at = now() WHERE id = :id"
        ),
        {"s": sentiment, "id": ticket_id},
    )


async def message(session: AsyncSession, message_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT m.id, m.kind, m.body, m.author_name, m.ticket_id FROM hd_message m "
            "WHERE m.id = :id"
        ),
        {"id": message_id},
    )
    return result.mappings().first()
