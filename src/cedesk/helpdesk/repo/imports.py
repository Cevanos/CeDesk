"""SQL for imports (md section 8). Always inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.imports import Failure, LeaseLost

Row = RowMapping

_RUN = (
    "SELECT id, workspace_id, connection_id, source, state, options, cursor, counts, sync_on, "
    "last_sync_at, next_run_at, lease_until, last_error, started_by, started_at, finished_at, "
    "retry_attempts, lease_token, (SELECT count(*) FROM hd_import_failure f "
    "WHERE f.run_id = hd_import_run.id AND f.status = 'open') AS open_failures FROM hd_import_run"
)  # lease_token: for the worker's own checks; never in a response (`_run_view` picks fields)
_RUN_ONE = _RUN + " WHERE id = :id"
_RUN_LOCKED = _RUN_ONE + " FOR UPDATE"


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


_CONNECTION = (
    "SELECT id, workspace_id, source, label, settings, credentials_sealed, credentials_hint, "
    "state, created_by, created_at FROM hd_import_connection WHERE id = :id"
)
_CONNECTION_LOCKED = _CONNECTION + " FOR UPDATE"


async def connection(
    session: AsyncSession, connection_id: UUID, *, lock: bool = False
) -> Row | None:
    """`lock`: held until the transaction ends, so starting, resuming and closing runs of one
    connection happen one after the other."""
    result = await session.execute(
        text(_CONNECTION_LOCKED if lock else _CONNECTION), {"id": connection_id}
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
    sync_on: bool = False,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_import_run (workspace_id, connection_id, source, options, "
            "started_by, sync_on) VALUES (:ws, :conn, :source, CAST(:options AS jsonb), :by, "
            ":sync) RETURNING id"
        ),
        {
            "ws": workspace_id, "conn": connection_id, "source": source,
            "options": json.dumps(dict(options)), "by": started_by, "sync": sync_on,
        },
    )  # fmt: skip
    return UUID(str(result.scalar_one()))


async def run(session: AsyncSession, run_id: UUID, *, lock: bool = False) -> Row | None:
    """`lock`: held until the transaction ends (the worker ending an import, so a sync stop
    waits for it and then sees the run as it left it)."""
    result = await session.execute(text(_RUN_LOCKED if lock else _RUN_ONE), {"id": run_id})
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
            "UPDATE hd_import_run SET state = 'running', lease_until = :lease, "
            "lease_token = gen_random_uuid() "
            "WHERE id = (SELECT id FROM hd_import_run WHERE state IN ('queued', 'running') "
            "AND next_run_at <= :now AND (lease_until IS NULL OR lease_until <= :now) "
            "ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, workspace_id, connection_id, source, state, options, cursor, counts, "
            "sync_on, last_sync_at, next_run_at, lease_until, last_error, started_by, "
            "started_at, finished_at, retry_attempts, lease_token"
        ),
        {"now": now, "lease": lease_until},
    )
    return result.mappings().first()


async def syncing_run(session: AsyncSession, connection_id: UUID) -> Row | None:
    """The finished run of this connection that is still syncing (M9), if any."""
    result = await session.execute(
        text(_RUN + " WHERE connection_id = :c AND state = 'done' AND sync_on LIMIT 1"),
        {"c": connection_id},
    )
    return result.mappings().first()


async def claim_sync(session: AsyncSession, now: datetime, lease_until: datetime) -> Row | None:
    """Takes the syncing run that is due, leases it and leaves its state alone (`done`). Same
    locking as `claim_due`."""
    result = await session.execute(
        text(
            "UPDATE hd_import_run SET lease_until = :lease, lease_token = gen_random_uuid() "
            "WHERE id = (SELECT id FROM hd_import_run WHERE state = 'done' AND sync_on "
            "AND next_run_at <= :now AND (lease_until IS NULL OR lease_until <= :now) "
            "ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, workspace_id, connection_id, source, state, options, cursor, counts, "
            "sync_on, last_sync_at, next_run_at, lease_until, last_error, started_by, "
            "started_at, finished_at, retry_attempts, lease_token"
        ),
        {"now": now, "lease": lease_until},
    )
    return result.mappings().first()


async def _owned(session: AsyncSession, sql: str, values: Mapping[str, Any]) -> None:
    """A worker's write about its run (`sql` ends with `WHERE id = :id`): it applies only
    while the run carries the worker's `token`, else `LeaseLost` (the transaction is the
    caller's to roll back)."""
    result = await session.execute(text(sql + " AND lease_token = :token"), values)
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise LeaseLost("Another worker has taken this import run.")


