"""SQL for `Idempotency-Key`s. Always inside the `workspace_session` of the change."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def claim(
    session: AsyncSession,
    workspace_id: UUID,
    member_id: UUID,
    key: str,
    fingerprint: bytes,
    lifetime: timedelta,
) -> RowMapping | None:
    """Takes the key for this transaction and returns None, or returns the earlier use.

    A request with the same key that is still running holds its row (not committed), so
    this waits for it to finish: a parallel repeat sees the result instead of making a
    second one. A key older than `lifetime` is taken over as new."""
    taken = await session.execute(
        text(
            "INSERT INTO hd_idempotency (workspace_id, member_id, key, fingerprint) "
            "VALUES (:ws, :m, :key, :fp) "
            "ON CONFLICT (workspace_id, member_id, key) DO UPDATE "
            "SET fingerprint = EXCLUDED.fingerprint, resource_id = NULL, created_at = now() "
            "WHERE hd_idempotency.created_at < now() - make_interval(secs => :life) "
            "RETURNING key"
        ),
        {
            "ws": workspace_id,
            "m": member_id,
            "key": key,
            "fp": fingerprint,
            "life": lifetime.total_seconds(),
        },
    )
    if taken.first() is not None:
        return None
    earlier = await session.execute(
        text(
            "SELECT fingerprint, resource_id, created_at FROM hd_idempotency "
            "WHERE member_id = :m AND key = :key"
        ),
        {"m": member_id, "key": key},
    )
    return earlier.mappings().one()


async def remember(session: AsyncSession, member_id: UUID, key: str, resource_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_idempotency SET resource_id = :r WHERE member_id = :m AND key = :key"),
        {"r": resource_id, "m": member_id, "key": key},
    )


async def sweep(session: AsyncSession, lifetime: timedelta) -> int:
    result = await session.execute(
        text("DELETE FROM hd_idempotency WHERE created_at < now() - make_interval(secs => :life)"),
        {"life": lifetime.total_seconds()},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
