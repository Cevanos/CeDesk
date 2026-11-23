"""SQL for outgoing webhooks (md PL3-PL6). RLS applies."""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

_HOOK = (
    "w.id, w.url, w.events, w.secret_hint, w.enabled, w.created_at, w.updated_at, "
    "w.created_by, m.name AS created_by_name"
)
_HOOK_FROM = "FROM hd_webhook w LEFT JOIN member m ON m.id = w.created_by "


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    webhook_id: UUID,
    url: str,
    events: list[str],
    secret_sealed: bytes,
    secret_hint: str,
    enabled: bool,
    created_by: UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_webhook (id, workspace_id, url, events, secret_sealed, "
            "secret_hint, enabled, created_by) VALUES (:id, :ws, :url, :events, :sealed, "
            ":hint, :enabled, :by)"
        ),
        {
            "id": webhook_id,
            "ws": workspace_id,
            "url": url,
            "events": events,
            "sealed": secret_sealed,
            "hint": secret_hint,
            "enabled": enabled,
            "by": created_by,
        },
    )


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_webhook"))
    return int(result.scalar_one())


async def get(session: AsyncSession, webhook_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(f"SELECT {_HOOK} {_HOOK_FROM} WHERE w.id = :id"),
        {"id": webhook_id},
    )
    return result.mappings().first()


async def all_hooks(session: AsyncSession) -> Sequence[RowMapping]:
    result = await session.execute(text(f"SELECT {_HOOK} {_HOOK_FROM} ORDER BY w.created_at, w.id"))
    return result.mappings().all()


async def update(session: AsyncSession, webhook_id: UUID, values: dict[str, Any]) -> None:
    """`values`: any of url, events, enabled (names from the code, never from a request)."""
    allowed = {"url", "events", "enabled"}
    assert set(values) <= allowed
    sets = ", ".join(f"{k} = :{k}" for k in values)
    await session.execute(
        text(f"UPDATE hd_webhook SET {sets}, updated_at = now() WHERE id = :id"),  # noqa: S608
        values | {"id": webhook_id},
    )