async def finish_to_sync(
    session: AsyncSession,
    run_id: UUID,
    *,
    last_sync_at: datetime,
    next_run_at: datetime,
    counts: Mapping[str, Any],
    token: UUID,
) -> None:
    """The first import is done and the run goes on syncing: it is `done`, the credentials
    stay, and the next look at the source is `next_run_at`."""
    await _owned(
        session,
        "UPDATE hd_import_run SET state = 'done', lease_until = NULL, lease_token = NULL, "
        "finished_at = now(), last_error = NULL, cursor = '{}'::jsonb, "
        "counts = CAST(:counts AS jsonb), last_sync_at = :last, next_run_at = :next, "
        "retry_attempts = 0 WHERE id = :id",
        {
            "id": run_id, "last": last_sync_at, "next": next_run_at,
            "counts": json.dumps(dict(counts)), "token": token,
        },
    )  # fmt: skip


async def sync_pass_done(
    session: AsyncSession,
    run_id: UUID,
    *,
    last_sync_at: datetime,
    next_run_at: datetime,
    counts: Mapping[str, Any],
    token: UUID,
) -> None:
    await _owned(
        session,
        "UPDATE hd_import_run SET lease_until = NULL, lease_token = NULL, last_error = NULL, "
        "cursor = '{}'::jsonb, counts = CAST(:counts AS jsonb), last_sync_at = :last, "
        "next_run_at = :next, retry_attempts = 0 WHERE id = :id",
        {
            "id": run_id, "last": last_sync_at, "next": next_run_at,
            "counts": json.dumps(dict(counts)), "token": token,
        },
    )  # fmt: skip


async def sync_error(
    session: AsyncSession, run_id: UUID, *, next_run_at: datetime, error: str, token: UUID
) -> None:
    """A sync pass that could not finish: the run stays syncing and is tried again later,
    with a fresh count of tries."""
    await _owned(
        session,
        "UPDATE hd_import_run SET lease_until = NULL, lease_token = NULL, next_run_at = :at, "
        "last_error = :error, retry_attempts = 0 WHERE id = :id",
        {"id": run_id, "at": next_run_at, "error": error[:500], "token": token},
    )


async def sync_now(session: AsyncSession, run_id: UUID) -> bool:
    """Makes a syncing run due now. False when the run isn't syncing."""
    result = await session.execute(
        text(
            "UPDATE hd_import_run SET next_run_at = now() "
            "WHERE id = :id AND state = 'done' AND sync_on RETURNING id"
        ),
        {"id": run_id},
    )
    return result.first() is not None


async def stop_sync(session: AsyncSession, run_id: UUID) -> str | None:
    """Turns syncing off and returns the run's state as it is now (None: it was already off).
    The lease is left alone: a worker on the run (the first import, or a look) keeps it until
    it is done, so no other worker can take the run meanwhile. The state is read by this
    UPDATE, after any worker that held the row committed, so the caller decides on the run
    as it really is."""
    result = await session.execute(
        text("UPDATE hd_import_run SET sync_on = false WHERE id = :id AND sync_on RETURNING state"),
        {"id": run_id},
    )
    found = result.first()
    return str(found[0]) if found is not None else None


async def save_progress(
    session: AsyncSession,
    run_id: UUID,
    *,
    cursor: Mapping[str, Any],
    counts: Mapping[str, Any],
    lease_until: datetime,
    token: UUID,
) -> None:
    """The cursor and counts, in the same transaction as the page they describe (and the
    failures noted on it). A page saved ends the tries of the one before. Only by the worker
    that holds the run (`LeaseLost` otherwise: the page's records roll back with it)."""
    await _owned(
        session,
        "UPDATE hd_import_run SET cursor = CAST(:cursor AS jsonb), "
        "counts = CAST(:counts AS jsonb), lease_until = :lease, retry_attempts = 0 "
        "WHERE id = :id",
        {
            "id": run_id, "cursor": json.dumps(dict(cursor)), "counts": json.dumps(dict(counts)),
            "lease": lease_until, "token": token,
        },
    )  # fmt: skip


