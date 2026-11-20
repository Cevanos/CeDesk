"""SQL for contacts (feature-gap plan phase 2): the list, one contact, notes, attribute
values, the activity timeline, merge and CSV. Run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

# Per contact: its live tickets, how many aren't resolved, and its latest activity (a
# ticket changed, or the contact was made). LATERAL, so a page in name order reads only
# the tickets of the contacts on it.
_STATS = """
    LEFT JOIN LATERAL (
        SELECT count(*) AS ticket_count,
               count(*) FILTER (WHERE t.status <> 'resolved') AS open_ticket_count,
               max(t.updated_at) AS last_ticket_at
        FROM hd_ticket t WHERE t.contact_id = c.id
    ) s ON true
"""
_COLUMNS = (
    "c.id, c.name, c.email, c.phone, c.organization, c.vip, c.customer_since, "
    "c.created_at, c.updated_at, c.erased_at, c.attributes, "
    "coalesce(s.ticket_count, 0) AS ticket_count, "
    "coalesce(s.open_ticket_count, 0) AS open_ticket_count, "
    "greatest(c.created_at, s.last_ticket_at) AS last_activity_at, "
    "ARRAY(SELECT a.email FROM hd_contact_alias a WHERE a.contact_id = c.id "
    "ORDER BY a.email) AS other_emails"
)
_FILTERS = """
    WHERE c.erased_at IS NULL
      AND (CAST(:vip AS boolean) IS NULL OR c.vip = CAST(:vip AS boolean))
      AND (CAST(:has_open AS boolean) IS NULL
           OR (coalesce(s.open_ticket_count, 0) > 0) = CAST(:has_open AS boolean))
      AND (CAST(:created_from AS timestamptz) IS NULL
           OR c.created_at >= CAST(:created_from AS timestamptz))
      AND (CAST(:created_to AS timestamptz) IS NULL
           OR c.created_at < CAST(:created_to AS timestamptz))
      AND (CAST(:attrs AS jsonb) IS NULL OR c.attributes @> CAST(:attrs AS jsonb))
      AND (CAST(:q_like AS text) IS NULL
           OR c.name ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR c.email ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR c.phone ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR c.organization ILIKE CAST(:q_like AS text) ESCAPE '!'
           OR EXISTS (SELECT 1 FROM hd_contact_alias a WHERE a.contact_id = c.id
                      AND a.email ILIKE CAST(:q_like AS text) ESCAPE '!'))
"""
# Each sort: the key expression, its SQL type, and whether it runs high to low. The id
# breaks ties in the same direction, so a page never skips or repeats a row.
SORTS: dict[str, tuple[str, str, bool]] = {
    "name": ("lower(c.name)", "text", False),
    "recent": ("greatest(c.created_at, s.last_ticket_at)", "timestamptz", True),
    "tickets": ("coalesce(s.ticket_count, 0)", "bigint", True),
}


def _list_sql(sort: str, *, paged: bool) -> str:
    """Fixed strings only: the sort's own expression and bound parameter names."""
    expr, kind, desc = SORTS[sort]
    op = "<" if desc else ">"
    order = "DESC" if desc else "ASC"
    after = (
        f" AND (CAST(:c_key AS {kind}) IS NULL OR ({expr}, c.id) {op} "
        f"(CAST(:c_key AS {kind}), CAST(:c_id AS uuid)))"
    )
    sql = (
        f"SELECT {_COLUMNS}, {expr} AS sort_key FROM hd_contact c {_STATS} {_FILTERS}"  # noqa: S608
        + after
        + f" ORDER BY {expr} {order}, c.id {order}"
    )
    return sql + (" LIMIT :limit" if paged else "")


def _params(filters: Mapping[str, Any]) -> dict[str, Any]:
    start: date | None = filters.get("created_from")
    end: date | None = filters.get("created_to")
    attrs = filters.get("attrs")
    return {
        "vip": filters.get("vip"),
        "has_open": filters.get("has_open"),
        "created_from": datetime.combine(start, time.min, UTC) if start else None,
        "created_to": datetime.combine(end + timedelta(days=1), time.min, UTC) if end else None,
        "attrs": json.dumps(attrs) if attrs else None,
        "q_like": filters.get("q_like"),
    }


