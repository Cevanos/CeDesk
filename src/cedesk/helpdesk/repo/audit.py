"""Audit log rows (md S9). Rows are only ever added; the app role can't change them."""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.audit import GENESIS, Entry, entry_hash

COLUMNS = (
    "a.seq, a.at, a.actor_id, a.actor_name, a.ip, a.area, a.action, a.target, a.target_id, "
    "a.detail, a.prev_hash, a.hash"
)


async def append(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    at: datetime,
    actor_id: UUID | None,
    actor_name: str,
    ip: str | None,
    area: str,
    action: str,
    target: str,
    target_id: UUID | None,
    detail: Mapping[str, Any],
) -> Entry:
    """Adds the next entry of the workspace's chain, in the caller's transaction, so a
    change and its entry are saved together or not at all. One append at a time per
    workspace (until the transaction ends), so two never take the same place."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('hd_audit:' || :ws, 0))"),
        {"ws": str(workspace_id)},
    )
    last = (
        await session.execute(text("SELECT seq, hash FROM hd_audit ORDER BY seq DESC LIMIT 1"))
    ).first()
    seq, prev_hash = (int(last[0]) + 1, bytes(last[1])) if last else (1, GENESIS)
    entry = Entry(
        seq=seq,
        at=at,
        actor_id=actor_id,
        actor_name=actor_name[:200] or "Someone",
        ip=(ip or None) and ip[:64],
        area=area,
        action=action,
        target=target[:300],
        target_id=target_id,
        detail=detail,
    )
    fields = entry.fields()
    await session.execute(
        text(
            "INSERT INTO hd_audit (workspace_id, seq, at, actor_id, actor_name, ip, area, "
            "action, target, target_id, detail, prev_hash, hash) VALUES (:ws, :seq, :at, "
            ":actor_id, :actor_name, :ip, :area, :action, :target, :target_id, "
            "CAST(:detail AS jsonb), :prev_hash, :hash)"
        ),
        {
            "ws": workspace_id,
            "seq": seq,
            "at": entry.at,
            "actor_id": actor_id,
            "actor_name": entry.actor_name,
            "ip": entry.ip,
            "area": area,
            "action": action,
            "target": entry.target,
            "target_id": target_id,
            "detail": _json(fields["detail"]),
            "prev_hash": prev_hash,
            "hash": entry_hash(prev_hash, entry),
        },
    )
    return entry


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


async def member_name(session: AsyncSession, member_id: UUID) -> str | None:
    found = await session.scalar(text("SELECT name FROM member WHERE id = :id"), {"id": member_id})
    return str(found) if found is not None else None


async def page(
    session: AsyncSession, *, area: str | None, before_seq: int | None, limit: int
) -> Sequence[RowMapping]:
    """Newest first."""
    result = await session.execute(
        text(
            "SELECT " + COLUMNS + " FROM hd_audit a "  # noqa: S608
            "WHERE (CAST(:area AS text) IS NULL OR a.area = :area) "
            "AND (CAST(:before AS bigint) IS NULL OR a.seq < :before) "
            "ORDER BY a.seq DESC LIMIT :limit"
        ),
        {"area": area, "before": before_seq, "limit": limit},
    )
    return result.mappings().all()


async def in_order(
    session: AsyncSession, *, area: str | None = None, batch: int = 1000
) -> AsyncIterator[RowMapping]:
    """Every entry, oldest first, read in batches (for Verify and the CSV)."""
    after = 0
    while True:
        result = await session.execute(
            text(
                "SELECT " + COLUMNS + " FROM hd_audit a "  # noqa: S608
                "WHERE a.seq > :after AND (CAST(:area AS text) IS NULL OR a.area = :area) "
                "ORDER BY a.seq LIMIT :batch"
            ),
            {"after": after, "area": area, "batch": batch},
        )
        rows = result.mappings().all()
        for row in rows:
            yield row
        if len(rows) < batch:
            return
        after = int(rows[-1]["seq"])
