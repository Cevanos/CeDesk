"""SQL for the outbox. Always run inside a `workspace_session`, in the transaction of the
change the event describes."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def add(
    session: AsyncSession,
    workspace_id: UUID,
    event: str,
    ticket_id: UUID | None,
    payload: Mapping[str, Any],
) -> UUID:
    """Stores the event; its `id` and `occurred_at` go into the payload too (PL5)."""
    result = await session.execute(
        text(
            "WITH v AS (SELECT gen_random_uuid() AS id, clock_timestamp() AS at) "
            "INSERT INTO hd_outbox (id, workspace_id, event, ticket_id, payload, occurred_at) "
            "SELECT v.id, :ws, :event, :ticket_id, CAST(:payload AS jsonb) "
            "|| jsonb_build_object('id', CAST(v.id AS text), 'occurred_at', v.at), v.at "
            "FROM v RETURNING id"
        ),
        {
            "ws": workspace_id,
            "event": event,
            "ticket_id": ticket_id,
            "payload": json.dumps(payload),
        },
    )
    return UUID(str(result.scalar_one()))


async def for_ticket(session: AsyncSession, ticket_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT id, event, ticket_id, payload, occurred_at, published_at FROM hd_outbox "
            "WHERE ticket_id = :id ORDER BY seq"
        ),
        {"id": ticket_id},
    )
    return result.mappings().all()


# Delivery (the worker)


async def has_due(session: AsyncSession) -> bool:
    result = await session.execute(
        text(
            "SELECT 1 FROM hd_outbox WHERE published_at IS NULL AND failed_at IS NULL "
            "AND next_attempt_at <= now() LIMIT 1"
        )
    )
    return result.first() is not None


async def claim_due(session: AsyncSession, limit: int, lease_seconds: int) -> Sequence[RowMapping]:
    """Takes up to `limit` due events, oldest first, counts the attempt and pushes the next
    try `lease_seconds` ahead (so a crashed worker's events come back later).

    One ticket's events go in order: an event waits while an earlier one of its ticket is
    still to deliver (a failed one no longer holds the others back)."""
    result = await session.execute(
        text(
            "UPDATE hd_outbox o SET attempts = attempts + 1, "
            "next_attempt_at = now() + make_interval(secs => :lease) "
            "FROM (SELECT d.id FROM hd_outbox d "
            "      WHERE d.published_at IS NULL AND d.failed_at IS NULL "
            "      AND d.next_attempt_at <= now() "
            "      AND NOT EXISTS (SELECT 1 FROM hd_outbox p "
            "                      WHERE p.workspace_id = d.workspace_id "
            "                      AND p.ticket_id = d.ticket_id AND p.seq < d.seq "
            "                      AND p.published_at IS NULL AND p.failed_at IS NULL) "
            "      ORDER BY d.seq LIMIT :limit FOR UPDATE SKIP LOCKED) due "
            "WHERE o.id = due.id "
            "RETURNING o.id, o.workspace_id, o.seq, o.event, o.ticket_id, o.payload, "
            "o.occurred_at, o.attempts"
        ),
        {"limit": limit, "lease": lease_seconds},
    )
    return sorted(result.mappings().all(), key=lambda r: int(r["seq"]))


async def done_handlers(session: AsyncSession, event_ids: Sequence[UUID]) -> set[tuple[UUID, str]]:
    result = await session.execute(
        text("SELECT event_id, handler FROM hd_outbox_done WHERE event_id = ANY(:ids)"),
        {"ids": list(event_ids)},
    )
    return {(UUID(str(r[0])), str(r[1])) for r in result.all()}


async def mark_done(
    session: AsyncSession, workspace_id: UUID, event_id: UUID, handler: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_outbox_done (workspace_id, event_id, handler) "
            "VALUES (:ws, :id, :h) ON CONFLICT DO NOTHING"
        ),
        {"ws": workspace_id, "id": event_id, "h": handler},
    )


async def publish(session: AsyncSession, event_id: UUID) -> None:
    """Every handler is done (or none wanted it)."""
    await session.execute(
        text("UPDATE hd_outbox SET published_at = now(), last_error = NULL WHERE id = :id"),
        {"id": event_id},
    )


async def retry_later(session: AsyncSession, event_id: UUID, error: str, at: datetime) -> None:
    await session.execute(
        text("UPDATE hd_outbox SET last_error = :e, next_attempt_at = :at WHERE id = :id"),
        {"e": error, "at": at, "id": event_id},
    )


async def fail(session: AsyncSession, event_id: UUID, error: str) -> None:
    await session.execute(
        text("UPDATE hd_outbox SET last_error = :e, failed_at = now() WHERE id = :id"),
        {"e": error, "id": event_id},
    )


# The failed list (Admins)


async def failed(
    session: AsyncSession, *, before: tuple[datetime, UUID] | None, limit: int
) -> Sequence[RowMapping]:
    """Newest failure first."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT id, event, ticket_id, occurred_at, attempts, last_error, "
            "failed_at AS created_at FROM hd_outbox WHERE failed_at IS NOT NULL "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (failed_at, id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY failed_at DESC, id DESC LIMIT :limit"
        ),
        {"at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def retry_failed(session: AsyncSession, event_id: UUID) -> bool:
    """Back in the queue now, with its tries counted from zero. Handlers that already
    finished stay done."""
    result = await session.execute(
        text(
            "UPDATE hd_outbox SET failed_at = NULL, attempts = 0, next_attempt_at = now() "
            "WHERE id = :id AND failed_at IS NOT NULL RETURNING id"
        ),
        {"id": event_id},
    )
    return result.first() is not None