async def list_contacts(
    session: AsyncSession,
    *,
    filters: Mapping[str, Any],
    sort: str,
    after: tuple[Any, UUID] | None,
    limit: int | None,
) -> Sequence[Row]:
    """`filters`: vip, has_open, created_from, created_to (dates), attrs (dict), q_like.
    `limit` None: every match (the export, segments' counts)."""
    key, row_id = after if after else (None, None)
    params = _params(filters) | {"c_key": key, "c_id": row_id}
    if limit is not None:
        params["limit"] = limit
    result = await session.execute(text(_list_sql(sort, paged=limit is not None)), params)
    return result.mappings().all()


async def count_contacts(session: AsyncSession, *, filters: Mapping[str, Any]) -> int:
    sql = f"SELECT count(*) FROM hd_contact c {_STATS} {_FILTERS}"  # noqa: S608 (constants)
    result = await session.execute(text(sql), _params(filters))
    return int(result.scalar_one())


def segment_from() -> str:
    """`FROM hd_contact c ... WHERE <the list's filters>`, for SQL that works on a
    segment's contacts (campaign recipients). Use with `segment_params`."""
    return f"FROM hd_contact c {_STATS} {_FILTERS}"


def segment_params(filters: Mapping[str, Any]) -> dict[str, Any]:
    return _params(filters)


async def contact_matches(
    session: AsyncSession, contact_id: UUID, *, filters: Mapping[str, Any]
) -> bool:
    """Whether one contact passes the list's filters (a segment's, for automation rules):
    the same SQL as the list, so a rule and the segment page always agree."""
    sql = (
        f"SELECT EXISTS (SELECT 1 FROM hd_contact c {_STATS} {_FILTERS} "  # noqa: S608
        "AND c.id = CAST(:contact_id AS uuid))"
    )
    result = await session.execute(text(sql), _params(filters) | {"contact_id": contact_id})
    return bool(result.scalar_one())


async def get(session: AsyncSession, contact_id: UUID, *, lock: bool = False) -> Row | None:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS}, "  # noqa: S608 (constants)
            "(SELECT round(avg(score), 1) FROM hd_csat WHERE contact_id = c.id) AS csat_average, "
            "(SELECT count(*) FROM hd_csat WHERE contact_id = c.id) AS csat_count, "
            "(SELECT count(*) FROM hd_contact_note n WHERE n.contact_id = c.id) AS note_count "
            f"FROM hd_contact c {_STATS} WHERE c.id = :id" + (" FOR UPDATE OF c" if lock else "")
        ),
        {"id": contact_id},
    )
    return result.mappings().first()


async def lock(session: AsyncSession, contact_ids: Sequence[UUID]) -> None:
    """Both contacts of a merge, in id order (two merges the other way can't deadlock)."""
    await session.execute(
        text("SELECT id FROM hd_contact WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"),
        {"ids": list(contact_ids)},
    )


async def update_profile(
    session: AsyncSession,
    contact_id: UUID,
    *,
    phone: str | None,
    organization: str | None,
    set_phone: bool,
    set_organization: bool,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_contact SET "
            "phone = CASE WHEN :sp THEN CAST(:phone AS text) ELSE phone END, "
            "organization = CASE WHEN :so THEN CAST(:org AS text) ELSE organization END, "
            "updated_at = now() WHERE id = :id"
        ),
        {
            "id": contact_id,
            "phone": phone,
            "org": organization,
            "sp": set_phone,
            "so": set_organization,
        },
    )


async def set_attributes(
    session: AsyncSession, contact_id: UUID, values: Mapping[str, Any]
) -> None:
    await session.execute(
        text(
            "UPDATE hd_contact SET attributes = CAST(:a AS jsonb), updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": contact_id, "a": json.dumps(dict(values))},
    )


# ---- notes (2.2) ----------------------------------------------------------------------

