"""The import framework (md M1-M8, section 8.2): connect, discover, dry run, run.

- **Connect** (Admin): a source's credentials are sealed in the database (bound to the
  connection) and deleted when the run ends without "keep syncing". A CSV file is uploaded
  to the object store instead and deleted the same way.
- **Discover**: counts, and each source group with the team it probably maps to.
- **Dry run**: saves nothing; says what will be created, what is already here, and warns
  ("No team picked for ...").
- **Run** (the worker, `run_due`): contacts, then tickets, then articles, then saved
  replies. Each page is one transaction that also saves the cursor, so a crash resumes at
  the page it was on. A slice lasts `BUDGET_SECONDS`, then the worker's other work gets its
  turn and the run goes on next pass. A 429 pauses the run for the source's Retry-After.

What an imported ticket is (md 8.2 step 5): its own dates, status, tags, custom fields and
CSAT; `source` and `source_id` (so running again creates no duplicates); the tag
`imported-<source>`; a first system line saying where it came from. Triage and card
redaction run; **rules, routing, notifications, outbox events and webhooks do not**. Agents
are matched by email; one who isn't in CeDesk keeps their name on their messages and the
ticket stays unassigned. Ticket numbers are reserved a batch at a time in their own short
transaction, so writing a batch never holds the counter that live tickets need.
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.crypto import CannotOpen, Sealer
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain import saved_replies as reply_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.imports import (
    Article,
    Contact,
    RateLimited,
    Reply,
    SourceConnector,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.portal import normalise_email
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import imports as repo
from cedesk.helpdesk.repo import saved_replies as reply_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import sla, triage
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound, TooLarge, Unavailable
from cedesk.helpdesk.service.idempotency import Idempotency, begin, remember
from cedesk.helpdesk.service.imports.connectors import build_connector
from cedesk.helpdesk.service.imports.csv_source import MAX_BYTES, BadFile, CsvConnector
from cedesk.helpdesk.service.members import require
from cedesk.storage import ObjectMissing, ObjectStore

logger = logging.getLogger("cedesk.imports")

Build = Callable[[str, Mapping[str, Any], str | None], Awaitable[SourceConnector]]
Row = RowMapping


@dataclass(frozen=True)
class Importers:
    """What the framework needs from outside: the sealer for stored credentials, the object
    store for uploaded files, and how a source's connector is built (replaced in tests)."""

    sealer: Sealer
    store: ObjectStore
    build: Build


def make_importers(secret_key: str, store: ObjectStore, build: Build | None = None) -> Importers:
    async def default(
        source: str, settings: Mapping[str, Any], secret: str | None
    ) -> SourceConnector:
        if source == "csv":
            return await CsvConnector.load(store, settings)
        return await build_connector(source, settings, secret)

    return Importers(Sealer(secret_key), store, build or default)


def _bound(workspace_id: UUID, connection_id: UUID) -> str:
    return f"{workspace_id}:import:{connection_id}:credentials"


# Views


@dataclass(frozen=True)
class ConnectionView:
    id: UUID
    source: str
    label: str
    state: str
    credentials_hint: str | None
    created_at: datetime


@dataclass(frozen=True)
class GroupView:
    external_id: str
    name: str
    suggested_team_id: UUID | None
    suggested_team: str | None


@dataclass(frozen=True)
class Discovery:
    counts: rules.Counts
    group_name: str
    groups: list[GroupView]
    status_map: Mapping[str, str]
    warnings: list[str]


@dataclass(frozen=True)
class DryRun:
    will_create: dict[str, int]
    already_imported: dict[str, int]
    warnings: list[str]


@dataclass(frozen=True)
class RunView:
    id: UUID
    connection_id: UUID
    source: str
    state: str
    kinds: list[str]
    counts: dict[str, Any]
    sync_on: bool
    started_at: datetime
    finished_at: datetime | None
    last_error: str | None


