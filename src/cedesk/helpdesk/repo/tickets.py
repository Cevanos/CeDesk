"""SQL for tickets, their contacts and messages. Always run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo.attachments import MESSAGE_FILES

Row = RowMapping

_COLUMNS = """
           t.id, t.number, t.subject, t.status, t.priority, t.channel, t.fields, t.tags,
           t.type_id, ty.name AS type_name,
           t.team_id, tm.name AS team_name,
           t.assignee_id, a.name AS assignee_name,
           t.contact_id, c.name AS contact_name, c.email AS contact_email,
           c.vip AS contact_vip, c.customer_since AS contact_since,
           t.first_reply_at, t.resolved_at, t.reopen_count, t.created_at, t.updated_at,
           t.accepted, t.conversation_id, t.handover, t.topic, t.sentiment, t.language,
           t.source, t.source_id, t.parent_id, t.merged_into_id,
           (SELECT count(*) FROM hd_ticket ch WHERE ch.parent_id = t.id) AS child_count
"""

_FROM = """
    FROM hd_ticket t
    JOIN hd_contact c ON c.id = t.contact_id
    LEFT JOIN hd_ticket_type ty ON ty.id = t.type_id
    LEFT JOIN hd_team tm ON tm.id = t.team_id
    LEFT JOIN member a ON a.id = t.assignee_id
"""

# The detail view also carries the ticket's CSAT and the customer's average (md T19).
_CSAT = """,
           (SELECT to_jsonb(r) FROM (SELECT score, comment, source, rated_at FROM hd_csat
             WHERE ticket_id = t.id) r) AS csat,
           (SELECT round(avg(score), 1) FROM hd_csat WHERE contact_id = t.contact_id)
             AS contact_csat_average,
           (SELECT count(*) FROM hd_csat WHERE contact_id = t.contact_id) AS contact_csat_count
"""

_DETAIL = "SELECT " + _COLUMNS + _CSAT + _FROM

# The ticket list orders by plain key expressions so an index can serve each page:
# migration 0009 creates one index per sort on exactly these expressions (an index is
# used only when they match). Times become numbers with the immutable
# `extract(epoch FROM (t - fixed instant))`. Each sort is (keys, direction): every key
# runs the same direction, so "after the cursor" is one row comparison.
_EPOCH = "extract(epoch FROM ({} - TIMESTAMPTZ '1970-01-01 00:00:00+00'))"
_INT, _NUM = "integer", "numeric"

_PRIORITY_KEYS = (
    ("(CASE WHEN t.status = 'resolved' THEN 1 ELSE 0 END)", _INT),
    (
        "(CASE WHEN t.status <> 'resolved' "
        "THEN array_position(ARRAY['urgent', 'high', 'medium', 'low'], t.priority) ELSE 0 END)",
        _INT,
    ),
    (
        "(CASE WHEN t.status = 'resolved' THEN -" + _EPOCH.format("t.resolved_at") + " "
        "ELSE " + _EPOCH.format("t.created_at") + " END)",
        _NUM,
    ),
    ("(CASE WHEN t.status = 'resolved' THEN -t.number ELSE t.number END)", _INT),
)
# Must match migration 0019's index exactly. `sla_due_at` is kept by service.sla; a
# ticket from before 0019 that nobody touched yet falls back to its creation time.
_SLA_KEYS = (
    ("(CASE WHEN t.status = 'resolved' THEN 1 ELSE 0 END)", _INT),
    (
        "(CASE WHEN t.status = 'resolved' THEN -" + _EPOCH.format("t.resolved_at") + " "
        "ELSE " + _EPOCH.format("coalesce(t.sla_due_at, t.created_at)") + " END)",
        _NUM,
    ),
    ("(CASE WHEN t.status = 'resolved' THEN -t.number ELSE t.number END)", _INT),
)
_CREATED_KEYS = (("(" + _EPOCH.format("t.created_at") + ")", _NUM), ("t.number", _INT))
_UPDATED_KEYS = (("(" + _EPOCH.format("t.updated_at") + ")", _NUM), ("t.number", _INT))

# sort -> (keys, ascending)
SORT_KEYS: dict[str, tuple[tuple[tuple[str, str], ...], bool]] = {
    "priority": (_PRIORITY_KEYS, True),
    "sla": (_SLA_KEYS, True),
    "newest": (_CREATED_KEYS, False),
    "oldest": (_CREATED_KEYS, True),
    "updated": (_UPDATED_KEYS, False),
}

# One static query; every filter is "parameter is NULL or matches", so nothing is ever
# built from request data. `q_like` is already escaped with '!' by the caller. Search
# (T8) reads `search_text` (subject, field values and tags, kept by a trigger) and the
# contact's name and email.
_FILTERS = """
    WHERE (CAST(:status AS text) IS NULL OR t.status = CAST(:status AS text))
      AND (CAST(:priority AS text) IS NULL OR t.priority = CAST(:priority AS text))
      AND (CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))
      AND (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))
      AND (CAST(:contact AS uuid) IS NULL OR t.contact_id = CAST(:contact AS uuid))
      AND (CAST(:scope AS text) = 'everyone'
           OR (CAST(:scope AS text) = 'mine' AND t.assignee_id = CAST(:me AS uuid))
           OR (CAST(:scope AS text) = 'unassigned' AND t.assignee_id IS NULL))
      AND (CAST(:q_like AS text) IS NULL
           OR t.number = CAST(:q_number AS integer)
           OR t.search_text ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR t.contact_id IN (SELECT c2.id FROM hd_contact c2
                               WHERE (c2.name || E'\\n' || c2.email)
                                     ILIKE CAST(:q_like AS text) ESCAPE '!'))