_NOTE = (
    "SELECT n.id, n.contact_id, n.author_id, coalesce(m.name, 'Former member') AS author_name, "
    "n.body, n.created_at, n.updated_at "
    "FROM hd_contact_note n LEFT JOIN member m ON m.id = n.author_id "
)


async def notes(
    session: AsyncSession,
    contact_id: UUID,
    *,
    after: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[Row]:
    """Newest first."""
    at, row_id = after if after else (None, None)
    result = await session.execute(
        text(
            _NOTE + "WHERE n.contact_id = :c AND (CAST(:at AS timestamptz) IS NULL "
            "OR (n.created_at, n.id) < (CAST(:at AS timestamptz), CAST(:rid AS uuid))) "
            "ORDER BY n.created_at DESC, n.id DESC LIMIT :limit"
        ),
        {"c": contact_id, "at": at, "rid": row_id, "limit": limit},
    )
    return result.mappings().all()


async def note(session: AsyncSession, contact_id: UUID, note_id: UUID) -> Row | None:
    result = await session.execute(
        text(_NOTE + "WHERE n.id = :id AND n.contact_id = :c"), {"id": note_id, "c": contact_id}
    )
    return result.mappings().first()


async def insert_note(
    session: AsyncSession, workspace_id: UUID, contact_id: UUID, author_id: UUID, body: str
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_contact_note (workspace_id, contact_id, author_id, body) "
            "VALUES (:ws, :c, :a, :b) RETURNING id"
        ),
        {"ws": workspace_id, "c": contact_id, "a": author_id, "b": body},
    )
    return UUID(str(result.scalar_one()))


async def update_note(session: AsyncSession, note_id: UUID, body: str) -> None:
    await session.execute(
        text("UPDATE hd_contact_note SET body = :b, updated_at = now() WHERE id = :id"),
        {"id": note_id, "b": body},
    )


async def delete_note(session: AsyncSession, note_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_contact_note WHERE id = :id RETURNING id"), {"id": note_id}
    )
    return result.first() is not None


# ---- activity timeline (2.7) ----------------------------------------------------------

# One row per thing that happened, from the tables that already hold it. Ticket items are
# limited to the teams the caller may see (`vt`: a team-only role's team). Archived
# tickets (md O3) give their created and resolved times. Message items are the public
# conversation only (customer, agent and AI messages; never notes or system lines).
_TIMELINE = """
SELECT * FROM (
    SELECT t.created_at AS at, 'ticket_created' AS kind, t.id AS id, t.id AS ticket_id,
           t.number, t.subject, NULL::text AS author, NULL::text AS body,
           NULL::integer AS score, t.channel AS extra
    FROM hd_ticket t WHERE t.contact_id = :c
      AND (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))
    UNION ALL
    SELECT t.resolved_at, 'ticket_resolved', t.id, t.id, t.number, t.subject, NULL, NULL,
           NULL, NULL
    FROM hd_ticket t WHERE t.contact_id = :c AND t.resolved_at IS NOT NULL
      AND (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))
    UNION ALL
    SELECT a.created_at, 'ticket_created', a.id, a.id, a.number, a.subject, NULL, NULL,
           NULL, a.channel
    FROM hd_ticket_archive a WHERE a.contact_id = :c
      AND (CAST(:vt AS uuid) IS NULL OR a.team_id = CAST(:vt AS uuid))
    UNION ALL
    SELECT a.resolved_at, 'ticket_resolved', a.id, a.id, a.number, a.subject, NULL, NULL,
           NULL, NULL
    FROM hd_ticket_archive a WHERE a.contact_id = :c AND a.resolved_at IS NOT NULL
      AND (CAST(:vt AS uuid) IS NULL OR a.team_id = CAST(:vt AS uuid))
    UNION ALL
    SELECT m.created_at, 'message', m.id, t.id, t.number, t.subject,
           coalesce(mem.name, m.author_name), left(m.body, 1000), NULL, m.kind
    FROM hd_message m JOIN hd_ticket t ON t.id = m.ticket_id
    LEFT JOIN member mem ON mem.id = m.author_member_id
    WHERE t.contact_id = :c AND m.kind IN ('customer', 'agent', 'ai')
      AND (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))
    UNION ALL
    SELECT r.rated_at, 'csat', r.id, r.ticket_id, t.number, t.subject, NULL,
           r.comment, r.score, NULL
    FROM hd_csat r LEFT JOIN hd_ticket t ON t.id = r.ticket_id
    WHERE r.contact_id = :c
      AND (CAST(:vt AS uuid) IS NULL OR t.team_id = CAST(:vt AS uuid))
    UNION ALL
    SELECT n.created_at, 'note', n.id, NULL, NULL, NULL,
           coalesce(mem.name, 'Former member'), left(n.body, 1000), NULL, NULL
    FROM hd_contact_note n LEFT JOIN member mem ON mem.id = n.author_id
    WHERE n.contact_id = :c
    UNION ALL
    SELECT g.created_at, 'merge', g.id, NULL, NULL, NULL,
           coalesce(mem.name, 'Former member'), g.merged_name, NULL, g.merged_email
    FROM hd_contact_merge g LEFT JOIN member mem ON mem.id = g.merged_by
    WHERE g.contact_id = :c
) x
WHERE (CAST(:kinds AS text[]) IS NULL OR x.kind = ANY(CAST(:kinds AS text[])))
  AND (CAST(:at AS timestamptz) IS NULL
       OR (x.at, x.kind, x.id) < (CAST(:at AS timestamptz), CAST(:kind AS text),
                                  CAST(:rid AS uuid)))
ORDER BY x.at DESC, x.kind DESC, x.id DESC
LIMIT :limit
"""


