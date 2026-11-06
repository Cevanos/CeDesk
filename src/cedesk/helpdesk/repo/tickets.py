"""SQL for tickets, their contacts and messages. Always run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_DETAIL = """
    SELECT t.id, t.number, t.subject, t.status, t.priority, t.channel, t.fields, t.tags,
           t.type_id, ty.name AS type_name,
           t.team_id, tm.name AS team_name,
           t.assignee_id, a.name AS assignee_name,
           t.contact_id, c.name AS contact_name, c.email AS contact_email,
           c.vip AS contact_vip, c.customer_since AS contact_since,
           t.first_reply_at, t.resolved_at, t.reopen_count, t.created_at, t.updated_at
    FROM hd_ticket t
    JOIN hd_contact c ON c.id = t.contact_id
    LEFT JOIN hd_ticket_type ty ON ty.id = t.type_id
    LEFT JOIN hd_team tm ON tm.id = t.team_id
    LEFT JOIN member a ON a.id = t.assignee_id
"""

# One static query; every filter is "parameter is NULL or matches", so nothing is
# ever built from strings. `q_like` is already escaped with '!' by the caller.
# Two fixed strings joined; every value is a bound parameter.
_LIST = (
    _DETAIL  # noqa: S608
    + """
    WHERE (CAST(:status AS text) IS NULL OR t.status = CAST(:status AS text))
      AND (CAST(:priority AS text) IS NULL OR t.priority = CAST(:priority AS text))
      AND (CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))
      AND (CAST(:scope AS text) = 'everyone'
           OR (CAST(:scope AS text) = 'mine' AND t.assignee_id = CAST(:me AS uuid))
           OR (CAST(:scope AS text) = 'unassigned' AND t.assignee_id IS NULL))
      AND (CAST(:q_like AS text) IS NULL
           OR t.number = CAST(:q_number AS integer)
           OR t.subject ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR c.name ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR c.email ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR EXISTS (SELECT 1 FROM jsonb_each_text(t.fields) f
                      WHERE f.value ILIKE CAST(:q_like AS text) ESCAPE '!')
           OR EXISTS (SELECT 1 FROM unnest(t.tags) tag
                      WHERE tag ILIKE CAST(:q_like AS text) ESCAPE '!'))
    ORDER BY
      CASE WHEN t.status = 'resolved' THEN 1 ELSE 0 END,
      CASE WHEN t.status = 'resolved' THEN NULL
           ELSE array_position(ARRAY['urgent', 'high', 'medium', 'low'], t.priority) END,
      CASE WHEN t.status = 'resolved' THEN NULL ELSE t.created_at END ASC,
      t.resolved_at DESC NULLS LAST,
      t.number DESC
    LIMIT :limit OFFSET :offset
"""
)


async def next_number(session: AsyncSession, workspace_id: UUID) -> int:
    """T-1001, T-1002, ... The row lock makes two creates in parallel take turns."""
    result = await session.execute(
        text(
            "INSERT INTO hd_counter (workspace_id, next_ticket_number) VALUES (:ws, 1002) "
            "ON CONFLICT (workspace_id) DO UPDATE "
            "SET next_ticket_number = hd_counter.next_ticket_number + 1, updated_at = now() "
            "RETURNING next_ticket_number - 1"
        ),
        {"ws": workspace_id},
    )
    return int(result.scalar_one())


async def upsert_contact(session: AsyncSession, workspace_id: UUID, email: str, name: str) -> UUID:
    """The contact with this email, created on first sight. An existing name is kept."""
    result = await session.execute(
        text(
            "INSERT INTO hd_contact (workspace_id, email, name) VALUES (:ws, :email, :name) "
            "ON CONFLICT (workspace_id, email) DO UPDATE SET updated_at = hd_contact.updated_at "
            "RETURNING id"
        ),
        {"ws": workspace_id, "email": email, "name": name},
    )
    return UUID(str(result.scalar_one()))


async def insert_ticket(session: AsyncSession, values: Mapping[str, Any]) -> UUID:
    params = dict(values)
    params["fields"] = json.dumps(params["fields"])
    result = await session.execute(
        text(
            "INSERT INTO hd_ticket (workspace_id, number, subject, status, priority, type_id, "
            "team_id, assignee_id, contact_id, channel, fields, tags, created_by_id) "
            "VALUES (:workspace_id, :number, :subject, 'open', :priority, :type_id, :team_id, "
            ":assignee_id, :contact_id, :channel, CAST(:fields AS jsonb), :tags, :created_by_id) "
            "RETURNING id"
        ),
        params,
    )
    return UUID(str(result.scalar_one()))


async def lock_ticket(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, number, subject, status, priority, type_id, team_id, assignee_id, "
            "fields, tags, resolved_at, reopen_count FROM hd_ticket WHERE id = :id FOR UPDATE"
        ),
        {"id": ticket_id},
    )
    return result.mappings().first()


async def update_ticket(session: AsyncSession, ticket_id: UUID, values: Mapping[str, Any]) -> None:
    params = dict(values)
    params["fields"] = json.dumps(params["fields"])
    await session.execute(
        text(
            "UPDATE hd_ticket SET subject = :subject, status = :status, priority = :priority, "
            "type_id = :type_id, team_id = :team_id, assignee_id = :assignee_id, "
            "fields = CAST(:fields AS jsonb), tags = :tags, "
            "resolved_at = CASE WHEN :status = 'resolved' THEN coalesce(resolved_at, now()) END, "
            "reopen_count = :reopen_count, updated_at = now() WHERE id = :id"
        ),
        {**params, "id": ticket_id},
    )


async def ticket_detail(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(text(_DETAIL + " WHERE t.id = :id"), {"id": ticket_id})
    return result.mappings().first()


async def list_tickets(
    session: AsyncSession,
    *,
    me: UUID,
    status: str | None,
    priority: str | None,
    team: UUID | None,
    scope: str,
    q_like: str | None,
    q_number: int | None,
    limit: int,
    offset: int,
) -> Sequence[Row]:
    result = await session.execute(
        text(_LIST),
        {
            "me": me,
            "status": status,
            "priority": priority,
            "team": team,
            "scope": scope,
            "q_like": q_like,
            "q_number": q_number,
            "limit": limit,
            "offset": offset,
        },
    )
    return result.mappings().all()


async def counts_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(text("SELECT status, count(*) FROM hd_ticket GROUP BY status"))
    return {str(status): int(n) for status, n in result.all()}


async def other_ticket_count(session: AsyncSession, contact_id: UUID, ticket_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_ticket WHERE contact_id = :c AND id <> :t"),
        {"c": contact_id, "t": ticket_id},
    )
    return int(result.scalar_one())


async def insert_message(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    ticket_id: UUID,
    kind: str,
    body: str,
    author_member_id: UUID | None,
    author_name: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_member_id, "
            "author_name) VALUES (:ws, :ticket, :kind, :body, :author, :name)"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "kind": kind,
            "body": body,
            "author": author_member_id,
            "name": author_name,
        },
    )


async def messages(session: AsyncSession, ticket_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT id, kind, body, author_member_id, author_name, created_at FROM hd_message "
            "WHERE ticket_id = :id ORDER BY created_at, id"
        ),
        {"id": ticket_id},
    )
    return result.mappings().all()


async def member_brief(session: AsyncSession, member_id: UUID) -> Row | None:
    result = await session.execute(
        text("SELECT id, name, role, is_active FROM member WHERE id = :id"), {"id": member_id}
    )
    return result.mappings().first()
