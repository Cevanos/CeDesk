"""SQL for tickets, their contacts and messages. Always run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_COLUMNS = """
           t.id, t.number, t.subject, t.status, t.priority, t.channel, t.fields, t.tags,
           t.type_id, ty.name AS type_name,
           t.team_id, tm.name AS team_name,
           t.assignee_id, a.name AS assignee_name,
           t.contact_id, c.name AS contact_name, c.email AS contact_email,
           c.vip AS contact_vip, c.customer_since AS contact_since,
           t.first_reply_at, t.resolved_at, t.reopen_count, t.created_at, t.updated_at
"""

_FROM = """
    FROM hd_ticket t
    JOIN hd_contact c ON c.id = t.contact_id
    LEFT JOIN hd_ticket_type ty ON ty.id = t.type_id
    LEFT JOIN hd_team tm ON tm.id = t.team_id
    LEFT JOIN member a ON a.id = t.assignee_id
"""

_DETAIL = "SELECT " + _COLUMNS + _FROM

# Every sort order as four ascending keys (k1, k2, k3, k4), so the page after a cursor
# is one row comparison for any sort. k3 is an exact numeric epoch (negated for "newest
# first"); k4 is the ticket number, unique per workspace, as the final tie-break.
_KEYS = """,
    CASE WHEN CAST(:sort AS text) = 'priority' AND t.status = 'resolved' THEN 1 ELSE 0 END
        AS k1,
    CASE WHEN CAST(:sort AS text) = 'priority' AND t.status <> 'resolved'
         THEN array_position(ARRAY['urgent', 'high', 'medium', 'low'], t.priority)
         ELSE 0 END AS k2,
    CASE CAST(:sort AS text)
         WHEN 'priority' THEN
              CASE WHEN t.status = 'resolved'
                   THEN -extract(epoch FROM coalesce(t.resolved_at, t.updated_at))
                   ELSE extract(epoch FROM t.created_at) END
         WHEN 'newest' THEN -extract(epoch FROM t.created_at)
         WHEN 'oldest' THEN extract(epoch FROM t.created_at)
         ELSE -extract(epoch FROM t.updated_at) END AS k3,
    CASE WHEN CAST(:sort AS text) = 'oldest'
              OR (CAST(:sort AS text) = 'priority' AND t.status <> 'resolved')
         THEN t.number ELSE -t.number END AS k4
"""

# One static query; every filter is "parameter is NULL or matches", so nothing is
# ever built from strings. `q_like` is already escaped with '!' by the caller.
# Fixed strings joined; every value is a bound parameter.
_LIST = (
    "SELECT * FROM (SELECT "  # noqa: S608
    + _COLUMNS
    + _KEYS
    + _FROM
    + """
    WHERE (CAST(:status AS text) IS NULL OR t.status = CAST(:status AS text))
      AND (CAST(:priority AS text) IS NULL OR t.priority = CAST(:priority AS text))
      AND (CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))
      AND (CAST(:contact AS uuid) IS NULL OR t.contact_id = CAST(:contact AS uuid))
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
    ) x
    WHERE CAST(:c1 AS integer) IS NULL
       OR (x.k1, x.k2, x.k3, x.k4) > (CAST(:c1 AS integer), CAST(:c2 AS integer),
                                      CAST(:c3 AS numeric), CAST(:c4 AS integer))
    ORDER BY x.k1, x.k2, x.k3, x.k4
    LIMIT :limit
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
            "SELECT id, number, subject, status, priority, channel, type_id, team_id, "
            "assignee_id, fields, tags, resolved_at, reopen_count, updated_at "
            "FROM hd_ticket WHERE id = :id FOR UPDATE"
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


async def set_assignee(session: AsyncSession, ticket_id: UUID, member_id: UUID | None) -> None:
    await session.execute(
        text("UPDATE hd_ticket SET assignee_id = :m, updated_at = now() WHERE id = :id"),
        {"id": ticket_id, "m": member_id},
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
    contact: UUID | None = None,
    scope: str,
    q_like: str | None,
    q_number: int | None,
    sort: str,
    after: tuple[int, int, str, int] | None,
    limit: int,
) -> Sequence[Row]:
    """Rows carry their sort keys k1..k4; the caller builds the next cursor from them."""
    c1, c2, c3, c4 = after if after else (None, None, None, None)
    result = await session.execute(
        text(_LIST),
        {
            "me": me,
            "status": status,
            "priority": priority,
            "team": team,
            "contact": contact,
            "scope": scope,
            "q_like": q_like,
            "q_number": q_number,
            "sort": sort,
            "c1": c1,
            "c2": c2,
            "c3": c3,
            "c4": c4,
            "limit": limit,
        },
    )
    return result.mappings().all()


async def counts_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(text("SELECT status, count(*) FROM hd_ticket GROUP BY status"))
    return {str(status): int(n) for status, n in result.all()}


async def get_contact(session: AsyncSession, contact_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT c.id, c.name, c.email, c.vip, c.customer_since, "
            "(SELECT count(*) FROM hd_ticket t WHERE t.contact_id = c.id) AS ticket_count "
            "FROM hd_contact c WHERE c.id = :id"
        ),
        {"id": contact_id},
    )
    return result.mappings().first()


async def update_contact(
    session: AsyncSession, contact_id: UUID, *, name: str | None, vip: bool | None
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_contact SET name = coalesce(:name, name), vip = coalesce(:vip, vip), "
            "updated_at = now() WHERE id = :id RETURNING id"
        ),
        {"id": contact_id, "name": name, "vip": vip},
    )
    return result.first() is not None


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
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_member_id, "
            "author_name) VALUES (:ws, :ticket, :kind, :body, :author, :name) RETURNING id"
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
    return UUID(str(result.scalar_one()))


async def messages(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    before: tuple[datetime, UUID] | None = None,
    limit: int,
) -> Sequence[Row]:
    """Newest first, starting after `before` (the last row of the previous page)."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT m.id, m.kind, m.body, m.author_member_id, m.author_name, m.created_at, "
            "coalesce((SELECT array_agg(x.member_id ORDER BY x.created_at, x.member_id) "
            "FROM hd_mention x WHERE x.message_id = m.id), '{}') AS mention_ids "
            "FROM hd_message m WHERE m.ticket_id = :id "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (m.created_at, m.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
        ),
        {"id": ticket_id, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def member_brief(session: AsyncSession, member_id: UUID) -> Row | None:
    result = await session.execute(
        text("SELECT id, name, role, is_active FROM member WHERE id = :id"), {"id": member_id}
    )
    return result.mappings().first()