async def timeline(
    session: AsyncSession,
    contact_id: UUID,
    *,
    visible_team: UUID | None,
    kinds: Sequence[str] | None,
    after: tuple[datetime, str, UUID] | None,
    limit: int,
) -> Sequence[Row]:
    at, kind, row_id = after if after else (None, None, None)
    result = await session.execute(
        text(_TIMELINE),
        {
            "c": contact_id,
            "vt": visible_team,
            "kinds": list(kinds) if kinds else None,
            "at": at,
            "kind": kind,
            "rid": row_id,
            "limit": limit,
        },
    )
    return result.mappings().all()


# ---- merge (2.4) ----------------------------------------------------------------------


async def _count(session: AsyncSession, sql: str, params: Mapping[str, Any]) -> int:
    result = await session.execute(text(sql), dict(params))
    return int(result.scalar_one())


async def merge_counts(session: AsyncSession, duplicate_id: UUID) -> dict[str, int]:
    """What a merge would move from the duplicate."""
    p = {"d": duplicate_id}
    return {
        "tickets": await _count(session, "SELECT count(*) FROM hd_ticket WHERE contact_id = :d", p),
        "archived_tickets": await _count(
            session, "SELECT count(*) FROM hd_ticket_archive WHERE contact_id = :d", p
        ),
        "ratings": await _count(session, "SELECT count(*) FROM hd_csat WHERE contact_id = :d", p),
        "notes": await _count(
            session, "SELECT count(*) FROM hd_contact_note WHERE contact_id = :d", p
        ),
        "other_emails": await _count(
            session, "SELECT count(*) FROM hd_contact_alias WHERE contact_id = :d", p
        ),
        "portal_sessions": await _count(
            session,
            "SELECT count(*) FROM hd_portal_session WHERE contact_id = :d "
            "AND revoked_at IS NULL AND expires_at > now()",
            p,
        ),
        "privacy_requests": await _count(
            session, "SELECT count(*) FROM hd_privacy_request WHERE contact_id = :d", p
        ),
    }


async def lock_tickets(session: AsyncSession, contact_ids: Sequence[UUID]) -> None:
    """The tickets of both contacts, before the contacts: the order a ticket change takes."""
    await session.execute(
        text("SELECT id FROM hd_ticket WHERE contact_id = ANY(:ids) ORDER BY id FOR UPDATE"),
        {"ids": list(contact_ids)},
    )


