"""SQL for the archive tier (md O3). RLS applies: only the session's workspace."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo import limits as limits_repo

# When each import source last brought tickets in: an imported ticket counts from then
# (as retention does), not from when it was resolved in the old help desk.
_IMPORTED = (
    "imported AS (SELECT source, max(greatest(started_at, coalesce(finished_at, started_at), "
    "coalesce(last_sync_at, started_at))) AS at FROM hd_import_run GROUP BY source)"
)

# What stops a ticket from moving, so no live ticket ever loses data by it (ON DELETE SET
# NULL or CASCADE on the ticket row): another ticket still in `hd_ticket` names it as its
# parent or as what it was merged into; it is linked to a live ticket that isn't resolved;
# or an email of it is still waiting to be sent. Such a ticket waits: once the tickets
# that name it are archived too (children and merged tickets move first, being named by
# nobody), it moves on a later run.
_MOVABLE = """
    NOT EXISTS (SELECT 1 FROM hd_ticket r WHERE r.parent_id = t.id AND r.id <> t.id)
    AND NOT EXISTS (SELECT 1 FROM hd_ticket r WHERE r.merged_into_id = t.id AND r.id <> t.id)
    AND NOT EXISTS (
        SELECT 1 FROM hd_link l JOIN hd_ticket o
            ON o.id = CASE WHEN l.ticket_a = t.id THEN l.ticket_b ELSE l.ticket_a END
        WHERE (l.ticket_a = t.id OR l.ticket_b = t.id) AND o.status <> 'resolved')
    AND NOT EXISTS (
        SELECT 1 FROM hd_email_out e WHERE e.ticket_id = t.id AND e.status = 'pending')
