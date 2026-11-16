"""SQL for plan limits, usage and retention (md O2, O4, O5). RLS applies, except for the
plan itself, which only the server's command line sets."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo.auth import TAKES_TICKETS


async def plan_name(session: AsyncSession) -> str | None:
    result = await session.execute(text("SELECT plan FROM hd_workspace_plan"))
    value = result.scalar_one_or_none()
    return str(value) if value is not None else None


async def set_plan(session: AsyncSession, workspace_id: UUID, plan: str | None) -> None:
    if plan is None:
        await session.execute(text("DELETE FROM hd_workspace_plan"))
        return
    await session.execute(
        text(
            "INSERT INTO hd_workspace_plan (workspace_id, plan) VALUES (:ws, :plan) "
            "ON CONFLICT (workspace_id) DO UPDATE SET plan = EXCLUDED.plan, updated_at = now()"
        ),
        {"ws": workspace_id, "plan": plan},
    )


async def agents(session: AsyncSession) -> int:
    result = await session.execute(
        text(f"SELECT count(*) FROM member m WHERE m.is_active AND {TAKES_TICKETS}")  # noqa: S608
    )
    return int(result.scalar_one())


async def tickets_since(session: AsyncSession, since: datetime, imported: Sequence[str]) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_ticket WHERE created_at >= :since "
            "AND NOT coalesce(source = ANY(CAST(:imported AS text[])), false)"
        ),
        {"since": since, "imported": list(imported)},
    )
    return int(result.scalar_one())


async def storage_bytes(session: AsyncSession) -> int:
    """Every stored file: on live tickets, and on archived ones (md O3; their total is kept
    with them, and an erase, which deletes the files, sets it to 0)."""
    result = await session.execute(
        text(
            "SELECT (SELECT coalesce(sum(size), 0) FROM hd_attachment) "
            "+ (SELECT coalesce(sum(file_bytes), 0) FROM hd_ticket_archive)"
        )
    )
    return int(result.scalar_one())


async def retention(session: AsyncSession) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT r.closed_ticket_days, r.export_before_delete, r.updated_at, "
            "m.name AS updated_by_name "
            "FROM hd_retention r LEFT JOIN member m ON m.id = r.updated_by"
        )
    )
    return result.mappings().first()


async def save_retention(
    session: AsyncSession,
    workspace_id: UUID,
    days: int | None,
    by: UUID,
    *,
    export: bool | None = None,
) -> None:
    """`export` None keeps the saved switch (off the first time)."""
    await session.execute(
        text(
            "INSERT INTO hd_retention (workspace_id, closed_ticket_days, updated_by, "
            "export_before_delete) VALUES (:ws, :days, :by, coalesce(:export, false)) "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "closed_ticket_days = EXCLUDED.closed_ticket_days, "
            "export_before_delete = coalesce(:export, hd_retention.export_before_delete), "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"ws": workspace_id, "days": days, "by": by, "export": export},
    )


async def resolved_before(
    session: AsyncSession, cutoff: datetime, limit: int
) -> Sequence[RowMapping]:
    """Resolved tickets older than the cutoff, with their files' storage keys.

    An imported ticket keeps its old help desk's dates, so it counts from when it came in
    (the latest time an import of its source ran), not from when it was resolved there:
    importing years of history never has it deleted the next day."""
    result = await session.execute(
        text(
            "WITH imported AS (SELECT source, max(greatest(started_at, "
            "coalesce(finished_at, started_at), coalesce(last_sync_at, started_at))) AS at "
            "FROM hd_import_run GROUP BY source) "
            "SELECT t.id, coalesce(array_agg(a.object_key) FILTER (WHERE a.object_key IS NOT "
            "NULL), '{}') AS keys FROM hd_ticket t LEFT JOIN imported i ON i.source = t.source "
            "LEFT JOIN hd_attachment a ON a.ticket_id = t.id "
            "WHERE t.status = 'resolved' AND greatest(t.resolved_at, i.at) < :cutoff "
            "GROUP BY t.id ORDER BY min(t.resolved_at) LIMIT :limit"
        ),
        {"cutoff": cutoff, "limit": limit},
    )
    return result.mappings().all()


async def delete_ticket_events(session: AsyncSession, ticket_ids: Sequence[UUID]) -> None:
    """The events and webhook deliveries of these tickets (live or archived): they name the
    ticket, with its subject, without a foreign key."""
    ids = {"ids": list(ticket_ids), "texts": [str(i) for i in ticket_ids]}
    await session.execute(
        text("DELETE FROM hd_webhook_delivery WHERE body->'ticket'->>'id' = ANY(:texts)"), ids
    )
    await session.execute(
        text("DELETE FROM hd_outbox WHERE ticket_id = ANY(CAST(:ids AS uuid[]))"), ids
    )


async def delete_tickets(session: AsyncSession, ticket_ids: Sequence[UUID]) -> int:
    """Their messages, files' rows, tasks and the rest go with them (ON DELETE CASCADE),
    and their events and webhook deliveries (`delete_ticket_events`)."""
    await delete_ticket_events(session, ticket_ids)
    result = await session.execute(
        text("DELETE FROM hd_ticket WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": list(ticket_ids)},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ---- export before delete (O2) -------------------------------------------------------


async def tickets_for_export(session: AsyncSession, ids: Sequence[UUID]) -> Sequence[RowMapping]:
    """As the privacy export reads a ticket, with its customer."""
    result = await session.execute(
        text(
            "SELECT t.id, t.number, t.subject, t.status, t.priority, t.channel, t.fields, "
            "t.tags, t.created_at, t.resolved_at, ty.name AS type_name, t.contact_id, "
            "c.email AS contact_email, c.name AS contact_name "
            "FROM hd_ticket t LEFT JOIN hd_ticket_type ty ON ty.id = t.type_id "
            "LEFT JOIN hd_contact c ON c.id = t.contact_id "
            "WHERE t.id = ANY(:ids) ORDER BY t.number"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().all()


async def ratings_for_export(session: AsyncSession, ids: Sequence[UUID]) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT ticket_id, score, comment, source, rated_at FROM hd_csat "
            "WHERE ticket_id = ANY(:ids) ORDER BY rated_at"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().all()


async def insert_export(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    export_id: UUID,
    key: str,
    tickets: int,
    size: int,
    contact_ids: Sequence[UUID],
    expires_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_retention_export (id, workspace_id, object_key, tickets, size, "
            "contact_ids, expires_at) VALUES (:id, :ws, :key, :tickets, :size, "
            "CAST(:contacts AS uuid[]), :expires)"
        ),
        {
            "id": export_id,
            "ws": workspace_id,
            "key": key,
            "tickets": tickets,
            "size": size,
            "contacts": [str(c) for c in contact_ids],
            "expires": expires_at,
        },
    )


_EXPORT = "SELECT id, object_key, tickets, size, created_at, expires_at FROM hd_retention_export "


async def exports(session: AsyncSession, limit: int) -> Sequence[RowMapping]:
    result = await session.execute(
        text(_EXPORT + "ORDER BY created_at DESC, id DESC LIMIT :limit"), {"limit": limit}
    )
    return result.mappings().all()


async def export(session: AsyncSession, export_id: UUID) -> RowMapping | None:
    result = await session.execute(text(_EXPORT + "WHERE id = :id"), {"id": export_id})
    return result.mappings().first()


async def expired_exports(session: AsyncSession, now: datetime) -> Sequence[RowMapping]:
    result = await session.execute(text(_EXPORT + "WHERE expires_at <= :now"), {"now": now})
    return result.mappings().all()


async def forget_export(session: AsyncSession, export_id: UUID) -> None:
    await session.execute(text("DELETE FROM hd_retention_export WHERE id = :id"), {"id": export_id})


async def exports_naming(session: AsyncSession, contact_id: UUID) -> Sequence[RowMapping]:
    """The retention exports that hold this customer's tickets (privacy erase)."""
    result = await session.execute(text(_EXPORT + "WHERE :c = ANY(contact_ids)"), {"c": contact_id})
    return result.mappings().all()
