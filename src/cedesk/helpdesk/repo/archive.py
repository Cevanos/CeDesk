"""SQL for the archive tier (md O3). RLS applies: only the session's workspace."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

# When each import source last brought tickets in: an imported ticket counts from then
# (as retention does), not from when it was resolved in the old help desk.
_IMPORTED = (
    "imported AS (SELECT source, max(greatest(started_at, coalesce(finished_at, started_at), "
    "coalesce(last_sync_at, started_at))) AS at FROM hd_import_run GROUP BY source)"
)

_MOVE = f"""
WITH {_IMPORTED},
picked AS (
    SELECT t.id FROM hd_ticket t LEFT JOIN imported i ON i.source = t.source
    WHERE t.status = 'resolved' AND greatest(t.resolved_at, i.at) < :cutoff
    ORDER BY t.resolved_at, t.id
    LIMIT :limit
    FOR UPDATE OF t SKIP LOCKED
),
copied AS (
    INSERT INTO hd_ticket_archive (workspace_id, id, number, subject, status, priority,
        channel, source, contact_id, team_id, assignee_id, created_at, resolved_at, ticket,
        messages, files, rating)
    SELECT t.workspace_id, t.id, t.number, t.subject, t.status, t.priority, t.channel,
        t.source, t.contact_id, t.team_id, t.assignee_id, t.created_at, t.resolved_at,
        to_jsonb(t) - 'search_text',
        coalesce((SELECT jsonb_agg(jsonb_build_object(
                     'id', m.id, 'kind', m.kind, 'author_name', m.author_name,
                     'author_member_id', m.author_member_id, 'body', m.body,
                     'created_at', m.created_at) ORDER BY m.created_at, m.id)
                  FROM hd_message m WHERE m.ticket_id = t.id), '[]'::jsonb),
        coalesce((SELECT jsonb_agg(jsonb_build_object(
                     'id', a.id, 'message_id', a.message_id, 'name', a.name, 'size', a.size,
                     'mime', a.mime, 'object_key', a.object_key,
                     'scan_status', a.scan_status) ORDER BY a.created_at, a.id)
                  FROM hd_attachment a WHERE a.ticket_id = t.id), '[]'::jsonb),
        (SELECT jsonb_build_object('score', r.score, 'comment', r.comment,
                                   'source', r.source, 'rated_at', r.rated_at)
         FROM hd_csat r WHERE r.ticket_id = t.id)
    FROM hd_ticket t JOIN picked p ON p.id = t.id
    RETURNING id
)
DELETE FROM hd_ticket WHERE id IN (SELECT id FROM copied) RETURNING id
"""  # noqa: S608 (constants only)


async def move(session: AsyncSession, cutoff: datetime, limit: int) -> int:
    """One batch: resolved tickets older than the cutoff go to the archive, with their
    messages, files' names and keys, and rating; the rest of what hung off them (tasks,
    notifications, links...) goes with the ticket row (ON DELETE CASCADE). Tickets another
    worker has locked are skipped, never moved twice."""
    result = await session.execute(text(_MOVE), {"cutoff": cutoff, "limit": limit})
    return len(result.all())


_SUMMARY = (
    "SELECT a.id, a.number, a.subject, a.status, a.priority, a.channel, a.created_at, "
    "a.resolved_at, a.archived_at, a.contact_id, c.email AS contact_email, "
    "c.name AS contact_name, a.team_id, tm.name AS team_name, a.assignee_id, "
    "mb.name AS assignee_name "
    "FROM hd_ticket_archive a LEFT JOIN hd_contact c ON c.id = a.contact_id "
    "LEFT JOIN hd_team tm ON tm.id = a.team_id LEFT JOIN member mb ON mb.id = a.assignee_id "
)


async def search(
    session: AsyncSession,
    *,
    number: int | None,
    contact_id: UUID | None,
    email: str | None,
    team_id: UUID | None,
    limit: int,
) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            _SUMMARY + "WHERE (CAST(:number AS bigint) IS NULL OR a.number = :number) "
            "AND (CAST(:contact AS uuid) IS NULL OR a.contact_id = CAST(:contact AS uuid)) "
            "AND (CAST(:email AS text) IS NULL OR lower(c.email) = lower(CAST(:email AS text))) "
            "AND (CAST(:team AS uuid) IS NULL OR a.team_id = CAST(:team AS uuid)) "
            "ORDER BY a.number DESC LIMIT :limit"
        ),
        {
            "number": number,
            "contact": contact_id,
            "email": email,
            "team": team_id,
            "limit": limit,
        },
    )
    return result.mappings().all()


async def get(session: AsyncSession, ticket_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(
            _SUMMARY.replace("SELECT ", "SELECT a.ticket, a.messages, a.files, a.rating, ", 1)
            + "WHERE a.id = :id"
        ),
        {"id": ticket_id},
    )
    return result.mappings().first()


# ---- retention (md O2) ----------------------------------------------------------------

_KEYS = (
    "coalesce((SELECT array_agg(f->>'object_key') FROM jsonb_array_elements(a.files) f "
    "WHERE f->>'scan_status' IS DISTINCT FROM 'erased'), '{}')"
)


async def resolved_before(
    session: AsyncSession, cutoff: datetime, limit: int
) -> Sequence[RowMapping]:
    """Archived tickets past retention (counted as for live ones), with their files' keys."""
    result = await session.execute(
        text(
            f"WITH {_IMPORTED} SELECT a.id, {_KEYS} AS keys FROM hd_ticket_archive a "  # noqa: S608
            "LEFT JOIN imported i ON i.source = a.source "
            "WHERE greatest(a.resolved_at, i.at) < :cutoff "
            "ORDER BY a.resolved_at, a.id LIMIT :limit"
        ),
        {"cutoff": cutoff, "limit": limit},
    )
    return result.mappings().all()


