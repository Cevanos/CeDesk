"""Replays of a rule or workflow on past tickets (differentiator plan #3). Every query
runs in a workspace session (RLS)."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_SELECT = (
    "SELECT "
    "r.id, r.kind, r.target_id, r.target_version, r.name, r.draft, r.ticket_limit, r.state, "
    "r.checked, r.matched, r.would_change, r.would_wait, r.conflicts, r.samples, "
    "r.conflict_samples, r.cursor_at, r.cursor_id, r.error, r.created_at, r.started_at, "
    "r.finished_at, m.name AS requested_by_name"
    " FROM hd_rule_replay r LEFT JOIN member m ON m.id = r.requested_by "
)


async def active(session: AsyncSession) -> UUID | None:
    result = await session.execute(
        text("SELECT id FROM hd_rule_replay WHERE state IN ('queued', 'running') LIMIT 1")
    )
    found = result.scalar_one_or_none()
    return None if found is None else UUID(str(found))


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    kind: str,
    target_id: UUID | None,
    target_version: int | None,
    name: str,
    draft: Mapping[str, Any] | None,
    ticket_limit: int,
    requested_by: UUID | None,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_rule_replay (workspace_id, kind, target_id, target_version, name, "
            "draft, ticket_limit, requested_by) VALUES (:ws, :kind, :target, :version, :name, "
            "CAST(:draft AS jsonb), :limit, :by) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "kind": kind,
            "target": target_id,
            "version": target_version,
            "name": name,
            "draft": None if draft is None else json.dumps(draft),
            "limit": ticket_limit,
            "by": requested_by,
        },
    )
    return UUID(str(result.scalar_one()))


async def get(session: AsyncSession, replay_id: UUID) -> Row | None:
    result = await session.execute(
        text(_SELECT + "WHERE r.id = :id"),
        {"id": replay_id},
    )
    return result.mappings().first()


async def cancel(session: AsyncSession, replay_id: UUID) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_rule_replay SET state = 'cancelled', finished_at = now(), "
            "lease_until = NULL WHERE id = :id AND state IN ('queued', 'running') RETURNING id"
        ),
        {"id": replay_id},
    )
    return result.first() is not None


async def claim(session: AsyncSession, lease_seconds: int) -> Row | None:
    """The workspace's replay to work on now: queued, or running and not held (the last
    pass let it go, or its worker died and the lease ran out). Locked; leased to this
    worker. None: nothing to do, or another worker
    has it."""
    result = await session.execute(
        text(
            _SELECT + "WHERE r.state = 'queued' OR (r.state = 'running' "
            "AND (r.lease_until IS NULL OR r.lease_until < now())) "
            "ORDER BY r.created_at LIMIT 1 FOR UPDATE OF r SKIP LOCKED"
        )
    )
    row = result.mappings().first()
    if row is None:
        return None
    await session.execute(
        text(
            "UPDATE hd_rule_replay SET state = 'running', "
            "started_at = coalesce(started_at, now()), "
            "lease_until = now() + make_interval(secs => :s) WHERE id = :id"
        ),
        {"id": row["id"], "s": lease_seconds},
    )
    return row


async def lock_running(session: AsyncSession, replay_id: UUID) -> Row | None:
    """The replay, locked, if it is still running (not cancelled meanwhile)."""
    result = await session.execute(
        text(_SELECT + "WHERE r.id = :id AND r.state = 'running' FOR UPDATE OF r"),
        {"id": replay_id},
    )
    return result.mappings().first()


async def save_page(
    session: AsyncSession,
    replay_id: UUID,
    *,
    counts: Mapping[str, int],
    samples: Sequence[Mapping[str, Any]],
    conflict_samples: Sequence[Mapping[str, Any]],
    cursor_at: datetime | None,
    cursor_id: UUID | None,
    lease_seconds: int,
    done: bool,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_rule_replay SET checked = :checked, matched = :matched, "
            "would_change = :would_change, would_wait = :would_wait, conflicts = :conflicts, "
            "samples = CAST(:samples AS jsonb), conflict_samples = CAST(:clashes AS jsonb), "
            "cursor_at = :cursor_at, cursor_id = :cursor_id, "
            "lease_until = CASE WHEN :done THEN NULL "
            "ELSE now() + make_interval(secs => :s) END, "
            "state = CASE WHEN :done THEN 'done' ELSE state END, "
            "finished_at = CASE WHEN :done THEN now() ELSE finished_at END WHERE id = :id"
        ),
        {
            **counts,
            "id": replay_id,
            "samples": json.dumps(list(samples)),
            "clashes": json.dumps(list(conflict_samples)),
            "cursor_at": cursor_at,
            "cursor_id": cursor_id,
            "s": lease_seconds,
            "done": done,
        },
    )


async def release(session: AsyncSession, replay_id: UUID) -> None:
    """The end of a pass: the next pass (any worker) may carry on."""
    await session.execute(
        text("UPDATE hd_rule_replay SET lease_until = NULL WHERE id = :id AND state = 'running'"),
        {"id": replay_id},
    )


async def fail(session: AsyncSession, replay_id: UUID, error: str) -> None:
    await session.execute(
        text(
            "UPDATE hd_rule_replay SET state = 'failed', error = :e, finished_at = now(), "
            "lease_until = NULL WHERE id = :id AND state = 'running'"
        ),
        {"id": replay_id, "e": error[:300]},
    )


_NEWEST = (
    "SELECT id, created_at FROM hd_ticket WHERE merged_into_id IS NULL "
    "ORDER BY created_at DESC, id DESC LIMIT :n"
)
_NEWEST_AFTER = (
    "SELECT id, created_at FROM hd_ticket WHERE merged_into_id IS NULL "
    "AND (created_at, id) < (:at, :id) ORDER BY created_at DESC, id DESC LIMIT :n"
)


async def ticket_page(
    session: AsyncSession, after_at: datetime | None, after_id: UUID | None, size: int
) -> Sequence[Row]:
    """The next tickets, newest first, after the cursor. Merged tickets are left out (they
    live on in the ticket they were merged into)."""
    query = _NEWEST_AFTER if after_at is not None else _NEWEST
    result = await session.execute(text(query), {"at": after_at, "id": after_id, "n": size})
    return result.mappings().all()


async def prune(session: AsyncSession, days: int) -> int:
    result = await session.execute(
        text(
            "DELETE FROM hd_rule_replay WHERE state NOT IN ('queued', 'running') "
            "AND coalesce(finished_at, created_at) < now() - make_interval(days => :d) "
            "RETURNING id"
        ),
        {"d": days},
    )
    return len(result.all())