async def release(
    session: AsyncSession,
    run_id: UUID,
    *,
    next_run_at: datetime,
    token: UUID,
    error: str | None = None,
) -> None:
    """Gives the run back to be taken again at `next_run_at` (a pause, or end of a slice)."""
    await _owned(
        session,
        "UPDATE hd_import_run SET lease_until = NULL, lease_token = NULL, next_run_at = :at, "
        "last_error = :error WHERE id = :id",
        {"id": run_id, "at": next_run_at, "error": error[:500] if error else None, "token": token},
    )


async def resume(session: AsyncSession, run_id: UUID) -> bool:
    """A failed run is queued again where it stopped: its cursor, counts, options, sync
    setting and start time stay; its tries start from 0. Only from `failed` and with no live
    lease, in one statement, so two resumes (or a worker) can't both take it. False when the
    run was not failed any more."""
    result = await session.execute(
        text(
            "UPDATE hd_import_run SET state = 'queued', next_run_at = now(), lease_until = NULL, "
            "lease_token = NULL, finished_at = NULL, last_error = NULL, retry_attempts = 0 "
            "WHERE id = :id AND state = 'failed' "
            "AND (lease_until IS NULL OR lease_until <= now()) RETURNING id"
        ),
        {"id": run_id},
    )
    return result.first() is not None


async def retry_later(
    session: AsyncSession,
    run_id: UUID,
    *,
    next_run_at: datetime,
    error: str,
    attempts: int,
    token: UUID,
) -> None:
    """A try of the page failed with a temporary error: the run waits until `next_run_at`
    and remembers it has had `attempts` failed tries (so a restart doesn't reset them)."""
    await _owned(
        session,
        "UPDATE hd_import_run SET lease_until = NULL, lease_token = NULL, next_run_at = :at, "
        "last_error = :error, retry_attempts = :attempts WHERE id = :id",
        {
            "id": run_id, "at": next_run_at, "error": error[:500], "attempts": attempts,
            "token": token,
        },
    )  # fmt: skip


async def finish(
    session: AsyncSession, run_id: UUID, *, state: str, token: UUID, error: str | None = None
) -> None:
    await _owned(
        session,
        "UPDATE hd_import_run SET state = :state, lease_until = NULL, lease_token = NULL, "
        "finished_at = now(), last_error = :error, retry_attempts = 0 WHERE id = :id",
        {
            "id": run_id, "state": state, "error": error[:500] if error else None,
            "token": token,
        },
    )  # fmt: skip


# Records a run could not read (P0-2)


async def note_failures(
    session: AsyncSession,
    workspace_id: UUID,
    run_id: UUID,
    source: str,
    failures: Sequence[Failure],
    *,
    attempts: int,
) -> None:
    """Writes what a page noted, in the page's transaction. The same record noted again in
    the same run (a later sync look) adds its tries and is open again, unless an Admin
    dismissed it: that decision stays."""
    for failure in failures:
        await session.execute(
            text(
                "INSERT INTO hd_import_failure (workspace_id, run_id, source, kind, external_id, "
                "category, attempts, last_error) VALUES (:ws, :run, :source, :kind, :ext, "
                ":category, :attempts, :error) "
                "ON CONFLICT (workspace_id, run_id, kind, external_id) DO UPDATE SET "
                "category = EXCLUDED.category, last_error = EXCLUDED.last_error, "
                "attempts = hd_import_failure.attempts + EXCLUDED.attempts, "
                "last_failed_at = now(), "
                "status = CASE WHEN hd_import_failure.status = 'dismissed' THEN 'dismissed' "
                "ELSE 'open' END, "
                "resolved_at = CASE WHEN hd_import_failure.status = 'dismissed' "
                "THEN hd_import_failure.resolved_at END"
            ),
            {
                "ws": workspace_id, "run": run_id, "source": source, "kind": failure.kind,
                "ext": failure.external_id[:200], "category": failure.category,
                "attempts": attempts if failure.category == "temporary" else 1,
                "error": failure.error[:500],
            },
        )  # fmt: skip