async def delete(session: AsyncSession, ticket_ids: Sequence[UUID]) -> int:
    result = await session.execute(
        text("DELETE FROM hd_ticket_archive WHERE id = ANY(CAST(:ids AS uuid[])) RETURNING id"),
        {"ids": list(ticket_ids)},
    )
    return len(result.all())


# ---- privacy (md S11) -----------------------------------------------------------------


async def for_contact(session: AsyncSession, contact_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT id, number, subject, status, priority, channel, created_at, resolved_at, "
            "ticket, messages, files, rating FROM hd_ticket_archive "
            "WHERE contact_id = :c ORDER BY number"
        ),
        {"c": contact_id},
    )
    return result.mappings().all()


async def file_keys(session: AsyncSession, contact_id: UUID) -> list[str]:
    result = await session.execute(
        text(f"SELECT unnest({_KEYS}) FROM hd_ticket_archive a WHERE a.contact_id = :c"),  # noqa: S608
        {"c": contact_id},
    )
    return [str(r[0]) for r in result.all() if r[0] is not None]


_ERASE = """
UPDATE hd_ticket_archive a SET
    subject = :erased,
    ticket = a.ticket || jsonb_build_object('subject', CAST(:erased AS text),
                                            'fields', '{}'::jsonb, 'handover', NULL),
    messages = coalesce((
        SELECT jsonb_agg(m || jsonb_build_object('body', CAST(:erased AS text))
                         || CASE WHEN m->>'kind' = 'customer'
                                 THEN jsonb_build_object('author_name', CAST(:name AS text))
                                 ELSE '{}'::jsonb END
                         ORDER BY n)
        FROM jsonb_array_elements(a.messages) WITH ORDINALITY AS e(m, n)), '[]'::jsonb),
    files = coalesce((
        SELECT jsonb_agg(f || '{"name": "erased", "scan_status": "erased"}'::jsonb ORDER BY n)
        FROM jsonb_array_elements(a.files) WITH ORDINALITY AS e(f, n)), '[]'::jsonb),
    rating = CASE WHEN a.rating IS NULL THEN NULL
                  ELSE a.rating || '{"comment": null}'::jsonb END
WHERE a.contact_id = :c
RETURNING a.id
"""


async def erase_for_contact(
    session: AsyncSession, contact_id: UUID, *, erased: str, name: str
) -> int:
    """As erasing live tickets: every word that could identify the customer is replaced
    (subject, fields, messages, file names, the rating's comment); the rows stay."""
    params: dict[str, Any] = {"c": contact_id, "erased": erased, "name": name}
    result = await session.execute(text(_ERASE), params)
    return len(result.all())


# ---- the setting (a column of hd_retention) ----------------------------------------------


async def setting(session: AsyncSession) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT r.archive_after_days, r.updated_at, m.name AS updated_by_name "
            "FROM hd_retention r LEFT JOIN member m ON m.id = r.updated_by"
        )
    )
    return result.mappings().first()


async def save_setting(
    session: AsyncSession, workspace_id: UUID, days: int | None, by: UUID
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_retention (workspace_id, archive_after_days, updated_by) "
            "VALUES (:ws, :days, :by) ON CONFLICT (workspace_id) DO UPDATE SET "
            "archive_after_days = EXCLUDED.archive_after_days, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"ws": workspace_id, "days": days, "by": by},
    )
