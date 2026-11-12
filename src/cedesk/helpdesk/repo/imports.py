"""SQL for imports (md section 8). Always inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_RUN = (
    "SELECT id, workspace_id, connection_id, source, state, options, cursor, counts, sync_on, "
    "last_sync_at, next_run_at, lease_until, last_error, started_by, started_at, finished_at "
    "FROM hd_import_run"
)


# Connections


async def insert_connection(
    session: AsyncSession,
    *,
    connection_id: UUID,
    workspace_id: UUID,
    source: str,
    label: str,
    settings: Mapping[str, Any],
    sealed: bytes | None,
    hint: str | None,
    created_by: UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_import_connection (id, workspace_id, source, label, settings, "
            "credentials_sealed, credentials_hint, created_by) VALUES (:id, :ws, :source, "
            ":label, CAST(:settings AS jsonb), :sealed, :hint, :by)"
        ),
        {
            "id": connection_id, "ws": workspace_id, "source": source, "label": label,
            "settings": json.dumps(dict(settings)), "sealed": sealed, "hint": hint,
            "by": created_by,
        },
    )  # fmt: skip


async def connection(session: AsyncSession, connection_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, workspace_id, source, label, settings, credentials_sealed, "
            "credentials_hint, state, created_by, created_at FROM hd_import_connection "
            "WHERE id = :id"
        ),
        {"id": connection_id},
    )
    return result.mappings().first()


async def close_connection(session: AsyncSession, connection_id: UUID) -> bool:
    """Deletes the credentials and the stored file reference; the row stays as history.
    False when the connection was already closed."""
    result = await session.execute(
        text(
            "UPDATE hd_import_connection SET credentials_sealed = NULL, credentials_hint = NULL, "
            "settings = settings - 'file_key', state = 'closed', updated_at = now() "
            "WHERE id = :id AND state <> 'closed' RETURNING id"
        ),
        {"id": connection_id},
    )
    return result.first() is not None


# Runs


async def insert_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    source: str,
    options: Mapping[str, Any],
    started_by: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_import_run (workspace_id, connection_id, source, options, "
            "started_by) VALUES (:ws, :conn, :source, CAST(:options AS jsonb), :by) RETURNING id"
        ),
        {
            "ws": workspace_id, "conn": connection_id, "source": source,
            "options": json.dumps(dict(options)), "by": started_by,
        },
    )  # fmt: skip
    return UUID(str(result.scalar_one()))


async def run(session: AsyncSession, run_id: UUID) -> Row | None:
    result = await session.execute(text(_RUN + " WHERE id = :id"), {"id": run_id})
    return result.mappings().first()


async def active_run(session: AsyncSession, connection_id: UUID) -> Row | None:
    result = await session.execute(
        text(_RUN + " WHERE connection_id = :c AND state IN ('queued', 'running') LIMIT 1"),
        {"c": connection_id},
    )
    return result.mappings().first()


async def claim_due(session: AsyncSession, now: datetime, lease_until: datetime) -> Row | None:
    """Takes the oldest run that is due and not leased, and leases it. Locked (SKIP
    LOCKED), so two workers never take the same run."""
    result = await session.execute(
        text(
            "UPDATE hd_import_run SET state = 'running', lease_until = :lease "
            "WHERE id = (SELECT id FROM hd_import_run WHERE state IN ('queued', 'running') "
            "AND next_run_at <= :now AND (lease_until IS NULL OR lease_until <= :now) "
            "ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, workspace_id, connection_id, source, state, options, cursor, counts, "
            "sync_on, last_sync_at, next_run_at, lease_until, last_error, started_by, "
            "started_at, finished_at"
        ),
        {"now": now, "lease": lease_until},
    )
    return result.mappings().first()


async def save_progress(
    session: AsyncSession,
    run_id: UUID,
    *,
    cursor: Mapping[str, Any],
    counts: Mapping[str, Any],
    lease_until: datetime,
) -> None:
    """The cursor and counts, in the same transaction as the page they describe."""
    await session.execute(
        text(
            "UPDATE hd_import_run SET cursor = CAST(:cursor AS jsonb), "
            "counts = CAST(:counts AS jsonb), lease_until = :lease WHERE id = :id"
        ),
        {
            "id": run_id, "cursor": json.dumps(dict(cursor)), "counts": json.dumps(dict(counts)),
            "lease": lease_until,
        },
    )  # fmt: skip


async def release(
    session: AsyncSession, run_id: UUID, *, next_run_at: datetime, error: str | None = None
) -> None:
    """Gives the run back to be taken again at `next_run_at` (a pause, or end of a slice)."""
    await session.execute(
        text(
            "UPDATE hd_import_run SET lease_until = NULL, next_run_at = :at, "
            "last_error = :error WHERE id = :id"
        ),
        {"id": run_id, "at": next_run_at, "error": error[:500] if error else None},
    )


async def finish(
    session: AsyncSession, run_id: UUID, *, state: str, error: str | None = None
) -> None:
    await session.execute(
        text(
            "UPDATE hd_import_run SET state = :state, lease_until = NULL, finished_at = now(), "
            "last_error = :error WHERE id = :id"
        ),
        {"id": run_id, "state": state, "error": error[:500] if error else None},
    )


# Which source records are already here


async def mapped(
    session: AsyncSession, source: str, kind: str, external_ids: Sequence[str]
) -> dict[str, UUID]:
    if not external_ids:
        return {}
    result = await session.execute(
        text(
            "SELECT external_id, local_id FROM hd_import_map "
            "WHERE source = :source AND kind = :kind AND external_id = ANY(:ids)"
        ),
        {"source": source, "kind": kind, "ids": list(external_ids)},
    )
    return {str(r[0]): UUID(str(r[1])) for r in result.all()}


async def put_map(
    session: AsyncSession,
    workspace_id: UUID,
    source: str,
    kind: str,
    external_id: str,
    local_id: UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_import_map (workspace_id, source, kind, external_id, local_id) "
            "VALUES (:ws, :source, :kind, :ext, :local) ON CONFLICT DO NOTHING"
        ),
        {"ws": workspace_id, "source": source, "kind": kind, "ext": external_id, "local": local_id},
    )


async def mapped_counts(session: AsyncSession, source: str) -> dict[str, int]:
    """How many contacts, articles and replies this source already brought."""
    result = await session.execute(
        text("SELECT kind, count(*) FROM hd_import_map WHERE source = :s GROUP BY kind"),
        {"s": source},
    )
    return {str(r[0]): int(r[1]) for r in result.all()}


async def existing_ticket_source_ids(
    session: AsyncSession, source: str, external_ids: Sequence[str]
) -> set[str]:
    if not external_ids:
        return set()
    result = await session.execute(
        text("SELECT source_id FROM hd_ticket WHERE source = :s AND source_id = ANY(:ids)"),
        {"s": source, "ids": list(external_ids)},
    )
    return {str(v) for v in result.scalars().all()}


async def ticket_count(session: AsyncSession, source: str) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_ticket WHERE source = :s"), {"s": source}
    )
    return int(result.scalar_one())


# Teams


async def team_names(session: AsyncSession) -> dict[UUID, str]:
    result = await session.execute(text("SELECT id, name FROM hd_team"))
    return {UUID(str(r[0])): str(r[1]) for r in result.all()}


# Writing imported tickets


async def insert_ticket(session: AsyncSession, values: Mapping[str, Any]) -> UUID:
    """An imported ticket with the dates, status and number it already has. Not the normal
    create path: no rules, no routing, no events (md 8.2 step 5)."""
    params = dict(values)
    params["fields"] = json.dumps(params["fields"])
    result = await session.execute(
        text(
            "INSERT INTO hd_ticket (workspace_id, number, subject, status, priority, team_id, "
            "assignee_id, contact_id, channel, fields, tags, first_reply_at, resolved_at, "
            "created_at, updated_at, source, source_id) VALUES (:workspace_id, :number, "
            ":subject, :status, :priority, :team_id, :assignee_id, :contact_id, :channel, "
            "CAST(:fields AS jsonb), :tags, :first_reply_at, :resolved_at, :created_at, "
            ":updated_at, :source, :source_id) RETURNING id"
        ),
        params,
    )
    return UUID(str(result.scalar_one()))


async def insert_message(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    ticket_id: UUID,
    kind: str,
    body: str,
    created_at: datetime,
    author_member_id: UUID | None,
    author_name: str | None,
    channel_message_id: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_member_id, "
            "author_name, channel_message_id, created_at) VALUES (:ws, :ticket, :kind, :body, "
            ":author, :name, :channel_id, :at)"
        ),
        {
            "ws": workspace_id, "ticket": ticket_id, "kind": kind, "body": body,
            "author": author_member_id, "name": author_name, "channel_id": channel_message_id,
            "at": created_at,
        },
    )  # fmt: skip


async def contact_since(session: AsyncSession, contact_id: UUID, at: datetime) -> None:
    """The customer has been one since their earliest ticket, not since the import."""
    await session.execute(
        text(
            "UPDATE hd_contact SET customer_since = LEAST(customer_since, :at) "
            "WHERE id = :id AND customer_since > :at"
        ),
        {"id": contact_id, "at": at},
    )


async def insert_csat(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    ticket_id: UUID,
    contact_id: UUID,
    score: int,
    comment: str | None,
    source: str,
    rated_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, comment, source, "
            "rated_at) VALUES (:ws, :ticket, :contact, :score, :comment, :source, :at) "
            "ON CONFLICT (workspace_id, ticket_id) DO NOTHING"
        ),
        {
            "ws": workspace_id, "ticket": ticket_id, "contact": contact_id, "score": score,
            "comment": comment, "source": source, "at": rated_at,
        },
    )  # fmt: skip


# Writing imported articles


async def category_by_name(session: AsyncSession, name: str) -> UUID | None:
    result = await session.execute(
        text("SELECT id FROM hd_article_category WHERE name = :n"), {"n": name}
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def insert_category(
    session: AsyncSession, workspace_id: UUID, *, name: str, source: str, source_id: str
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_article_category (workspace_id, name, position, source, source_id) "
            "VALUES (:ws, :name, (SELECT coalesce(max(position), 0) + 1 FROM hd_article_category), "
            ":source, :sid) RETURNING id"
        ),
        {"ws": workspace_id, "name": name, "source": source, "sid": source_id},
    )
    return UUID(str(result.scalar_one()))


async def insert_article(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    category_id: UUID | None,
    title: str,
    body: str,
    published: bool,
    source: str,
    source_id: str,
    updated_by: UUID | None,
) -> UUID | None:
    """None when this source article is already here (its source index said so)."""
    result = await session.execute(
        text(
            "INSERT INTO hd_article (workspace_id, category_id, title, body, status, source, "
            "source_id, updated_by, published_at) VALUES (:ws, :cat, :title, :body, :status, "
            ":source, :sid, :by, CASE WHEN :published THEN now() END) "
            "ON CONFLICT (workspace_id, source, source_id) WHERE source IS NOT NULL "
            "DO NOTHING RETURNING id"
        ),
        {
            "ws": workspace_id, "cat": category_id, "title": title, "body": body,
            "status": "published" if published else "draft", "published": published,
            "source": source, "sid": source_id, "by": updated_by,
        },
    )  # fmt: skip
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


# Events: the history and audit of imports


async def add_event(
    session: AsyncSession,
    workspace_id: UUID,
    connection_id: UUID,
    kind: str,
    *,
    run_id: UUID | None = None,
    member_id: UUID | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_import_event (workspace_id, connection_id, run_id, kind, member_id, "
            "detail) VALUES (:ws, :conn, :run, :kind, :member, CAST(:detail AS jsonb))"
        ),
        {
            "ws": workspace_id, "conn": connection_id, "run": run_id, "kind": kind,
            "member": member_id, "detail": json.dumps(dict(detail or {})),
        },
    )  # fmt: skip