"""


def _list_query(keys: tuple[tuple[str, str], ...], ascending: bool) -> str:
    """Fixed strings only: the key expressions above and bound parameter names."""
    selected = "".join(f", {expr} AS k{i}" for i, (expr, _) in enumerate(keys, 1))
    row = ", ".join(expr for expr, _ in keys)
    after = ", ".join(f"CAST(:c{i} AS {kind})" for i, (_, kind) in enumerate(keys, 1))
    direction = "ASC" if ascending else "DESC"
    order = ", ".join(f"{expr} {direction}" for expr, _ in keys)
    return (
        "SELECT "
        + _COLUMNS
        + selected
        + _FROM
        + _FILTERS
        + f"  AND (CAST(:c1 AS {keys[0][1]}) IS NULL OR ({row}) {'>' if ascending else '<'} "
        + f"({after}))\n"
        + f"    ORDER BY {order}\n    LIMIT :limit"
    )


_LIST = {sort: _list_query(keys, asc) for sort, (keys, asc) in SORT_KEYS.items()}


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
    handover = params.get("handover")
    params["handover"] = None if handover is None else json.dumps(handover)
    for key in (
        "conversation_id",
        "topic",
        "sentiment",
        "language",
        "source",
        "source_id",
        "parent_id",
    ):
        params.setdefault(key, None)
    params.setdefault("accepted", True)
    result = await session.execute(
        text(
            "INSERT INTO hd_ticket (workspace_id, number, subject, status, priority, type_id, "
            "team_id, assignee_id, contact_id, channel, fields, tags, created_by_id, "
            "conversation_id, accepted, handover, topic, sentiment, language, source, "
            "source_id, parent_id) "
            "VALUES (:workspace_id, :number, :subject, 'open', :priority, :type_id, :team_id, "
            ":assignee_id, :contact_id, :channel, CAST(:fields AS jsonb), :tags, :created_by_id, "
            ":conversation_id, :accepted, CAST(:handover AS jsonb), :topic, :sentiment, "
            ":language, :source, :source_id, :parent_id) "
            "RETURNING id"
        ),
        params,
    )
    return UUID(str(result.scalar_one()))


async def lock_ticket(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, number, subject, status, priority, channel, type_id, team_id, "
            "assignee_id, fields, tags, resolved_at, reopen_count, updated_at, parent_id, "
            "merged_into_id FROM hd_ticket WHERE id = :id FOR UPDATE"
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
    visible_team: UUID | None = None,
    scope: str,
    q_like: str | None,
    q_number: int | None,
    sort: str,
    after: Sequence[int | str] | None,
    limit: int,
) -> Sequence[Row]:
    """Rows carry their sort keys k1..kN; the caller builds the next cursor from them."""
    keys, _ = SORT_KEYS[sort]
    cursor = list(after) if after else [None] * len(keys)
    result = await session.execute(
        text(_LIST[sort]),
        {
            **{f"c{i}": value for i, value in enumerate(cursor, 1)},
            "me": me,
            "status": status,
            "priority": priority,
            "team": team,
            "contact": contact,
            "vt": visible_team,
            "scope": scope,
            "q_like": q_like,
            "q_number": q_number,
            "limit": limit,
        },
    )
    return result.mappings().all()


async def counts_by_status(
    session: AsyncSession, visible_team: UUID | None = None
) -> dict[str, int]:
    """`visible_team`: count only that team's tickets (a `team_only` role)."""
    result = await session.execute(
        text(
            "SELECT status, count(*) FROM hd_ticket t "
            "WHERE (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid)) GROUP BY status"
        ),
        {"vt": visible_team},
    )
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
    email: Mapping[str, Any] | None = None,
    channel_message_id: str | None = None,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_member_id, "
            "author_name, email, channel_message_id) VALUES (:ws, :ticket, :kind, :body, "
            ":author, :name, CAST(:email AS jsonb), :channel_id) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "kind": kind,
            "body": body,
            "author": author_member_id,
            "name": author_name,
            "email": None if email is None else json.dumps(dict(email)),
            "channel_id": channel_message_id,
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
            # Fixed strings only: MESSAGE_FILES is a constant.
            "SELECT m.id, m.kind, m.body, m.author_member_id, m.author_name, m.created_at, "  # noqa: S608
            "m.email, m.channel_message_id, "
            "(SELECT o.status FROM hd_email_out o WHERE o.message_id = m.id "
            " ORDER BY o.created_at DESC LIMIT 1) AS delivery, "
            "coalesce((SELECT array_agg(x.member_id ORDER BY x.created_at, x.member_id) "
            "FROM hd_mention x WHERE x.message_id = m.id), '{}') AS mention_ids, "
            + MESSAGE_FILES
            + " "
            "FROM hd_message m WHERE m.ticket_id = :id "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (m.created_at, m.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
        ),
        {"id": ticket_id, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def message_by_channel_id(
    session: AsyncSession, ticket_id: UUID, channel_message_id: str
) -> UUID | None:
    """A message already saved from that channel message (a retried delivery)."""
    result = await session.execute(
        text(
            "SELECT id FROM hd_message WHERE ticket_id = :ticket "
            "AND channel_message_id = :channel_id"
        ),
        {"ticket": ticket_id, "channel_id": channel_message_id},
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def member_brief(session: AsyncSession, member_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT m.id, m.name, m.role, m.is_active, "  # noqa: S608
            + auth_repo.ROLE_COLUMNS
            + " FROM member m "
            + auth_repo.ROLE_JOIN
            + "WHERE m.id = :id"
        ),
        {"id": member_id},
    )
    return result.mappings().first()


async def set_accepted(session: AsyncSession, ticket_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_ticket SET accepted = true, updated_at = now() WHERE id = :id"),
        {"id": ticket_id},
    )


async def ticket_team(session: AsyncSession, ticket_id: UUID) -> tuple[bool, UUID | None]:
    """(found, team_id) of a ticket, for checking a scoped role's access."""
    result = await session.execute(
        text("SELECT team_id FROM hd_ticket WHERE id = :id"), {"id": ticket_id}
    )
    row = result.first()
    return (False, None) if row is None else (True, row[0])