async def resolve_failures(
    session: AsyncSession, source: str, kind: str, external_ids: Sequence[str]
) -> None:
    """Records read in full now: any open failure noted for them earlier is resolved (a
    dismissed one stays dismissed). Never called for a record noted on the same page."""
    if not external_ids:
        return
    await session.execute(
        text(
            "UPDATE hd_import_failure SET status = 'resolved', resolved_at = now() "
            "WHERE source = :source AND kind = :kind AND external_id = ANY(:ids) "
            "AND status = 'open'"
        ),
        {"source": source, "kind": kind, "ids": [e[:200] for e in external_ids]},
    )


async def failures(
    session: AsyncSession,
    run_id: UUID,
    *,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[Row]:
    """A run's failures, last failed first."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT id, run_id, source, kind, external_id, category, attempts, last_error, "
            "first_failed_at, last_failed_at, last_failed_at AS created_at, status, resolved_at, "
            "resolved_by AS resolved_by_id FROM hd_import_failure WHERE run_id = :run "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (last_failed_at, id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY last_failed_at DESC, id DESC LIMIT :limit"
        ),
        {"run": run_id, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


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


async def ticket_ids_by_source(
    session: AsyncSession, source: str, external_ids: Sequence[str]
) -> dict[str, UUID]:
    """The CeDesk ticket for each source id that is already here."""
    if not external_ids:
        return {}
    result = await session.execute(
        text("SELECT source_id, id FROM hd_ticket WHERE source = :s AND source_id = ANY(:ids)"),
        {"s": source, "ids": list(external_ids)},
    )
    return {str(r[0]): UUID(str(r[1])) for r in result.all()}


async def message_keys(
    session: AsyncSession, ticket_ids: Sequence[UUID]
) -> dict[UUID, tuple[set[str], set[tuple[str, int]]]]:
    """Per ticket, which messages it already has: the source ids kept as `channel_message_id`,
    and (kind, whole second) for every message, to tell apart those that had no source id."""
    out: dict[UUID, tuple[set[str], set[tuple[str, int]]]] = {t: (set(), set()) for t in ticket_ids}
    if not ticket_ids:
        return out
    result = await session.execute(
        text(
            "SELECT ticket_id, channel_message_id, kind, created_at FROM hd_message "
            "WHERE ticket_id = ANY(:ids)"
        ),
        {"ids": list(ticket_ids)},
    )
    for ticket_id, channel_id, kind, created in result.all():
        ids, stamps = out[UUID(str(ticket_id))]
        if channel_id:
            ids.add(str(channel_id))
        stamps.add((str(kind), int(created.timestamp())))
    return out


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
    params.setdefault("id", None)  # None: the database picks one
    result = await session.execute(
        text(
            "INSERT INTO hd_ticket (id, workspace_id, number, subject, status, priority, "
            "team_id, assignee_id, contact_id, channel, fields, tags, first_reply_at, "
            "resolved_at, created_at, updated_at, source, source_id) VALUES "
            "(coalesce(CAST(:id AS uuid), gen_random_uuid()), :workspace_id, :number, "
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
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_member_id, "
            "author_name, channel_message_id, created_at) VALUES (:ws, :ticket, :kind, :body, "
            ":author, :name, :channel_id, :at) RETURNING id"
        ),
        {
            "ws": workspace_id, "ticket": ticket_id, "kind": kind, "body": body,
            "author": author_member_id, "name": author_name, "channel_id": channel_message_id,
            "at": created_at,
        },
    )  # fmt: skip
    return UUID(str(result.scalar_one()))


TRACKED = ("status", "priority", "assignee_id", "team_id", "tags", "subject", "fields")


async def current(session: AsyncSession, ticket_id: UUID) -> Row:
    """The ticket's fields a sync look may change, locked until the look's page commits."""
    result = await session.execute(
        text(
            "SELECT status, priority, resolved_at, assignee_id, team_id, tags, subject, "
            "fields FROM hd_ticket WHERE id = :id FOR UPDATE"
        ),
        {"id": ticket_id},
    )
    return result.mappings().one()


async def seen(session: AsyncSession, ticket_id: UUID) -> dict[str, Any] | None:
    """What the source showed for this ticket the last time it was read (P1-10)."""
    found = await session.scalar(
        text("SELECT seen FROM hd_import_seen WHERE ticket_id = :id"), {"id": ticket_id}
    )
    return dict(found) if found is not None else None


async def put_seen(
    session: AsyncSession, workspace_id: UUID, ticket_id: UUID, values: Mapping[str, Any]
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_import_seen (workspace_id, ticket_id, seen) "
            "VALUES (:ws, :id, CAST(:seen AS jsonb)) ON CONFLICT (workspace_id, ticket_id) "
            "DO UPDATE SET seen = EXCLUDED.seen, seen_at = now()"
        ),
        {"ws": workspace_id, "id": ticket_id, "seen": json.dumps(dict(values))},
    )


async def apply_changes(
    session: AsyncSession,
    ticket_id: UUID,
    changes: Mapping[str, Any],
    *,
    first_reply_at: datetime | None,
    updated_at: datetime,
) -> None:
    """Writes what a sync look decided to change (only names in `TRACKED` plus
    `resolved_at`); the time only moves forward and the first reply is only filled."""
    allowed = {k: v for k, v in changes.items() if k in (*TRACKED, "resolved_at")}
    sets = [f"{k} = :{k}" if k != "fields" else "fields = CAST(:fields AS jsonb)" for k in allowed]
    values: dict[str, Any] = {**allowed, "id": ticket_id, "first": first_reply_at,
                              "updated": updated_at}  # fmt: skip
    if "fields" in values:
        values["fields"] = json.dumps(values["fields"])
    sets += ["first_reply_at = coalesce(first_reply_at, :first)",
             "updated_at = GREATEST(updated_at, :updated)"]  # fmt: skip
    await session.execute(
        text("UPDATE hd_ticket SET " + ", ".join(sets) + " WHERE id = :id"),  # noqa: S608
        values,
    )


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


# History (M10)


async def history(
    session: AsyncSession, *, before: tuple[datetime, UUID] | None, limit: int
) -> Sequence[Row]:
    """Every import run, newest first, with where it came from and who started it."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT r.id, r.connection_id, r.source, c.label, r.state, r.sync_on, r.counts, "
            "r.last_error, r.last_sync_at, r.started_by, m.name AS started_by_name, "
            "r.started_at, r.started_at AS created_at, r.finished_at, r.options "
            "FROM hd_import_run r JOIN hd_import_connection c "
            "ON c.workspace_id = r.workspace_id AND c.id = r.connection_id "
            "LEFT JOIN member m ON m.workspace_id = r.workspace_id AND m.id = r.started_by "
            "WHERE (CAST(:at AS timestamptz) IS NULL "
            "       OR (r.started_at, r.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY r.started_at DESC, r.id DESC LIMIT :limit"
        ),
        {"at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


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


async def failure(session: AsyncSession, run_id: UUID, failure_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, run_id, source, kind, external_id, category, attempts, last_error, "
            "first_failed_at, last_failed_at, status, resolved_at, resolved_by AS resolved_by_id "
            "FROM hd_import_failure WHERE run_id = :run AND id = :id"
        ),
        {"run": run_id, "id": failure_id},
    )
    return result.mappings().first()


async def dismiss_failure(session: AsyncSession, failure_id: UUID, member_id: UUID) -> bool:
    """An Admin accepts what is missing: the failure ends as `dismissed`. False when it was
    not open any more."""
    result = await session.execute(
        text(
            "UPDATE hd_import_failure SET status = 'dismissed', resolved_at = now(), "
            "resolved_by = :member WHERE id = :id AND status = 'open' RETURNING id"
        ),
        {"id": failure_id, "member": member_id},
    )
    return result.first() is not None