async def set_secret(session: AsyncSession, webhook_id: UUID, sealed: bytes, hint: str) -> None:
    await session.execute(
        text(
            "UPDATE hd_webhook SET secret_sealed = :sealed, secret_hint = :hint, "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": webhook_id, "sealed": sealed, "hint": hint},
    )


async def delete(session: AsyncSession, webhook_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_webhook WHERE id = :id RETURNING id"), {"id": webhook_id}
    )
    return result.first() is not None


async def sealed_secret(session: AsyncSession, webhook_id: UUID) -> bytes:
    result = await session.execute(
        text("SELECT secret_sealed FROM hd_webhook WHERE id = :id"), {"id": webhook_id}
    )
    return bytes(result.scalar_one())


async def add_ping(
    session: AsyncSession,
    workspace_id: UUID,
    delivery_id: UUID,
    webhook_id: UUID,
    body: dict[str, Any],
) -> None:
    """The test ping's delivery: no outbox event behind it."""
    await session.execute(
        text(
            "INSERT INTO hd_webhook_delivery (id, workspace_id, webhook_id, event, body) "
            "VALUES (:id, :ws, :hook, 'ping', CAST(:body AS jsonb))"
        ),
        {"id": delivery_id, "ws": workspace_id, "hook": webhook_id, "body": json.dumps(body)},
    )


async def listening(session: AsyncSession, event: str) -> Sequence[RowMapping]:
    """Turned-on webhooks that listen to this event."""
    result = await session.execute(
        text("SELECT id, events FROM hd_webhook WHERE enabled AND :event = ANY(events)"),
        {"event": event},
    )
    return result.mappings().all()


async def add_delivery(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    webhook_id: UUID,
    event_id: UUID | None,
    event: str,
    body: dict[str, Any],
) -> UUID | None:
    """One delivery per (webhook, event): a second hand-out of the same event adds none."""
    result = await session.execute(
        text(
            "INSERT INTO hd_webhook_delivery (workspace_id, webhook_id, event_id, event, body) "
            "VALUES (:ws, :hook, :event_id, :event, CAST(:body AS jsonb)) "
            "ON CONFLICT (webhook_id, event_id) DO NOTHING RETURNING id"
        ),
        {
            "ws": workspace_id,
            "hook": webhook_id,
            "event_id": event_id,
            "event": event,
            "body": json.dumps(body),
        },
    )
    found = result.scalar_one_or_none()
    return UUID(str(found)) if found is not None else None


_DELIVERY = (
    "d.id, d.webhook_id, w.url, d.event, d.event_id, d.body, d.state, d.tries, d.next_try_at, "
    "d.last_status, d.last_ms, d.last_error, d.delivered_at, d.created_at"
)


async def claim_due(session: AsyncSession, limit: int, lease_seconds: int) -> Sequence[RowMapping]:
    """Pending deliveries whose time has come and that no other worker holds, held for
    `lease_seconds` (a worker that dies lets them go). With the webhook's secret. Every
    delivery time is the database's clock, so the app server's clock never matters."""
    result = await session.execute(
        text(
            "WITH due AS (SELECT d.id FROM hd_webhook_delivery d "
            "  WHERE d.state = 'pending' AND d.next_try_at <= now() "
            "  AND (d.lease_until IS NULL OR d.lease_until < now()) "
            "  ORDER BY d.next_try_at, d.created_at LIMIT :limit FOR UPDATE SKIP LOCKED) "
            "UPDATE hd_webhook_delivery d SET lease_until = now() + make_interval(secs => :lease) "
            "FROM due, hd_webhook w WHERE d.id = due.id AND w.id = d.webhook_id "
            "RETURNING d.id, d.webhook_id, d.event, d.body, d.tries, w.url, w.enabled, "
            "w.secret_sealed"
        ),
        {"limit": limit, "lease": lease_seconds},
    )
    return result.mappings().all()


async def record_try(
    session: AsyncSession,
    workspace_id: UUID,
    delivery_id: UUID,
    *,
    status: int | None,
    ms: int,
    error: str | None,
    response: str | None,
    state: str,
    wait_seconds: int | None,
) -> None:
    """One try: logged, and the delivery moved on (delivered, retried `wait_seconds` from
    now by the database's clock, or failed)."""
    await session.execute(
        text(
            "INSERT INTO hd_webhook_attempt (workspace_id, delivery_id, status, ms, error, "
            "response) VALUES (:ws, :id, :status, :ms, :error, :response)"
        ),
        {
            "ws": workspace_id,
            "id": delivery_id,
            "status": status,
            "ms": ms,
            "error": error,
            "response": response,
        },
    )
    await session.execute(
        text(
            "UPDATE hd_webhook_delivery SET tries = tries + 1, state = :state, "
            "lease_until = NULL, last_status = :status, last_ms = :ms, last_error = :error, "
            "next_try_at = CASE WHEN CAST(:wait AS integer) IS NULL THEN next_try_at "
            "  ELSE now() + make_interval(secs => CAST(:wait AS integer)) END, "
            "delivered_at = CASE WHEN :state = 'delivered' THEN now() END WHERE id = :id"
        ),
        {
            "id": delivery_id,
            "state": state,
            "status": status,
            "ms": ms,
            "error": error,
            "wait": wait_seconds,
        },
    )


async def deliveries(
    session: AsyncSession,
    *,
    state: str | None,
    webhook_id: UUID | None,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Newest first."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            f"SELECT {_DELIVERY} FROM hd_webhook_delivery d "  # noqa: S608 - fixed strings
            "JOIN hd_webhook w ON w.id = d.webhook_id "
            "WHERE (CAST(:state AS text) IS NULL OR d.state = :state) "
            "AND (CAST(:hook AS uuid) IS NULL OR d.webhook_id = CAST(:hook AS uuid)) "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (d.created_at, d.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY d.created_at DESC, d.id DESC LIMIT :limit"
        ),
        {"state": state, "hook": webhook_id, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def attempts(session: AsyncSession, delivery_ids: Sequence[UUID]) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT delivery_id, at, status, ms, error, response FROM hd_webhook_attempt "
            "WHERE delivery_id = ANY(CAST(:ids AS uuid[])) ORDER BY at"
        ),
        {"ids": list(delivery_ids)},
    )
    return result.mappings().all()


async def failed_hook_enabled(session: AsyncSession, delivery_id: UUID) -> bool | None:
    """Whether a failed delivery's webhook is on; None when there is no failed delivery
    with that id. Locks the delivery, so a retry decided on it is the only one."""
    result = await session.execute(
        text(
            "SELECT w.enabled FROM hd_webhook_delivery d JOIN hd_webhook w "
            "ON w.id = d.webhook_id WHERE d.id = :id AND d.state = 'failed' FOR UPDATE OF d"
        ),
        {"id": delivery_id},
    )
    found = result.scalar_one_or_none()
    return None if found is None else bool(found)


async def retry(session: AsyncSession, delivery_id: UUID) -> bool:
    """A failed delivery back in the queue now, with its tries counted from zero."""
    result = await session.execute(
        text(
            "UPDATE hd_webhook_delivery SET state = 'pending', tries = 0, next_try_at = now(), "
            "lease_until = NULL WHERE id = :id AND state = 'failed' RETURNING id"
        ),
        {"id": delivery_id},
    )
    return result.first() is not None


async def prune(session: AsyncSession, days: int) -> int:
    """Deletes delivered and failed deliveries older than `days` (their tries go with them):
    the log is for recent trouble, and each body names a ticket."""
    result = await session.execute(
        text(
            "DELETE FROM hd_webhook_delivery WHERE state <> 'pending' "
            "AND created_at < now() - make_interval(days => :days)"
        ),
        {"days": days},
    )
    return int(getattr(result, "rowcount", 0) or 0)