"""


def _rows(table: str, order: str, *, extra: str = "", join: str = "", drop: str = "") -> str:
    """Every row of `table` on ticket t, all columns, as a JSON array."""
    return (
        f"coalesce((SELECT jsonb_agg(to_jsonb(x) - 'workspace_id' - 'ticket_id'{drop}{extra} "  # noqa: S608
        f"ORDER BY {order}) FROM {table} x {join} WHERE x.ticket_id = t.id), '[]'::jsonb)"
    )


_NAME = "LEFT JOIN member {a} ON {a}.id = x.{col}"

_RELATED = f"""jsonb_build_object(
    'time_log', {_rows("hd_time_log", "x.logged_at, x.id",
                       extra=" || jsonb_build_object('member_name', mm.name)",
                       join=_NAME.format(a="mm", col="member_id"))},
    'tasks', {_rows("hd_task", "x.created_at, x.id",
                    extra=" || jsonb_build_object('owner_name', mo.name)",
                    join=_NAME.format(a="mo", col="owner_id"))},
    'approvals', {_rows("hd_approval", "x.created_at, x.id",
                        extra=" || jsonb_build_object('requester_name', mr.name, "
                        "'approver_name', ma.name, 'decided_by_name', md.name)",
                        join=_NAME.format(a="mr", col="requester_id") + " "
                        + _NAME.format(a="ma", col="approver_id") + " "
                        + _NAME.format(a="md", col="decided_by"))},
    'links', coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'id', l.id, 'kind', l.kind, 'ticket_id', o.id, 'number', o.number,
            'created_by', l.created_by, 'created_at', l.created_at) ORDER BY l.created_at, l.id)
        FROM hd_link l JOIN hd_ticket o
            ON o.id = CASE WHEN l.ticket_a = t.id THEN l.ticket_b ELSE l.ticket_a END
        WHERE l.ticket_a = t.id OR l.ticket_b = t.id), '[]'::jsonb),
    'side_conversations', {_rows("hd_side_conversation", "x.created_at, x.id",
        extra=" || jsonb_build_object('messages', coalesce((SELECT jsonb_agg("
        "to_jsonb(sm) - 'workspace_id' ORDER BY sm.created_at, sm.id) "
        "FROM hd_side_message sm WHERE sm.side_id = x.id), '[]'::jsonb))")},
    'jira_issues', {_rows("hd_jira_issue", "x.created_at, x.issue_key")},
    'emails', {_rows("hd_email_out", "x.created_at, x.id",
                     drop=" - 'attempts' - 'next_attempt_at'",
                     extra=" || CASE WHEN x.sensitive THEN jsonb_build_object('body_text', NULL) "
                     "ELSE '{}'::jsonb END")},
    'cevanos_chat', (SELECT to_jsonb(x) - 'workspace_id' - 'ticket_id'
                     FROM hd_cevanos_chat x WHERE x.ticket_id = t.id)
)"""  # noqa: S608  # fmt: skip

_MOVE = f"""
WITH {_IMPORTED},
picked AS (
    SELECT t.id FROM hd_ticket t LEFT JOIN imported i ON i.source = t.source
    WHERE t.status = 'resolved' AND greatest(t.resolved_at, i.at) < :cutoff
    AND {_MOVABLE}
    ORDER BY t.resolved_at, t.id
    LIMIT :limit
    FOR UPDATE OF t SKIP LOCKED
),
copied AS (
    INSERT INTO hd_ticket_archive (workspace_id, id, number, subject, status, priority,
        channel, source, source_id, contact_id, team_id, assignee_id, created_at,
        resolved_at, ticket, messages, files, file_bytes, rating, related, email_ids)
    SELECT t.workspace_id, t.id, t.number, t.subject, t.status, t.priority, t.channel,
        t.source, t.source_id, t.contact_id, t.team_id, t.assignee_id, t.created_at,
        t.resolved_at,
        to_jsonb(t) - 'search_text' || jsonb_build_object(
            'team_name', tm.name, 'assignee_name', mb.name, 'type_name', ty.name),
        coalesce((SELECT jsonb_agg(to_jsonb(m) - 'workspace_id' - 'ticket_id'
                                   ORDER BY m.created_at, m.id)
                  FROM hd_message m WHERE m.ticket_id = t.id), '[]'::jsonb),
        coalesce((SELECT jsonb_agg(to_jsonb(a) - 'workspace_id' - 'ticket_id'
                                   ORDER BY a.created_at, a.id)
                  FROM hd_attachment a WHERE a.ticket_id = t.id), '[]'::jsonb),
        (SELECT coalesce(sum(a.size), 0) FROM hd_attachment a WHERE a.ticket_id = t.id),
        (SELECT to_jsonb(r) - 'workspace_id' - 'ticket_id' FROM hd_csat r
         WHERE r.ticket_id = t.id),
        {_RELATED},
        ARRAY(SELECT m.email->>'message_id' FROM hd_message m
              WHERE m.ticket_id = t.id AND m.email->>'message_id' IS NOT NULL)
    FROM hd_ticket t JOIN picked p ON p.id = t.id
    LEFT JOIN hd_team tm ON tm.id = t.team_id
    LEFT JOIN member mb ON mb.id = t.assignee_id
    LEFT JOIN hd_ticket_type ty ON ty.id = t.type_id
    RETURNING id
)
DELETE FROM hd_ticket WHERE id IN (SELECT id FROM copied) RETURNING id
"""  # noqa: S608 (constants only)


async def move(session: AsyncSession, cutoff: datetime, limit: int) -> int:
    """One batch: resolved tickets older than the cutoff go to the archive with everything
    that is their history: the ticket, every message (all columns, the email headers too),
    its files (names, sizes, storage keys), rating, time logged, tasks, approvals, links
    (with the other ticket's number), side conversations with their messages, Jira issues,
    the emails sent and the Cevanos chat. Then the ticket row is deleted, and what is left
    hanging off it goes with it (ON DELETE CASCADE): only notifications and mentions (an
    agent's inbox, not the ticket's history) and rating links (long expired).

    A ticket another live ticket still needs waits (see `_MOVABLE`). Tickets another worker
    has locked are skipped, never moved twice."""
    result = await session.execute(text(_MOVE), {"cutoff": cutoff, "limit": limit})
    return len(result.all())


_SUMMARY = (
    "SELECT a.id, a.number, a.subject, a.status, a.priority, a.channel, a.created_at, "
    "a.resolved_at, a.archived_at, a.contact_id, c.email AS contact_email, "
    "c.name AS contact_name, a.team_id, a.assignee_id, "
    # The names when it was archived: the team or member may be gone since.
    "coalesce(a.ticket->>'team_name', tm.name) AS team_name, "
    "coalesce(a.ticket->>'assignee_name', mb.name) AS assignee_name "
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
            _SUMMARY.replace(
                "SELECT ", "SELECT a.ticket, a.messages, a.files, a.rating, a.related, ", 1
            )
            + "WHERE a.id = :id"
        ),
        {"id": ticket_id},
    )
    return result.mappings().first()


# ---- a late email reply, a re-import -------------------------------------------------


async def for_email_ids(session: AsyncSession, ids: Sequence[str]) -> RowMapping | None:
    """The archived ticket that had an email with one of these Message-IDs (In-Reply-To,
    References), the first listed winning."""
    if not ids:
        return None
    result = await session.execute(
        text(
            "SELECT a.id, a.number, (SELECT min(array_position(CAST(:ids AS text[]), e)) "
            " FROM unnest(a.email_ids) e) AS pos "
            "FROM hd_ticket_archive a WHERE a.email_ids && CAST(:ids AS text[]) "
            "ORDER BY pos, a.number DESC LIMIT 1"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().first()


async def for_contact_number(
    session: AsyncSession, number: int, contact_email: str
) -> RowMapping | None:
    """Archived ticket T-<number>, only if <contact_email> is its customer."""
    result = await session.execute(
        text(
            "SELECT a.id, a.number FROM hd_ticket_archive a "
            "JOIN hd_contact c ON c.id = a.contact_id "
            "WHERE a.number = :n AND c.email = :e"
        ),
        {"n": number, "e": contact_email},
    )
    return result.mappings().first()


async def source_ids(session: AsyncSession, source: str, external_ids: Sequence[str]) -> set[str]:
    """Which of these import source ids are already here as archived tickets."""
    if not external_ids:
        return set()
    result = await session.execute(
        text(
            "SELECT source_id FROM hd_ticket_archive "
            "WHERE source = :s AND source_id = ANY(CAST(:ids AS text[]))"
        ),
        {"s": source, "ids": list(external_ids)},
    )
    return {str(v) for v in result.scalars().all()}


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
    """As retention deletes live tickets: their events and webhook deliveries (which name
    the ticket and its subject, without a foreign key) go too."""
    await limits_repo.delete_ticket_events(session, ticket_ids)
    result = await session.execute(
        text("DELETE FROM hd_ticket_archive WHERE id = ANY(CAST(:ids AS uuid[])) RETURNING id"),
        {"ids": list(ticket_ids)},
    )
    return len(result.all())


# ---- privacy (md S11) -----------------------------------------------------------------


async def for_export(session: AsyncSession, ids: Sequence[UUID]) -> Sequence[RowMapping]:
    """Archived tickets as the privacy export reads them, with their customer (retention
    export before delete, md O2)."""
    result = await session.execute(
        text(
            "SELECT a.id, a.number, a.subject, a.status, a.priority, a.channel, a.created_at, "
            "a.resolved_at, a.ticket, a.messages, a.files, a.rating, a.contact_id, "
            "c.email AS contact_email, c.name AS contact_name FROM hd_ticket_archive a "
            "LEFT JOIN hd_contact c ON c.id = a.contact_id WHERE a.id = ANY(:ids) "
            "ORDER BY a.number"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().all()


async def for_contact(session: AsyncSession, contact_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT id, number, subject, status, priority, channel, created_at, resolved_at, "
            "ticket, messages, files, rating, related FROM hd_ticket_archive "
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


def _each(array: str, patch: str) -> str:
    """`array` (a JSON array) with `patch` merged into each element, order kept."""
    return (
        f"coalesce((SELECT jsonb_agg(x || {patch} ORDER BY n) FROM "  # noqa: S608
        f"jsonb_array_elements({array}) WITH ORDINALITY AS e(x, n)), '[]'::jsonb)"
    )


_ERASED = "CAST(:erased AS text)"

# As live erase (repo/privacy.py): the ticket, its messages (their email headers and
# recipients too), file names, the rating's comment, the emails sent, tasks, approvals,
# time notes and side conversations. Jira issues and links hold only keys and ids.
_ERASE = f"""
UPDATE hd_ticket_archive a SET
    subject = :erased,
    ticket = a.ticket || jsonb_build_object('subject', {_ERASED},
                                            'fields', '{{}}'::jsonb, 'handover', NULL),
    messages = {_each("a.messages", f"jsonb_build_object('body', {_ERASED}, 'email', NULL, "
        "'recipients', NULL) "
        f"|| CASE WHEN x->>'kind' = 'customer' THEN jsonb_build_object('author_name', "
        f"CAST(:name AS text)) ELSE '{{}}'::jsonb END")},
    files = {_each("a.files", "jsonb_build_object('name', 'erased', 'scan_status', 'erased')")},
    file_bytes = 0,
    email_ids = '{{}}',
    rating = CASE WHEN a.rating IS NULL THEN NULL
                  ELSE a.rating || '{{"comment": null}}'::jsonb END,
    related = a.related || jsonb_build_object(
        'emails', {_each("a.related->'emails'", f"jsonb_build_object('subject', {_ERASED}, "
            f"'body_text', {_ERASED}, 'to_addresses', jsonb_build_array('erased@erased.invalid'), "
            "'cc_addresses', '[]'::jsonb)")},
        'tasks', {_each("a.related->'tasks'", f"jsonb_build_object('text', {_ERASED})")},
        'approvals', {_each("a.related->'approvals'",
            f"jsonb_build_object('what', {_ERASED}, 'decision_note', NULL)")},
        'time_log', {_each("a.related->'time_log'", "jsonb_build_object('note', NULL)")},
        'side_conversations', {_each("a.related->'side_conversations'",
            f"jsonb_build_object('subject', {_ERASED}, "
            "'to_addresses', jsonb_build_array('erased@erased.invalid'), 'messages', "
            + _each("x->'messages'", f"jsonb_build_object('body', {_ERASED}) || CASE WHEN "
                    "x->>'direction' = 'in' THEN jsonb_build_object('author_name', "
                    "CAST(:name AS text), 'from_address', 'erased@erased.invalid') "
                    "ELSE '{}'::jsonb END") + ")")})
WHERE a.contact_id = :c
RETURNING a.id
"""  # noqa: S608 (constants only)  # fmt: skip


async def erase_for_contact(
    session: AsyncSession, contact_id: UUID, *, erased: str, name: str
) -> int:
    """As erasing live tickets: every word that could identify the customer is replaced
    (subject, fields, messages and their email headers, file names, the rating's comment,
    the emails sent, tasks, approvals, time notes); the rows stay. Their files are deleted
    from storage by the caller (`file_keys`), so they no longer count as storage used."""
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