async def _changed(session: AsyncSession, sql: str, params: Mapping[str, Any]) -> int:
    result = await session.execute(text(sql), dict(params))
    return int(cast("CursorResult[Any]", result).rowcount)


async def move_everything(
    session: AsyncSession, duplicate_id: UUID, survivor_id: UUID
) -> dict[str, int]:
    """Everything that names the duplicate now names the survivor. The duplicate's portal
    sign-ins are not moved: they end with its row (they were for the other address)."""
    p = {"d": duplicate_id, "s": survivor_id}
    return {
        "tickets": await _changed(
            session, "UPDATE hd_ticket SET contact_id = :s WHERE contact_id = :d", p
        ),
        "archived_tickets": await _changed(
            session, "UPDATE hd_ticket_archive SET contact_id = :s WHERE contact_id = :d", p
        ),
        "ratings": await _changed(
            session, "UPDATE hd_csat SET contact_id = :s WHERE contact_id = :d", p
        ),
        "notes": await _changed(
            session, "UPDATE hd_contact_note SET contact_id = :s WHERE contact_id = :d", p
        ),
        "other_emails": await _changed(
            session, "UPDATE hd_contact_alias SET contact_id = :s WHERE contact_id = :d", p
        ),
        "merge_history": await _changed(
            session, "UPDATE hd_contact_merge SET contact_id = :s WHERE contact_id = :d", p
        ),
        # A later run of an import finds the survivor for the duplicate's source id.
        "import_links": await _changed(
            session,
            "UPDATE hd_import_map SET local_id = :s WHERE kind = 'contact' AND local_id = :d",
            p,
        ),
        # Retention exports (md O2) that name the duplicate name the survivor, so erasing
        # the survivor still deletes them.
        "retention_exports": await _changed(
            session,
            "UPDATE hd_retention_export SET contact_ids = array_replace(contact_ids, "
            "CAST(:d AS uuid), CAST(:s AS uuid)) WHERE CAST(:d AS uuid) = ANY(contact_ids)",
            p,
        ),
    }


async def delete_contact(session: AsyncSession, contact_id: UUID) -> None:
    """Its portal sign-ins and live-update tickets go with it."""
    await session.execute(
        text("DELETE FROM hd_stream_ticket WHERE contact_id = :c"), {"c": contact_id}
    )
    await session.execute(text("DELETE FROM hd_contact WHERE id = :c"), {"c": contact_id})


async def add_alias(
    session: AsyncSession, workspace_id: UUID, contact_id: UUID, email: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_contact_alias (workspace_id, email, contact_id) VALUES (:ws, :e, :c) "
            "ON CONFLICT (workspace_id, email) DO UPDATE SET contact_id = EXCLUDED.contact_id"
        ),
        {"ws": workspace_id, "e": email, "c": contact_id},
    )


async def merge_profile(
    session: AsyncSession,
    survivor_id: UUID,
    *,
    phone: str | None,
    organization: str | None,
    vip: bool,
    customer_since: datetime,
    attributes: Mapping[str, Any],
) -> None:
    await session.execute(
        text(
            "UPDATE hd_contact SET phone = :phone, organization = :org, vip = :vip, "
            "customer_since = :since, attributes = CAST(:a AS jsonb), updated_at = now() "
            "WHERE id = :id"
        ),
        {
            "id": survivor_id,
            "phone": phone,
            "org": organization,
            "vip": vip,
            "since": customer_since,
            "a": json.dumps(dict(attributes)),
        },
    )


async def record_merge(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    survivor_id: UUID,
    duplicate: Mapping[Any, Any],
    merged_by: UUID,
    moved: Mapping[str, int],
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_contact_merge (workspace_id, contact_id, merged_id, merged_name, "
            "merged_email, merged_by, moved) "
            "VALUES (:ws, :c, :m, :name, :email, :by, CAST(:moved AS jsonb))"
        ),
        {
            "ws": workspace_id,
            "c": survivor_id,
            "m": duplicate["id"],
            "name": duplicate["name"],
            "email": duplicate["email"],
            "by": merged_by,
            "moved": json.dumps(dict(moved)),
        },
    )