def _run_view(row: Mapping[Any, Any]) -> RunView:
    return RunView(
        id=row["id"],
        connection_id=row["connection_id"],
        source=row["source"],
        state=row["state"],
        kinds=list(row["options"].get("kinds", rules.KINDS)),
        counts=dict(row["counts"]),
        sync_on=bool(row["sync_on"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        last_error=row["last_error"],
    )


def _connection_view(row: Mapping[Any, Any]) -> ConnectionView:
    return ConnectionView(
        id=row["id"],
        source=row["source"],
        label=row["label"],
        state=row["state"],
        credentials_hint=row["credentials_hint"],
        created_at=row["created_at"],
    )


# Talking to a source from a request: its failures become the API's


async def _asked[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except RateLimited:
        raise Unavailable(
            "The source is rate limiting this account. Try again in a minute."
        ) from None
    except SourceError as exc:
        raise (Invalid(str(exc)) if exc.permanent else Unavailable(str(exc))) from None
    except BadFile as exc:
        raise Invalid(str(exc)) from None


async def _build(
    importers: Importers, workspace_id: UUID, row: Mapping[Any, Any]
) -> SourceConnector:
    """The connector for a stored connection. Raises what the source or the sealer raises:
    the worker decides what that means for a run, a request turns it into an API error."""
    secret = None
    if row["credentials_sealed"] is not None:
        secret = importers.sealer.open(
            row["credentials_sealed"], bound_to=_bound(workspace_id, row["id"])
        )
    return await importers.build(row["source"], row["settings"], secret)


async def _open(
    importers: Importers, workspace_id: UUID, row: Mapping[Any, Any]
) -> SourceConnector:
    try:
        return await _asked(_build(importers, workspace_id, row))
    except CannotOpen as exc:
        raise Invalid(str(exc)) from None
    except ObjectMissing:
        raise Invalid("The uploaded file is no longer stored. Upload it again.") from None


def _hint(credentials: Mapping[str, Any]) -> str | None:
    """The last 4 characters of the secret (never of an email address), to tell keys apart."""
    for name in ("token", "api_key", "app_secret"):
        value = credentials.get(name)
        if isinstance(value, str) and value:
            return value[-4:]
    return None


# Connect (Admin)


async def connect(
    sm: Sessionmaker,
    importers: Importers,
    caller: Caller,
    *,
    source: str,
    settings: Mapping[str, Any],
    credentials: Mapping[str, Any],
    idem: Idempotency | None = None,
) -> ConnectionView:
    """Checks the credentials with the source, then keeps them sealed. With an
    `Idempotency-Key`, a repeat returns the connection the first one made."""
    require(caller, Action.settings)
    if source not in rules.AVAILABLE_SOURCES:
        raise Invalid("Pick one of: " + ", ".join(rules.AVAILABLE_SOURCES) + ".")
    ws, connection_id = caller.workspace_id, uuid4()
    secret = json.dumps(dict(credentials))
    connector = await _asked(importers.build(source, settings, secret))
    info = await _asked(connector.check())
    sealed = importers.sealer.seal(secret, bound_to=_bound(ws, connection_id))
    async with workspace_session(sm, ws) as s:
        earlier = await begin(s, caller, idem)
        if earlier is not None:
            row = await repo.connection(s, earlier)
        else:
            await repo.insert_connection(
                s, connection_id=connection_id, workspace_id=ws, source=source,
                label=info.name[:200] or rules.label(source), settings=settings, sealed=sealed,
                hint=_hint(credentials), created_by=caller.member_id,
            )  # fmt: skip
            await repo.add_event(
                s, ws, connection_id, "connected", member_id=caller.member_id,
                detail={"source": source, "account": info.name[:200]},
            )  # fmt: skip
            await remember(s, caller, idem, connection_id)
            row = await repo.connection(s, connection_id)
    assert row is not None
    return _connection_view(row)


async def upload_csv(
    sm: Sessionmaker,
    importers: Importers,
    caller: Caller,
    *,
    name: str,
    data: bytes,
    idem: Idempotency | None = None,
) -> ConnectionView:
    """The file is checked (header, UTF-8, size) and stored until the run ends. With an
    `Idempotency-Key`, a repeat returns the connection the first one made and keeps only
    the first file."""
    require(caller, Action.settings)
    if len(data) > MAX_BYTES:
        raise TooLarge("The file is larger than 25 MB.")
    try:
        connector = CsvConnector(data, filename=name)
    except BadFile as exc:
        raise Invalid(str(exc)) from None
    ws, connection_id = caller.workspace_id, uuid4()
    key = f"imports/{ws}/{connection_id}.csv"
    await importers.store.put(key, data, content_type="text/csv")
    replayed = False
    try:
        async with workspace_session(sm, ws) as s:
            earlier = await begin(s, caller, idem)
            if earlier is not None:
                replayed = True
                row = await repo.connection(s, earlier)
            else:
                await repo.insert_connection(
                    s, connection_id=connection_id, workspace_id=ws, source="csv",
                    label=(name.strip()[:200] or "CSV file"),
                    settings={"file_key": key, "filename": name.strip()[:200]},
                    sealed=None, hint=None, created_by=caller.member_id,
                )  # fmt: skip
                counts = await connector.discover()
                await repo.add_event(
                    s, ws, connection_id, "connected", member_id=caller.member_id,
                    detail={
                        "source": "csv", "tickets": counts.tickets,
                        "rows_left_out": len(connector.bad_rows),
                    },
                )  # fmt: skip
                await remember(s, caller, idem, connection_id)
                row = await repo.connection(s, connection_id)
    except Exception:
        await importers.store.delete(key)
        raise
    if replayed:  # the first request's file is the one kept
        await importers.store.delete(key)
    assert row is not None
    return _connection_view(row)


async def _connection(s: AsyncSession, connection_id: UUID) -> Row:
    row = await repo.connection(s, connection_id)
    if row is None:
        raise NotFound("No such connection.")
    return row


async def close_connection(
    sm: Sessionmaker, importers: Importers, caller: Caller, connection_id: UUID
) -> None:
    """Deletes the credentials (or the uploaded file) now. The history stays."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        row = await _connection(s, connection_id)
        if await repo.active_run(s, connection_id) is not None:
            raise Conflict("A run is in progress on this connection.")
        file_key = row["settings"].get("file_key")
        if await repo.close_connection(s, connection_id):
            await repo.add_event(
                s, ws, connection_id, "credentials_deleted", member_id=caller.member_id
            )
    if file_key:
        await importers.store.delete(str(file_key))


# Discover and dry run


async def _group_views(s: AsyncSession, groups: Sequence[rules.Group]) -> list[GroupView]:
    """Each source group with the team it probably maps to (by name, else by words)."""
    teams = await repo.team_names(s)
    names = list(teams.values())
    by_name = {v: k for k, v in teams.items()}
    views = []
    for group in groups:
        suggested = rules.suggest_team(group.name, names)
        views.append(
            GroupView(group.external_id, group.name, by_name.get(suggested or ""), suggested)
        )
    return views


async def discover(
    sm: Sessionmaker, importers: Importers, caller: Caller, connection_id: UUID
) -> Discovery:
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        row = await _connection(s, connection_id)
    if row["state"] == "closed":
        raise Conflict("This connection was closed. Connect again.")
    # The source is asked outside any transaction: it may be slow.
    connector = await _open(importers, ws, row)
    counts = await _asked(connector.discover())
    groups = await _asked(connector.groups())
    async with workspace_session(sm, ws) as s:
        views = await _group_views(s, groups)
    return Discovery(
        counts=counts,
        group_name=rules.GROUP_NAMES.get(row["source"], "Groups"),
        groups=views,
        status_map=dict(connector.status_map),
        warnings=_warnings(connector),
    )


def _warnings(connector: Any) -> list[str]:
    found = getattr(connector, "warnings", None)
    return list(found()) if callable(found) else []


async def _checked_options(s: AsyncSession, raw: Mapping[str, Any] | None) -> rules.Options:
    try:
        chosen = rules.options(raw)
    except rules.BadOptions as exc:
        raise Invalid(str(exc)) from None
    teams = await repo.team_names(s)
    for team in chosen.team_map.values():
        if team is not None and team not in teams:
            raise Invalid("That team does not exist.")
    return chosen


async def dry_run(
    sm: Sessionmaker,
    importers: Importers,
    caller: Caller,
    connection_id: UUID,
    raw_options: Mapping[str, Any] | None,
) -> DryRun:
    """What a run would do, without doing any of it. `already_imported` counts what this
    source brought before, so running again shows how much is new."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        row = await _connection(s, connection_id)
        chosen = await _checked_options(s, raw_options)
    connector = await _open(importers, ws, row)
    found = await _asked(connector.discover())
    source_groups = await _asked(connector.groups())
    async with workspace_session(sm, ws) as s:
        groups = await _group_views(s, source_groups)
        have = await repo.mapped_counts(s, row["source"])
        have_tickets = await repo.ticket_count(s, row["source"])
    already = {
        "contacts": have.get("contact", 0),
        "tickets": have_tickets,
        "articles": have.get("article", 0),
        "replies": have.get("reply", 0),
    }
    discovered = {
        "contacts": found.contacts, "tickets": found.tickets,
        "articles": found.articles, "replies": found.replies,
    }  # fmt: skip
    warnings = _warnings(connector)
    if "tickets" in chosen.kinds:
        warnings += [
            f"No team picked for '{g.name}'."
            for g in groups
            if chosen.team_map.get(g.external_id) is None
        ]
    return DryRun(
        will_create={
            k: max(0, discovered[k] - already[k]) if k in chosen.kinds else 0 for k in rules.KINDS
        },
        already_imported={k: already[k] if k in chosen.kinds else 0 for k in rules.KINDS},
        warnings=warnings,
    )


# Start and read a run


async def start_run(
    sm: Sessionmaker,
    caller: Caller,
    connection_id: UUID,
    raw_options: Mapping[str, Any] | None,
    idem: Idempotency | None = None,
) -> RunView:
    """Queues the run; the worker does it. One run at a time per connection. With an
    `Idempotency-Key`, a repeat returns the run the first one queued."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        earlier = await begin(s, caller, idem)
        if earlier is not None:
            again = await repo.run(s, earlier)
            assert again is not None
            return _run_view(again)
        row = await _connection(s, connection_id)
        if row["state"] == "closed":
            raise Conflict("This connection was closed. Connect again.")
        chosen = await _checked_options(s, raw_options)
        if await repo.active_run(s, connection_id) is not None:
            raise Conflict("A run is already in progress on this connection.")
        run_id = await repo.insert_run(
            s, workspace_id=ws, connection_id=connection_id, source=row["source"],
            options=chosen.as_json(), started_by=caller.member_id,
        )  # fmt: skip
        await repo.add_event(
            s, ws, connection_id, "started", run_id=run_id, member_id=caller.member_id,
            detail={"kinds": list(chosen.kinds)},
        )  # fmt: skip
        await remember(s, caller, idem, run_id)
        found = await repo.run(s, run_id)
    assert found is not None
    return _run_view(found)


async def get_run(sm: Sessionmaker, caller: Caller, run_id: UUID) -> RunView:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.run(s, run_id)
    if row is None:
        raise NotFound("No such run.")
    return _run_view(row)


# The worker: runs that are due


@dataclass
class _Ctx:
    sm: Sessionmaker
    ws: UUID
    run_id: UUID
    source: str
    started_by: UUID | None
    team_map: Mapping[str, UUID | None]
    status_map: Mapping[str, str]
    group_names: Mapping[str, str]
    cfg: triage.Config | None = None
    members: dict[str, Row | None] = field(default_factory=dict)
    contacts: dict[str, UUID] = field(default_factory=dict)
    categories: dict[str, UUID] = field(default_factory=dict)


async def run_due(
    sm: Sessionmaker, importers: Importers, *, budget: float = rules.BUDGET_SECONDS
) -> int:
    """One slice of import work, at most `budget` seconds: takes runs that are due, one
    workspace after another, and works each until the time is up. Returns how many runs
    it worked on. Run by the worker every pass; it does nothing when no import is due."""
    deadline = time.monotonic() + budget
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    worked = 0
    for workspace_id in workspace_ids:
        while time.monotonic() < deadline:
            now = datetime.now(UTC)
            async with workspace_session(sm, workspace_id) as s:
                claimed = await repo.claim_due(s, now, now + timedelta(seconds=rules.LEASE_SECONDS))
            if claimed is None:
                break
            worked += 1
            await _work(sm, importers, claimed, deadline)
    return worked


async def _work(sm: Sessionmaker, importers: Importers, run: Row, deadline: float) -> None:
    ws, run_id = run["workspace_id"], run["id"]
    try:
        await _slice(sm, importers, run, deadline)
    except RateLimited as exc:
        wait = rules.wait_seconds(exc.retry_after)
        await _pause(sm, ws, run_id, wait, "The source is rate limiting this import.")
    except SourceError as exc:
        if exc.permanent:
            await _fail(sm, ws, run, str(exc))
        else:
            await _pause(sm, ws, run_id, rules.DEFAULT_RATE_WAIT, str(exc))
    except (CannotOpen, BadFile) as exc:
        await _fail(sm, ws, run, str(exc))
    except ObjectMissing:
        await _fail(sm, ws, run, "The uploaded file is no longer stored. Upload it again.")
    except Exception as exc:
        # Never the message: a database error can quote the customer's data.
        logger.error("import run failed: %s", type(exc).__name__, extra={"event": "import"})
        await _fail(sm, ws, run, "The import stopped on an unexpected error. See the server log.")


async def _pause(
    sm: Sessionmaker, workspace_id: UUID, run_id: UUID, seconds: int, why: str
) -> None:
    async with workspace_session(sm, workspace_id) as s:
        await repo.release(
            s, run_id, next_run_at=datetime.now(UTC) + timedelta(seconds=seconds), error=why
        )


async def _fail(sm: Sessionmaker, workspace_id: UUID, run: Row, message: str) -> None:
    async with workspace_session(sm, workspace_id) as s:
        await repo.finish(s, run["id"], state="failed", error=message)
        await repo.add_event(
            s, workspace_id, run["connection_id"], "failed", run_id=run["id"],
            detail={"error": message[:300]},
        )  # fmt: skip


async def _slice(sm: Sessionmaker, importers: Importers, run: Row, deadline: float) -> None:
    ws, run_id = run["workspace_id"], run["id"]
    async with workspace_session(sm, ws) as s:
        conn = await repo.connection(s, run["connection_id"])
    if conn is None or conn["state"] == "closed":
        await _fail(sm, ws, run, "The connection was closed.")
        return
    connector = await _build(importers, ws, conn)
    chosen = rules.options(run["options"])
    groups = {g.external_id: g.name for g in await connector.groups()}
    ctx = _Ctx(
        sm=sm, ws=ws, run_id=run_id, source=run["source"], started_by=run["started_by"],
        team_map=chosen.team_map, status_map=connector.status_map, group_names=groups,
    )  # fmt: skip
    cursor: dict[str, Any] = dict(run["cursor"])
    counts: dict[str, Any] = dict(run["counts"])
    done: list[str] = list(cursor.get("done", []))
    while True:
        kind = rules.next_kind(chosen.kinds, done)
        if kind is None:
            await _complete(sm, importers, ws, run, conn, counts)
            return
        if time.monotonic() >= deadline:
            async with workspace_session(sm, ws) as s:
                await repo.release(s, run_id, next_run_at=datetime.now(UTC))
            return
        page = await connector.page(kind, cursor.get("page"))
        async with workspace_session(sm, ws) as s:
            stats = await _write(s, ctx, kind, list(page.items))
            counts[kind] = _add(counts.get(kind, {}), stats)
            skipped = getattr(connector, "bad_rows", None)
            if skipped is not None:
                counts["rows_left_out"] = len(skipped)
            if page.cursor is None:
                done.append(kind)
                cursor = {"done": done}
            else:
                cursor = {"done": done, "page": dict(page.cursor)}
            await repo.save_progress(
                s, run_id, cursor=cursor, counts=counts,
                lease_until=datetime.now(UTC) + timedelta(seconds=rules.LEASE_SECONDS),
            )  # fmt: skip


def _add(total: Mapping[str, int], more: Mapping[str, int]) -> dict[str, int]:
    out = dict(total)
    for key, value in more.items():
        out[key] = out.get(key, 0) + value
    return out


async def _complete(
    sm: Sessionmaker, importers: Importers, ws: UUID, run: Row, conn: Row, counts: Mapping[str, Any]
) -> None:
    file_key = conn["settings"].get("file_key")
    async with workspace_session(sm, ws) as s:
        await repo.finish(s, run["id"], state="done")
        await repo.add_event(
            s, ws, run["connection_id"], "finished", run_id=run["id"], detail=counts
        )
        if not run["sync_on"] and await repo.close_connection(s, run["connection_id"]):
            await repo.add_event(
                s, ws, run["connection_id"], "credentials_deleted", run_id=run["id"]
            )
        else:
            file_key = None
    if file_key:
        await importers.store.delete(str(file_key))


# Writing a page


async def _write(s: AsyncSession, ctx: _Ctx, kind: str, items: list[Any]) -> dict[str, int]:
    if kind == "contacts":
        return await _contacts(s, ctx, items)
    if kind == "tickets":
        return await _tickets(s, ctx, items)
    if kind == "articles":
        return await _articles(s, ctx, items)
    return await _replies(s, ctx, items)


async def _contact(s: AsyncSession, ctx: _Ctx, contact: Contact) -> UUID:
    known = ctx.contacts.get(contact.external_id)
    if known is not None:
        return known
    found = (await repo.mapped(s, ctx.source, "contact", [contact.external_id])).get(
        contact.external_id
    )
    if found is None:
        email = normalise_email(contact.email or "") or rules.placeholder_email(
            ctx.source, contact.external_id
        )
        found = await ticket_repo.upsert_contact(
            s, ctx.ws, email, (contact.name or "").strip()[:120] or email
        )
        await repo.put_map(s, ctx.ws, ctx.source, "contact", contact.external_id, found)
    ctx.contacts[contact.external_id] = found
    return found


async def _contacts(s: AsyncSession, ctx: _Ctx, items: list[Contact]) -> dict[str, int]:
    have = await repo.mapped(s, ctx.source, "contact", [c.external_id for c in items])
    created = 0
    for contact in items:
        if contact.external_id not in have:
            await _contact(s, ctx, contact)
            created += 1
    return {"created": created, "skipped": len(items) - created}


async def _member(s: AsyncSession, ctx: _Ctx, email: str | None) -> Row | None:
    if not email:
        return None
    key = email.strip().lower()
    if key not in ctx.members:
        ctx.members[key] = await auth_repo.active_member_for_email(s, key)
    return ctx.members[key]


async def _tickets(s: AsyncSession, ctx: _Ctx, items: list[Ticket]) -> dict[str, int]:
    unique = {t.external_id: t for t in items}
    have = await repo.existing_ticket_source_ids(s, ctx.source, list(unique))
    fresh = [t for key, t in unique.items() if key not in have]
    stats = {"created": 0, "skipped": len(items) - len(fresh), "unknown_agent_messages": 0}
    if not fresh:
        return stats
    # The numbers are taken in their own short transaction: the counter row stays locked only
    # for that statement, not for the whole batch.
    async with workspace_session(ctx.sm, ctx.ws) as reserved:
        first = await ticket_repo.reserve_numbers(reserved, ctx.ws, len(fresh))
    cfg = ctx.cfg = ctx.cfg or await triage.config(s)
    ids = []
    for offset, ticket in enumerate(fresh):
        ids.append(await _ticket(s, ctx, cfg, ticket, first + offset, stats))
        stats["created"] += 1
    # An old open ticket has a due time in the past; the SLA code files that breach as
    # already alerted when a change (an import) makes it, so nobody is paged for it.
    await sla.refresh(s, ticket_ids=ids)
    return stats


async def _ticket(
    s: AsyncSession, ctx: _Ctx, cfg: triage.Config, t: Ticket, number: int, stats: dict[str, int]
) -> UUID:
    contact_id = await _contact(s, ctx, t.requester)
    state = rules.status(t.status, ctx.status_map)
    messages = sorted((m for m in t.messages if rules.body(m.body)), key=lambda m: m.created_at)
    last = messages[-1].created_at if messages else t.created_at
    resolved_at = (t.resolved_at or t.updated_at or last) if state == "resolved" else None
    assignee = await _member(s, ctx, t.assignee_email)
    subject = triage.clean(cfg, rules.subject(t.subject), rules.SUBJECT_MAX)
    removed = set(subject.numbers)
    ticket_id = await repo.insert_ticket(
        s,
        {
            "workspace_id": ctx.ws,
            "number": number,
            "subject": subject.text or rules.subject(None),
            "status": state,
            "priority": rules.priority(t.priority),
            "team_id": ctx.team_map.get(t.group_id) if t.group_id else None,
            "assignee_id": assignee["id"] if assignee else None,
            "contact_id": contact_id,
            "channel": rules.channel(t.channel),
            "fields": rules.clean_fields(t.fields),
            "tags": rules.clean_tags(t.tags, ctx.source),
            "first_reply_at": next((m.created_at for m in messages if m.kind == "agent"), None),
            "resolved_at": resolved_at,
            "created_at": t.created_at,
            "updated_at": max(t.updated_at or t.created_at, last, t.created_at),
            "source": ctx.source,
            "source_id": t.external_id,
        },
    )
    group = ctx.group_names.get(t.group_id) if t.group_id else None
    first_at = min(t.created_at, messages[0].created_at) if messages else t.created_at
    await repo.insert_message(
        s, workspace_id=ctx.ws, ticket_id=ticket_id, kind="system",
        body=rules.history_line(ctx.source, group, t.external_id, t.status),
        created_at=first_at - timedelta(milliseconds=1), author_member_id=None,
        author_name=None, channel_message_id=None,
    )  # fmt: skip
    seen: set[str] = set()
    for message in messages:
        body, author_member, name = rules.body(message.body), None, message.author_name
        if message.kind == "customer":
            cleaned = triage.clean(cfg, body)
            body, removed = cleaned.text, removed | cleaned.numbers
            name = name or t.requester.name
        else:
            member = await _member(s, ctx, message.author_email)
            if member is not None:
                author_member, name = member["id"], member["name"]
            else:
                name = rules.unknown_agent(name, ctx.source)
                if message.kind == "agent":
                    stats["unknown_agent_messages"] += 1
        external = message.external_id[:200] if message.external_id else None
        if external in seen:
            external = None
        if external:
            seen.add(external)
        await repo.insert_message(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, kind=message.kind, body=body,
            created_at=message.created_at, author_member_id=author_member, author_name=name,
            channel_message_id=external,
        )  # fmt: skip
    if removed:
        await repo.insert_message(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, kind="system",
            body=triage.removed_line(len(removed)), created_at=last, author_member_id=None,
            author_name=None, channel_message_id=None,
        )  # fmt: skip
    if t.csat is not None and state == "resolved" and resolved_at is not None:
        await repo.insert_csat(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, contact_id=contact_id,
            score=t.csat.score, comment=(t.csat.comment or "")[:2000] or None,
            source=ctx.source, rated_at=t.csat.rated_at or resolved_at,
        )  # fmt: skip
    await triage.retriage(s, ticket_id, cfg=cfg)
    await repo.contact_since(s, contact_id, t.created_at)
    return ticket_id


async def _articles(s: AsyncSession, ctx: _Ctx, items: list[Article]) -> dict[str, int]:
    created = 0
    for article in items:
        category_id = None
        if article.category is not None:
            category_id = await _category(s, ctx, article.category)
        made = await repo.insert_article(
            s, ctx.ws, category_id=category_id, title=article.title.strip()[:200] or "(untitled)",
            body=article.body[:100_000], published=article.published, source=ctx.source,
            source_id=article.external_id[:200], updated_by=ctx.started_by,
        )  # fmt: skip
        if made is not None:
            await repo.put_map(s, ctx.ws, ctx.source, "article", article.external_id, made)
            created += 1
    return {"created": created, "skipped": len(items) - created}


async def _category(s: AsyncSession, ctx: _Ctx, category: rules.Category) -> UUID:
    known = ctx.categories.get(category.external_id)
    if known is not None:
        return known
    found = (await repo.mapped(s, ctx.source, "category", [category.external_id])).get(
        category.external_id
    )
    if found is None:
        name = category.name.strip()[:80] or "Imported"
        found = await repo.category_by_name(s, name) or await repo.insert_category(
            s, ctx.ws, name=name, source=ctx.source, source_id=category.external_id[:200]
        )
        await repo.put_map(s, ctx.ws, ctx.source, "category", category.external_id, found)
    ctx.categories[category.external_id] = found
    return found


async def _replies(s: AsyncSession, ctx: _Ctx, items: list[Reply]) -> dict[str, int]:
    have = await repo.mapped(s, ctx.source, "reply", [r.external_id for r in items])
    taken = await reply_repo.shortcuts(s)
    created = 0
    for reply in items:
        body = reply.body.strip()[: reply_rules.MAX_BODY]
        if reply.external_id in have or not body:
            continue
        try:
            shortcut = reply_rules.free_shortcut(reply.shortcut, taken)
        except reply_rules.ShortcutError:
            continue
        made = await reply_repo.insert_reply(
            s, ctx.ws, shortcut=shortcut, body=body, source=ctx.source, created_by=ctx.started_by
        )
        if made is not None:
            taken.add(shortcut)
            await repo.put_map(s, ctx.ws, ctx.source, "reply", reply.external_id, made)
            created += 1
    return {"created": created, "skipped": len(items) - created}