# ---- CSV import (2.5) -----------------------------------------------------------------


async def by_emails(session: AsyncSession, emails: Sequence[str]) -> dict[str, Row]:
    """Existing contacts for these addresses, by their own email or another one merged
    into them (keyed by the address asked for)."""
    result = await session.execute(
        text(
            "SELECT e.email AS asked, c.id, c.name, c.phone, c.organization, c.vip, "
            "c.attributes, c.erased_at "
            "FROM unnest(CAST(:emails AS text[])) e(email) "
            "JOIN hd_contact c ON c.email = e.email OR c.id = "
            "(SELECT a.contact_id FROM hd_contact_alias a WHERE a.email = e.email)"
        ),
        {"emails": list(emails)},
    )
    return {str(r["asked"]): r for r in result.mappings().all()}


async def insert_contact(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    email: str,
    name: str,
    phone: str | None,
    organization: str | None,
    vip: bool,
    attributes: Mapping[str, Any],
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_contact (workspace_id, email, name, phone, organization, vip, "
            "attributes) VALUES (:ws, :email, :name, :phone, :org, :vip, CAST(:a AS jsonb)) "
            "RETURNING id"
        ),
        {
            "ws": workspace_id,
            "email": email,
            "name": name,
            "phone": phone,
            "org": organization,
            "vip": vip,
            "a": json.dumps(dict(attributes)),
        },
    )
    return UUID(str(result.scalar_one()))


async def update_imported(
    session: AsyncSession,
    contact_id: UUID,
    *,
    name: str,
    phone: str | None,
    organization: str | None,
    vip: bool,
    attributes: Mapping[str, Any],
) -> None:
    await session.execute(
        text(
            "UPDATE hd_contact SET name = :name, phone = :phone, organization = :org, "
            "vip = :vip, attributes = CAST(:a AS jsonb), updated_at = now() WHERE id = :id"
        ),
        {
            "id": contact_id,
            "name": name,
            "phone": phone,
            "org": organization,
            "vip": vip,
            "a": json.dumps(dict(attributes)),
        },
    )


# ---- privacy (md S11) -----------------------------------------------------------------


async def notes_for_export(session: AsyncSession, contact_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(_NOTE + "WHERE n.contact_id = :c ORDER BY n.created_at, n.id"), {"c": contact_id}
    )
    return result.mappings().all()


async def merges_for_export(session: AsyncSession, contact_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT merged_id, merged_name, merged_email, created_at FROM hd_contact_merge "
            "WHERE contact_id = :c ORDER BY created_at"
        ),
        {"c": contact_id},
    )
    return result.mappings().all()


async def aliases(session: AsyncSession, contact_id: UUID) -> list[str]:
    result = await session.execute(
        text("SELECT email FROM hd_contact_alias WHERE contact_id = :c ORDER BY email"),
        {"c": contact_id},
    )
    return [str(r[0]) for r in result.all()]


async def erase(session: AsyncSession, contact_id: UUID) -> dict[str, int]:
    """The phase 2 data about one customer: notes, other addresses, attribute values and
    the names and addresses kept in their merge history (the merges stay, as counts)."""
    p = {"c": contact_id}
    return {
        "contact_notes": await _changed(
            session, "DELETE FROM hd_contact_note WHERE contact_id = :c", p
        ),
        "other_emails": await _changed(
            session, "DELETE FROM hd_contact_alias WHERE contact_id = :c", p
        ),
        "attributes": await _changed(
            session,
            "UPDATE hd_contact SET attributes = '{}' WHERE id = :c AND attributes <> '{}'",
            p,
        ),
        "merge_history": await _changed(
            session,
            "UPDATE hd_contact_merge SET merged_name = NULL, merged_email = NULL "
            "WHERE contact_id = :c AND (merged_name IS NOT NULL OR merged_email IS NOT NULL)",
            p,
        ),
    }
