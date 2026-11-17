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

**One record never stops a run** (audit P0-2): a ticket the source deleted after listing it
(a 404 on its messages) is left out, written to `hd_import_failure` (`not_found`) in the same
transaction as the page and its cursor, and the rest of the page goes on. A temporary error
(a 5xx, a timeout) retries the page with exponential backoff and jitter (`RetryPolicy`,
never less than the source's Retry-After); the count of tries is kept on the run
(`retry_attempts`), so a worker restart doesn't reset it. When the tries run out, one last try
isolates the records that keep failing (noted as `temporary`, their files kept as links) and
saves the rest; a page that still can't be read stops the run with a message that says so,
and its cursor never moves past it. Refused credentials and other permanent errors still stop
the run at once; a record imported later (an import run again, a sync look) resolves its
failure.

What an imported ticket is (md 8.2 step 5): its own dates, status, tags, custom fields and
CSAT; `source` and `source_id` (so running again creates no duplicates); the tag
`imported-<source>`; a first system line saying where it came from. Triage and card
redaction run; **rules, routing, notifications, outbox events and webhooks do not**. Agents
are matched by email; one who isn't in CeDesk keeps their name on their messages and the
ticket stays unassigned. Ticket numbers are reserved a batch at a time in their own short
transaction, so writing a batch never holds the counter that live tickets need.

**Files** (md 8.2 step 5): a message's attachments are copied to the object store, in the
same way as a file in a customer's email (the virus scan sees them like any other). They are
fetched and stored *before* the page's transaction opens, so no transaction waits on a
download; if the page then fails, the copies are deleted. A file over 25 MB, or one the
source no longer serves, is kept as a link in a system line under its message.

**Keep syncing until go-live** (M9): a run started with `sync` doesn't delete the
credentials when its first import ends. It stays `done` with `sync_on`, and the worker asks
the source "what changed since the last look" every 5 minutes (`run_due`, the same pass that
does imports): a new ticket is imported, a ticket already here gets only its new messages
(found by their source ids), its new status, its owner and team when the source names ones
CeDesk knows, and the tags it gained. An import run again does the same for every ticket
already here, so changes made in the source between two imports aren't lost. `sync-now`
makes the next look due at once; `sync-stop` turns it off and deletes the credentials.
"""

import asyncio
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
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain import saved_replies as reply_rules
from cedesk.helpdesk.domain.attachments import clean_mime, clean_name, object_key
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.imports import (
    Article,
    ChangeSource,
    Contact,
    FileSource,
    LeaseLost,
    Message,
    RateLimited,
    Reply,
    SourceConnector,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.portal import normalise_email
from cedesk.helpdesk.repo import archive as archive_repo
from cedesk.helpdesk.repo import attachments as attach_repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import imports as repo
from cedesk.helpdesk.repo import saved_replies as reply_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, sla, triage
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
    retry: rules.RetryPolicy = field(default_factory=rules.RetryPolicy)


def retry_policy(settings: Any) -> rules.RetryPolicy:
    """The import retry policy from `Settings.import_retry_*` (raises ValueError when the cap
    is under the base, so a bad setting stops the process at start)."""
    return rules.RetryPolicy(
        attempts=settings.import_retry_attempts,
        base_seconds=settings.import_retry_base_seconds,
        max_seconds=settings.import_retry_max_seconds,
    )


def make_importers(
    secret_key: str,
    store: ObjectStore,
    build: Build | None = None,
    retry: rules.RetryPolicy | None = None,
) -> Importers:
    async def default(
        source: str, settings: Mapping[str, Any], secret: str | None
    ) -> SourceConnector:
        if source == "csv":
            return await CsvConnector.load(store, settings)
        return await build_connector(source, settings, secret)

    return Importers(Sealer(secret_key), store, build or default, retry or rules.RetryPolicy())


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
    last_sync_at: datetime | None = None
    retry_attempts: int = 0  # failed tries of the page it is on (P0-2)
    outcome: str = "in_progress"  # rules.OUTCOMES (P0-3)
    report: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureView:
    id: UUID
    kind: str
    external_id: str
    category: str
    attempts: int
    last_error: str
    first_failed_at: datetime
    last_failed_at: datetime
    status: str
    resolved_at: datetime | None
    resolved_by_id: UUID | None = None


@dataclass(frozen=True)
class FailurePage:
    failures: list[FailureView]
    next_cursor: str | None


@dataclass(frozen=True)
class HistoryItem:
    id: UUID
    connection_id: UUID
    source: str
    account: str
    result: str  # running | finished | syncing | failed
    started_by_id: UUID | None
    started_by: str | None
    started_at: datetime
    finished_at: datetime | None
    kinds: list[str]
    counts: dict[str, Any]
    sync_on: bool
    last_sync_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class HistoryPage:
    runs: list[HistoryItem]
    next_cursor: str | None


def result_word(state: str, sync_on: bool) -> str:
    """What an Admin reads for a run: `running` covers queued and running."""
    if state == "failed":
        return "failed"
    if state == "done":
        return "syncing" if sync_on else "finished"
    return "running"


def run_report(row: Mapping[Any, Any]) -> dict[str, Any]:
    """What a run brought and what it couldn't, for the Admin (P0-3): the open failures
    (listed one by one by `run_failures`), what was cut, linked or replaced (`losses`, each
    with its meaning), the source's warnings, and the source's own counts against what was
    imported (`reconciliation`: a positive `difference` is records the source counted that
    the run neither imported nor found already here nor noted)."""
    counts: Mapping[str, Any] = row["counts"] or {}
    losses = {k: int(v) for k, v in (counts.get("losses") or {}).items() if v}
    found = counts.get("discovered") or {}
    reconciliation: dict[str, Any] = {}
    for kind in row["options"].get("kinds", rules.KINDS):
        mine = counts.get(kind) or {}
        accounted = sum(int(mine.get(k) or 0) for k in ("created", "skipped", "failed"))
        source = found.get(kind) if isinstance(found, Mapping) else None
        reconciliation[kind] = {
            "in_source": source,
            "accounted_for": accounted,
            "difference": None if source is None else max(0, int(source) - accounted),
        }
    return {
        "open_failures": int(row.get("open_failures") or 0),
        "losses": [{"key": k, "count": n, "meaning": rules.LOSS_KEYS.get(k, k)}
                   for k, n in sorted(losses.items())],
        "warnings": list(counts.get("warnings") or []),
        "reconciliation": reconciliation,
    }  # fmt: skip


def _outcome(row: Mapping[Any, Any], report: Mapping[str, Any]) -> str:
    losses = {item["key"]: item["count"] for item in report["losses"]}
    gaps = sum(int(r["difference"] or 0) for r in report["reconciliation"].values())
    return rules.outcome(row["state"], report["open_failures"], losses, gaps)


def _run_view(row: Mapping[Any, Any]) -> RunView:
    report = run_report(row)
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
        last_sync_at=row["last_sync_at"],
        retry_attempts=int(row.get("retry_attempts") or 0),
        outcome=_outcome(row, report),
        report=report,
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
            await audit.record(
                s, caller, Area.imports, "connected", f"Import from {rules.label(source)}",
                target_id=connection_id, detail={"account": info.name[:200]},
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
                await audit.record(
                    s, caller, Area.imports, "connected", "Import from a CSV file",
                    target_id=connection_id, detail={"tickets": counts.tickets},
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


async def _connection(s: AsyncSession, connection_id: UUID, *, lock: bool = False) -> Row:
    row = await repo.connection(s, connection_id, lock=lock)
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
        row = await _connection(s, connection_id, lock=True)
        if await repo.active_run(s, connection_id) is not None:
            raise Conflict("A run is in progress on this connection.")
        file_key = row["settings"].get("file_key")
        if await repo.close_connection(s, connection_id):
            await repo.add_event(
                s, ws, connection_id, "credentials_deleted", member_id=caller.member_id
            )
            await audit.record(
                s, caller, Area.imports, "closed", f"Import from {rules.label(row['source'])}",
                target_id=connection_id,
            )  # fmt: skip
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
    source brought before, so running again shows how much is new. That is an estimate
    (totals, not ids: asking the source for every id would take as long as the run), and
    wrong when some came from another account of the same help desk or were deleted there,
    so then a warning says so. The run itself checks each one by its id."""
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
    warnings += _estimate_warnings(row["source"], chosen.kinds, discovered, already)
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


def _estimate_warnings(
    source: str, kinds: Sequence[str], discovered: Mapping[str, int], already: Mapping[str, int]
) -> list[str]:
    """When something from this help desk is here already, "to create" is a guess."""
    label = rules.label(source)
    out = []
    for kind in kinds:
        found, have = discovered.get(kind, 0), already.get(kind, 0)
        if not have:
            continue
        if have > found:
            out.append(
                f"CeDesk already has {have} {kind} from {label}, more than this account has "
                f"({found}): some came from another {label} account or were deleted there. "
                f"So '{kind} to create' can't be counted ahead; the run checks each one: new "
                "ones are created, ones already here are updated, none are copied twice."
            )
        else:
            out.append(
                f"'{kind} to create' assumes the {have} {kind} already here from {label} came "
                "from this account. The run checks each one: new ones are created, ones "
                "already here are updated, none are copied twice."
            )
    return out


# Start and read a run


async def start_run(
    sm: Sessionmaker,
    caller: Caller,
    connection_id: UUID,
    raw_options: Mapping[str, Any] | None,
    idem: Idempotency | None = None,
    *,
    sync: bool = False,
) -> RunView:
    """Queues the run; the worker does it. One run at a time per connection. `sync`: keep
    asking the source for changes after the import, until the Admin stops it (M9). With an
    `Idempotency-Key`, a repeat returns the run the first one queued."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        earlier = await begin(s, caller, idem)
        if earlier is not None:
            again = await repo.run(s, earlier)
            assert again is not None
            return _run_view(again)
        row = await _connection(s, connection_id, lock=True)
        if row["state"] == "closed":
            raise Conflict("This connection was closed. Connect again.")
        chosen = await _checked_options(s, raw_options)
        if sync and row["source"] not in rules.SYNC_SOURCES:
            raise Invalid("This source can't keep syncing: there is nothing to ask again.")
        if await repo.active_run(s, connection_id) is not None:
            raise Conflict("A run is already in progress on this connection.")
        if await repo.syncing_run(s, connection_id) is not None:
            raise Conflict("This connection is syncing. Stop the sync before starting again.")
        run_id = await repo.insert_run(
            s, workspace_id=ws, connection_id=connection_id, source=row["source"],
            options=chosen.as_json(), started_by=caller.member_id, sync_on=sync,
        )  # fmt: skip
        await repo.add_event(
            s, ws, connection_id, "started", run_id=run_id, member_id=caller.member_id,
            detail={"kinds": list(chosen.kinds), "sync": sync},
        )  # fmt: skip
        await audit.record(
            s, caller, Area.imports, "started", f"Import from {rules.label(row['source'])}",
            target_id=run_id, detail={"kinds": list(chosen.kinds), "sync": sync},
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


async def run_failures(
    sm: Sessionmaker, caller: Caller, run_id: UUID, *, cursor: str | None = None, limit: int = 50
) -> FailurePage:
    """The records a run could not read and left out, last failed first (P0-2). They stay
    after the run ends; one imported later shows as resolved."""
    require(caller, Action.settings)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "import_failure")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from None
    async with workspace_session(sm, caller.workspace_id) as s:
        if await repo.run(s, run_id) is None:
            raise NotFound("No such run.")
        rows = await repo.failures(s, run_id, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "import_failure")
    return FailurePage(failures=[_failure_view(r) for r in page], next_cursor=next_cursor)


def _failure_view(r: Mapping[Any, Any]) -> FailureView:
    return FailureView(
        id=r["id"], kind=r["kind"], external_id=r["external_id"], category=r["category"],
        attempts=r["attempts"], last_error=r["last_error"],
        first_failed_at=r["first_failed_at"], last_failed_at=r["last_failed_at"],
        status=r["status"], resolved_at=r["resolved_at"], resolved_by_id=r["resolved_by_id"],
    )  # fmt: skip


async def dismiss_failure(
    sm: Sessionmaker, caller: Caller, run_id: UUID, failure_id: UUID
) -> FailureView:
    """An Admin accepts that a record (or part of its history) will not come: the failure
    ends as `dismissed`, with who decided. Only an open one; a later read never reopens it."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await repo.failure(s, run_id, failure_id) is None:
            raise NotFound("No such failure on this run.")
        if not await repo.dismiss_failure(s, failure_id, caller.member_id):
            raise Conflict("This failure is not open any more.")
        found = await repo.failure(s, run_id, failure_id)
    if found is None:  # the run was deleted meanwhile
        raise NotFound("No such failure on this run.")
    return _failure_view(found)


async def resume_run(
    sm: Sessionmaker,
    importers: Importers,
    caller: Caller,
    run_id: UUID,
    idem: Idempotency | None = None,
) -> RunView:
    """Queues a failed import again where it stopped: the worker carries on from its last
    saved page (the page that failed is read again; the ones before it are not). The same run
    keeps its counts, options, team mapping, sync setting and failure notes; its tries start
    from 0. Only a failed run whose connection still has readable credentials (or its file),
    and no other run of that connection in progress or syncing. Nothing changes when it is
    refused. The connection row is locked meanwhile, and the run moves out of `failed` in one
    statement, so two resumes at once queue it once."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        earlier = await begin(s, caller, idem)
        if earlier is not None:
            again = await repo.run(s, earlier)
            assert again is not None
            return _run_view(again)
        first = await repo.run(s, run_id)
        if first is None:
            raise NotFound("No such run.")
        conn = await repo.connection(s, first["connection_id"], lock=True)
        row = await repo.run(s, run_id)  # read again now that the connection is ours
        assert row is not None
        _resumable(row)
        _credentials_kept(importers, ws, conn)
        others = await repo.active_run(s, row["connection_id"])
        if others is not None or await repo.syncing_run(s, row["connection_id"]) is not None:
            raise Conflict(
                "Another run on this connection is in progress or syncing. Let it finish (or "
                "stop the sync) before resuming this one."
            )
        if not await repo.resume(s, run_id):
            raise Conflict("This run is in progress.")
        detail = _resume_detail(row)
        await repo.add_event(
            s, ws, row["connection_id"], "resumed", run_id=run_id, member_id=caller.member_id,
            detail=detail,
        )  # fmt: skip
        await audit.record(
            s, caller, Area.imports, "resumed", f"Import from {rules.label(row['source'])}",
            target_id=run_id, detail=detail,
        )  # fmt: skip
        await remember(s, caller, idem, run_id)
        found = await repo.run(s, run_id)
    assert found is not None
    return _run_view(found)


def _resumable(row: Mapping[Any, Any]) -> None:
    if row["state"] in ("queued", "running"):
        raise Conflict("This run is in progress.")
    if row["state"] == "done":
        if row["sync_on"]:
            raise Conflict("This run finished and is syncing; there is nothing to resume.")
        raise Conflict("This run finished; there is nothing to resume.")


def _credentials_kept(importers: Importers, ws: UUID, conn: Mapping[Any, Any] | None) -> None:
    """The connection can still be read: never reconnected, nothing new asked for, nothing
    about the credentials said back."""
    gone = Conflict(
        "This run's connection was closed and its credentials deleted. Connect again and start "
        "a new import: what is already here is skipped."
    )
    if conn is None or conn["state"] == "closed":
        raise gone
    if conn["source"] == "csv":
        if not conn["settings"].get("file_key"):
            raise gone
        return
    if conn["credentials_sealed"] is None:
        raise gone
    try:
        importers.sealer.open(conn["credentials_sealed"], bound_to=_bound(ws, conn["id"]))
    except (CannotOpen, ValueError):
        raise Conflict(
            "This run's stored credentials can't be read any more. Connect again and start a "
            "new import: what is already here is skipped."
        ) from None


def _resume_detail(row: Mapping[Any, Any]) -> dict[str, Any]:
    """Where the run carries on from, in words safe to keep: the kinds already done, whether
    a page of the next was saved, what it made so far and why it had stopped. Never the raw
    cursor (a source's paging token)."""
    cursor = row["cursor"] or {}
    counts = row["counts"] or {}
    created = {
        kind: int(c["created"])
        for kind, c in counts.items()
        if isinstance(c, Mapping) and "created" in c
    }
    return {
        "kinds_done": list(cursor.get("done", [])),
        "page_saved": "page" in cursor,
        "created": created,
        "error": (row["last_error"] or "")[:300],
    }


async def sync_now(sm: Sessionmaker, caller: Caller, run_id: UUID) -> RunView:
    """Makes the next look at the source due now (the worker does it within its next pass)."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.run(s, run_id)
        if row is None:
            raise NotFound("No such run.")
        if not row["sync_on"]:
            raise Conflict("This run isn't syncing.")
        if row["state"] != "done":
            raise Conflict("The first import is still running; syncing starts when it ends.")
        await repo.sync_now(s, run_id)
        found = await repo.run(s, run_id)
    assert found is not None
    return _run_view(found)


async def sync_stop(
    sm: Sessionmaker, importers: Importers, caller: Caller, run_id: UUID
) -> RunView:
    """Go-live: stops asking the source and deletes the stored credentials, so CeDesk keeps
    no access to the old help desk. While the first import is still running, only the sync
    is turned off; the credentials go when the import ends. A worker on the run keeps its
    lease; whichever of the stop and the import's end commits first, the other sees it (the
    worker reads the run locked, the stop decides on the state its update returns)."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    async with workspace_session(sm, ws) as s:
        row = await repo.run(s, run_id)
        if row is None:
            raise NotFound("No such run.")
        state = await repo.stop_sync(s, run_id)  # waits for a worker ending the import
        if state is None:
            raise Conflict("This run isn't syncing.")
        label = rules.label(row["source"])
        await repo.add_event(
            s, ws, row["connection_id"], "sync_stopped", run_id=run_id, member_id=caller.member_id
        )
        await audit.record(
            s, caller, Area.imports, "sync_stopped", f"Import from {label}", target_id=run_id
        )
        if state == "done" and await repo.close_connection(s, row["connection_id"]):
            await repo.add_event(
                s, ws, row["connection_id"], "credentials_deleted", run_id=run_id,
                member_id=caller.member_id,
            )  # fmt: skip
        found = await repo.run(s, run_id)
    assert found is not None
    return _run_view(found)


async def history(
    sm: Sessionmaker, caller: Caller, *, cursor: str | None = None, limit: int = 50
) -> HistoryPage:
    """Every import, newest first: when, from where, by whom, and how it went (M10)."""
    require(caller, Action.settings)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "import")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from None
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.history(s, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "import")
    return HistoryPage(
        runs=[
            HistoryItem(
                id=r["id"], connection_id=r["connection_id"], source=r["source"],
                account=r["label"], result=result_word(r["state"], bool(r["sync_on"])),
                started_by_id=r["started_by"], started_by=r["started_by_name"],
                started_at=r["started_at"], finished_at=r["finished_at"],
                kinds=list(r["options"].get("kinds", rules.KINDS)), counts=dict(r["counts"]),
                sync_on=bool(r["sync_on"]), last_sync_at=r["last_sync_at"],
                last_error=r["last_error"],
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )  # fmt: skip


# The worker: runs that are due


@dataclass(frozen=True)
class _Kept:
    """A file copied to the object store, waiting for its row."""

    attachment_id: UUID
    key: str
    name: str
    size: int
    mime: str


@dataclass(frozen=True)
class _Linked:
    """A file kept as a link: the system line that says so."""

    line: str


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
    store: ObjectStore
    update_existing: bool = False  # a sync look: tickets already here get their new messages
    cfg: triage.Config | None = None
    members: dict[str, Row | None] = field(default_factory=dict)
    contacts: dict[str, UUID] = field(default_factory=dict)
    categories: dict[str, UUID] = field(default_factory=dict)
    # Set by `_prepare` for the page about to be written:
    have: dict[str, UUID] = field(default_factory=dict)  # source id -> ticket already here
    archived: set[str] = field(default_factory=set)  # source ids already in the archive (O3)
    ticket_ids: dict[str, UUID] = field(default_factory=dict)  # source id -> ticket id to use
    seen: dict[UUID, tuple[set[str], set[tuple[str, int]]]] = field(default_factory=dict)
    files: dict[int, list[_Kept | _Linked]] = field(default_factory=dict)  # by id(message)
    unresolved: set[str] = field(default_factory=set)  # this page's noted tickets (P0-2)
    file_notes: list[rules.Failure] = field(default_factory=list)  # files that couldn't be copied


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
            lease = now + timedelta(seconds=rules.LEASE_SECONDS)
            async with workspace_session(sm, workspace_id) as s:
                claimed = await repo.claim_due(s, now, lease)
                if claimed is None:  # imports first; then a syncing run whose look is due
                    claimed = await repo.claim_sync(s, now, lease)
            if claimed is None:
                break
            worked += 1
            await _work(sm, importers, claimed, deadline)
    return worked


async def _work(sm: Sessionmaker, importers: Importers, run: Row, deadline: float) -> None:
    """One slice of a run this worker took. If its lease ran out and another worker took the
    run meanwhile, every write it tries raises `LeaseLost` and changes nothing: it stops here
    and leaves the run to its new owner."""
    try:
        await _work_owned(sm, importers, run, deadline)
    except LeaseLost:
        logger.warning("import run taken over by another worker", extra={"event": "import"})


async def _work_owned(sm: Sessionmaker, importers: Importers, run: Row, deadline: float) -> None:
    ws = run["workspace_id"]
    syncing = run["state"] == "done"  # a finished run being asked for changes (M9)
    try:
        if syncing:
            await _sync_slice(sm, importers, run, deadline)
        else:
            await _slice(sm, importers, run, deadline)
    except RateLimited as exc:
        wait = rules.wait_seconds(exc.retry_after)
        await _pause(sm, run, wait, "The source is rate limiting this import.")
    except SourceError as exc:
        if exc.permanent:
            await _stop(sm, ws, run, str(exc))
        else:
            await _retry(sm, importers.retry, run, exc)
    except (CannotOpen, BadFile) as exc:
        await _stop(sm, ws, run, str(exc))
    except ObjectMissing:
        await _stop(sm, ws, run, "The uploaded file is no longer stored. Upload it again.")
    except LeaseLost:
        raise  # not a failure of the run: `_work` stops this worker
    except Exception as exc:
        # Never the message: a database error can quote the customer's data.
        logger.error("import run failed: %s", type(exc).__name__, extra={"event": "import"})
        await _stop(sm, ws, run, "The import stopped on an unexpected error. See the server log.")


SYNC_RETRY_SECONDS = 3600  # a sync look that failed for good is tried again in an hour


async def _stop(sm: Sessionmaker, workspace_id: UUID, run: Row, message: str) -> None:
    """An import that can't go on fails. A sync that can't (a key that was revoked) is not
    ended: it says so in `last_error`, once in the history, and tries again in an hour."""
    if run["state"] != "done":
        await _fail(sm, workspace_id, run, message)
        return
    async with workspace_session(sm, workspace_id) as s:
        await repo.sync_error(
            s, run["id"], error=message, token=run["lease_token"],
            next_run_at=datetime.now(UTC) + timedelta(seconds=SYNC_RETRY_SECONDS),
        )  # fmt: skip
        if run["last_error"] != message[:500]:
            await repo.add_event(
                s, workspace_id, run["connection_id"], "failed", run_id=run["id"],
                detail={"error": message[:300], "sync": True},
            )  # fmt: skip


async def _retry(sm: Sessionmaker, policy: rules.RetryPolicy, run: Row, exc: SourceError) -> None:
    """A temporary error: the page is tried again after a backoff, up to `policy.attempts`
    failed tries, then once more isolating the records that fail (`_slice`). When even that
    try fails, the page can't be read: the run stops there (an import fails; a sync look is
    tried again in an hour), with its cursor still before the page."""
    ws, run_id = run["workspace_id"], run["id"]
    async with workspace_session(sm, ws) as s:
        current = await repo.run(s, run_id)  # pages saved this slice set the count back to 0
        attempts = int((current or run)["retry_attempts"] or 0) + 1
        if attempts <= policy.attempts:
            wait = policy.delay(attempts, exc.retry_after)
            await repo.retry_later(
                s, run_id, next_run_at=datetime.now(UTC) + timedelta(seconds=wait),
                error=str(exc), attempts=attempts, token=run["lease_token"],
            )  # fmt: skip
            return
    await _stop(sm, ws, run, rules.gave_up(run["source"], attempts, str(exc), run["state"]))


async def _pause(sm: Sessionmaker, run: Row, seconds: int, why: str) -> None:
    async with workspace_session(sm, run["workspace_id"]) as s:
        await repo.release(
            s, run["id"], next_run_at=datetime.now(UTC) + timedelta(seconds=seconds),
            token=run["lease_token"], error=why,
        )  # fmt: skip


async def _fail(sm: Sessionmaker, workspace_id: UUID, run: Row, message: str) -> None:
    async with workspace_session(sm, workspace_id) as s:
        await repo.finish(s, run["id"], state="failed", token=run["lease_token"], error=message)
        await repo.add_event(
            s, workspace_id, run["connection_id"], "failed", run_id=run["id"],
            detail={"error": message[:300]},
        )  # fmt: skip
        await audit.record_server(
            s, workspace_id, Area.imports, "failed", f"Import from {rules.label(run['source'])}",
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
        store=importers.store, update_existing=True,
    )  # fmt: skip
    noter = _isolating(connector, ctx, run, importers.retry)
    cursor: dict[str, Any] = dict(run["cursor"])
    counts: dict[str, Any] = dict(run["counts"])
    done: list[str] = list(cursor.get("done", []))
    if "discovered" not in counts:
        counts.update(await _baseline(connector))
    losses = rules.Losses()
    while True:
        kind = rules.next_kind(chosen.kinds, done)
        if kind is None:
            await _complete(sm, importers, ws, run, conn, counts)
            return
        if time.monotonic() >= deadline:
            async with workspace_session(sm, ws) as s:
                await repo.release(
                    s, run_id, next_run_at=datetime.now(UTC), token=run["lease_token"]
                )
            return
        with rules.collecting(losses):
            page = await connector.page(kind, cursor.get("page"))
            items = list(page.items)
            kept = await _prepare(ctx, connector, items) if kind == "tickets" else []
        noted = (noter.take() if noter is not None else []) + ctx.file_notes
        ctx.unresolved = {f.external_id for f in noted if f.kind == "ticket"}
        try:
            async with workspace_session(sm, ws) as s:
                with rules.collecting(losses):
                    stats = await _write(s, ctx, kind, items)
                stats = await _note(s, ctx, run, noted, stats)
                counts[kind] = _add(counts.get(kind, {}), stats)
                counts["losses"] = rules.merge_losses(counts.get("losses"), losses.take())
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
                    token=run["lease_token"],
                )  # fmt: skip
        except Exception:
            await _discard(importers.store, kept)
            losses.take()  # the page wasn't saved: what it counted goes with it
            raise
        _isolate(noter, ctx, False)  # the page is saved: the next one starts with fresh tries


async def _baseline(connector: SourceConnector) -> dict[str, Any]:
    """What the run starts from, for its report (P0-3): the source's own counts (to compare
    with what was imported) and its warnings (what it can't bring). Asked once, on the run's
    first slice; a source that can't say leaves them out, and the run goes on."""
    out: dict[str, Any] = {"discovered": None, "warnings": []}
    try:
        found = await connector.discover()
        out["discovered"] = {k: int(getattr(found, k)) for k in rules.KINDS}
    except (SourceError, RateLimited):
        pass
    warn = getattr(connector, "warnings", None)
    if callable(warn):
        try:
            said = warn()
            if isinstance(said, Awaitable):
                said = await said
            out["warnings"] = [str(w)[:300] for w in list(said or [])[:50]]
        except (SourceError, RateLimited):
            pass
    return out


async def _sync_slice(sm: Sessionmaker, importers: Importers, run: Row, deadline: float) -> None:
    """One look at the source for what changed since the last one, as many pages as the
    time allows. The look's place is in the run's cursor, so a long one carries on."""
    ws, run_id = run["workspace_id"], run["id"]
    async with workspace_session(sm, ws) as s:
        conn = await repo.connection(s, run["connection_id"])
    if conn is None or conn["state"] == "closed":
        async with workspace_session(sm, ws) as s:
            await repo.stop_sync(s, run_id)  # the credentials are gone: nothing to ask with
        return
    connector = await _build(importers, ws, conn)
    if not isinstance(connector, ChangeSource):
        raise SourceError("This source can't be asked what changed.", permanent=True)
    chosen = rules.options(run["options"])
    groups = {g.external_id: g.name for g in await connector.groups()}
    ctx = _Ctx(
        sm=sm, ws=ws, run_id=run_id, source=run["source"], started_by=run["started_by"],
        team_map=chosen.team_map, status_map=connector.status_map, group_names=groups,
        store=importers.store, update_existing=True,
    )  # fmt: skip
    noter = _isolating(connector, ctx, run, importers.retry)
    mark: dict[str, Any] = dict((run["cursor"] or {}).get("sync") or {})
    now = datetime.now(UTC)
    started = datetime.fromisoformat(mark["started"]) if mark.get("started") else now
    if mark.get("since"):
        since = datetime.fromisoformat(mark["since"])
    else:  # a little before the last look, so a change made while it ran is not missed
        since = (run["last_sync_at"] or run["started_at"]) - timedelta(
            seconds=rules.SYNC_OVERLAP_SECONDS
        )
    counts: dict[str, Any] = dict(run["counts"])
    total: dict[str, int] = dict(counts.get("sync") or {})
    this_look: dict[str, int] = dict(mark.get("look") or {})
    page_cursor: Mapping[str, Any] | None = mark.get("page")
    losses = rules.Losses()
    while True:
        if time.monotonic() >= deadline:
            async with workspace_session(sm, ws) as s:
                await repo.release(
                    s, run_id, next_run_at=datetime.now(UTC), token=run["lease_token"]
                )
            return
        with rules.collecting(losses):
            page = await connector.changed_tickets(since, page_cursor)
            items = list(page.items)
            kept = await _prepare(ctx, connector, items)
        noted = (noter.take() if noter is not None else []) + ctx.file_notes
        ctx.unresolved = {f.external_id for f in noted if f.kind == "ticket"}
        try:
            async with workspace_session(sm, ws) as s:
                with rules.collecting(losses):
                    stats = await _tickets(s, ctx, items)
                stats = await _note(s, ctx, run, noted, stats)
                total, this_look = _add(total, stats), _add(this_look, stats)
                counts["sync"] = total
                counts["losses"] = rules.merge_losses(counts.get("losses"), losses.take())
                if page.cursor is None:
                    done_at = datetime.now(UTC)
                    await repo.sync_pass_done(
                        s, run_id, last_sync_at=started, counts=counts,
                        next_run_at=done_at + timedelta(seconds=rules.SYNC_EVERY_SECONDS),
                        token=run["lease_token"],
                    )  # fmt: skip
                else:
                    page_cursor = dict(page.cursor)
                    mark = {
                        "since": since.isoformat(), "started": started.isoformat(),
                        "page": page_cursor, "look": this_look,
                    }  # fmt: skip
                    await repo.save_progress(
                        s, run_id, cursor={"sync": mark}, counts=counts,
                        lease_until=datetime.now(UTC) + timedelta(seconds=rules.LEASE_SECONDS),
                        token=run["lease_token"],
                    )  # fmt: skip
        except Exception:
            await _discard(importers.store, kept)
            raise
        _isolate(noter, ctx, False)
        if page.cursor is None:
            return


# Records left out (P0-2)


def _isolate(noter: rules.RecordFailures | None, ctx: _Ctx, on: bool) -> None:
    if noter is not None:
        noter.isolate = on


def _isolating(
    connector: SourceConnector, ctx: _Ctx, run: Row, policy: rules.RetryPolicy
) -> rules.RecordFailures | None:
    """The connector's notes of records it left out (None: it notes none, as CSV), set to
    isolate failing records when this slice is the last try after the run's retries."""
    found = getattr(connector, "failures", None)
    noter = found if isinstance(found, rules.RecordFailures) else None
    _isolate(noter, ctx, int(run["retry_attempts"] or 0) >= policy.attempts)
    return noter


async def _note(
    s: AsyncSession, ctx: _Ctx, run: Row, noted: Sequence[rules.Failure], stats: dict[str, int]
) -> dict[str, int]:
    """Writes the page's noted records in its own transaction, before its cursor moves, and
    counts them: `failed` (left out) and `incomplete` (imported without all its history)."""
    if not noted:
        return stats
    await repo.note_failures(
        s, ctx.ws, ctx.run_id, ctx.source, noted, attempts=int(run["retry_attempts"] or 0) + 1
    )
    last = {(f.kind, f.external_id): f.category for f in noted}  # noted twice: the last
    partial = sum(1 for category in last.values() if category == "incomplete")
    extra = {"failed": len(last) - partial, "incomplete": partial}
    return _add(stats, {k: n for k, n in extra.items() if n})


# Files


async def _discard(store: ObjectStore, keys: Sequence[str]) -> None:
    for key in keys:
        try:
            await store.delete(key)
        except Exception:  # best effort: an unreferenced object is harmless
            logger.warning("import file left in storage", extra={"event": "import"})


def _is_new(message: Message, known: tuple[set[str], set[tuple[str, int]]] | None) -> bool:
    """Whether a source message is not on the CeDesk ticket yet. Without a known ticket, all
    are. A message with a source id is known by it; one without, by (kind, second)."""
    if known is None:
        return True
    ids, stamps = known
    if message.external_id:
        return message.external_id[:200] not in ids
    return (message.kind, int(message.created_at.timestamp())) not in stamps


def _worth_keeping(message: Message) -> bool:
    return bool(rules.body(message.body)) or bool(message.attachments)


async def _prepare(ctx: _Ctx, connector: SourceConnector, items: list[Ticket]) -> list[str]:
    """Before a page of tickets is written: which are already here, and the files of the
    messages about to be added, copied to the object store. Runs outside any transaction (a
    download can be slow). Returns the stored keys, for `_discard` if the page then fails."""
    unique = {t.external_id: t for t in items}
    async with workspace_session(ctx.sm, ctx.ws) as s:
        ctx.have = await repo.ticket_ids_by_source(s, ctx.source, list(unique))
        # An archived ticket (md O3) was imported already: skipped, never made again, and a
        # sync look leaves it as archived (it is read only).
        ctx.archived = await archive_repo.source_ids(s, ctx.source, list(unique))
        ctx.seen = (
            await repo.message_keys(s, list(ctx.have.values())) if ctx.update_existing else {}
        )
    ctx.ticket_ids = {ext: ctx.have.get(ext) or uuid4() for ext in unique}
    ctx.files = {}
    ctx.file_notes = []
    stored: list[str] = []
    files = connector if isinstance(connector, FileSource) else None
    try:
        for ext, ticket in unique.items():
            known_id = ctx.have.get(ext)
            if ext in ctx.archived or (known_id is not None and not ctx.update_existing):
                continue
            known = ctx.seen.get(known_id) if known_id is not None else None
            for message in ticket.messages:
                if not (_worth_keeping(message) and _is_new(message, known)):
                    continue
                kept: list[_Kept | _Linked] = []
                if len(rules.body(message.body)) > rules.BODY_MAX:
                    kept.append(await _body_file(ctx, ctx.ticket_ids[ext], message, stored))
                if message.attachments:
                    kept += await _copy_files(ctx, files, ctx.ticket_ids[ext], ext, message, stored)
                if kept:
                    ctx.files[id(message)] = kept
    except BaseException:
        await _discard(ctx.store, stored)
        raise
    return stored


async def _body_file(ctx: _Ctx, ticket_id: UUID, message: Message, stored: list[str]) -> _Kept:
    """A message longer than `hd_message.body` holds: its whole text, as a .txt file on the
    message (the message itself shows the start and says so). Nothing is cut (P0-6)."""
    attachment_id = uuid4()
    key = object_key(ctx.ws, ticket_id, attachment_id)
    data = rules.body(message.body).encode()
    await ctx.store.put(key, data, content_type="text/plain")
    stored.append(key)
    rules.loss("body_as_file")
    return _Kept(attachment_id, key, "full-message.txt", len(data), "text/plain")


FILE_TRIES = 3  # a file that fails for a moment is tried this often, then kept as a link
FILE_RETRY_SECONDS = 2.0  # the wait before the 2nd try; doubled before the 3rd


async def _fetch(connector: FileSource, att: rules.Attachment) -> tuple[bytes | None, str]:
    """The file's bytes, or None and why not. A temporary error is tried `FILE_TRIES` times
    with a short wait; a 429 still pauses the whole run (`RateLimited` is raised). One file
    never holds a page: before (live B-29, B-42) a host the server couldn't reach made the
    page retry for hours and the import stalled."""
    for attempt in range(1, FILE_TRIES + 1):
        try:
            data = await connector.fetch(att, rules.FILE_MAX)
        except rules.RecordGone:
            return None, "file_gone"
        except SourceError as exc:
            if exc.permanent:
                return None, "file_gone"
            if attempt == FILE_TRIES:
                return None, "file_unreachable"
            await asyncio.sleep(FILE_RETRY_SECONDS * 2 ** (attempt - 1))
            continue
        return data, "" if data is not None else "file_gone"
    return None, "file_unreachable"  # pragma: no cover


_LINK_WHY = {
    "files_over_limit": "more than 20 files on the message",
    "file_too_big": "over 25 MB",
    "file_gone": "over 25 MB or no longer in the source",
    "file_unreachable": "the file's host couldn't be reached",
    "file_empty": "an empty file",
    "file_no_access": "this source's files can't be read by the importer",
}


async def _copy_files(
    ctx: _Ctx,
    connector: FileSource | None,
    ticket_id: UUID,
    external_id: str,
    message: Message,
    stored: list[str],
) -> list[_Kept | _Linked]:
    """Copies the message's files to the object store. A file that can't be copied (past
    20 on the message, over 25 MB, gone, unreachable, empty) is never dropped: it becomes a
    link line, is counted by reason, and one that should have been copied (gone,
    unreachable) notes its ticket `incomplete` with why (P0-3, P0-6)."""
    out: list[_Kept | _Linked] = []
    trouble: dict[str, int] = {}
    for i, att in enumerate(message.attachments):
        data: bytes | None = None
        if connector is None:
            why = "file_no_access"
        elif i >= rules.FILES_PER_MESSAGE:
            why = "files_over_limit"
        elif att.size is not None and att.size > rules.FILE_MAX:
            why = "file_too_big"
        else:
            data, why = await _fetch(connector, att)
            if data is not None and not data:
                why = "file_empty"
        if why:
            out.append(_Linked(rules.link_line(att.name, att.size, att.url, _LINK_WHY[why])))
            if why in rules.LOSS_KEYS:
                rules.loss(why)
            if why in ("file_gone", "file_unreachable"):
                trouble[why] = trouble.get(why, 0) + 1
            continue
        assert data is not None
        attachment_id = uuid4()
        key = object_key(ctx.ws, ticket_id, attachment_id)
        mime = clean_mime(att.mime)
        await ctx.store.put(key, data, content_type=mime)
        stored.append(key)
        out.append(_Kept(attachment_id, key, clean_name(att.name), len(data), mime))
    if trouble:
        said = "; ".join(f"{n} {_LINK_WHY[w]}" for w, n in sorted(trouble.items()))
        ctx.file_notes.append(
            rules.Failure("ticket", external_id[:200], "incomplete",
                          f"Files kept as links instead of copies: {said}.")
        )  # fmt: skip
    return out


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
        # Locked: a sync stop sent now waits, then sees the run done and deletes the
        # credentials; one sent before shows here as sync off.
        current = await repo.run(s, run["id"], lock=True)
        syncing = bool(current and current["sync_on"])
        if syncing:
            now = datetime.now(UTC)
            await repo.finish_to_sync(
                s, run["id"], last_sync_at=run["started_at"], counts=counts,
                next_run_at=now + timedelta(seconds=rules.SYNC_EVERY_SECONDS),
                token=run["lease_token"],
            )  # fmt: skip
        else:
            await repo.finish(s, run["id"], state="done", token=run["lease_token"])
        await repo.add_event(
            s, ws, run["connection_id"], "finished", run_id=run["id"], detail=counts
        )
        await audit.record_server(
            s, ws, Area.imports, "finished", f"Import from {rules.label(run['source'])}",
            detail={"syncing": syncing},
        )  # fmt: skip
        if not syncing and await repo.close_connection(s, run["connection_id"]):
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
    """Writes a page prepared by `_prepare`: new tickets are created; ones already here get
    their new messages, status, owner and new tags (an import run again, or a sync look), and
    count as skipped when nothing changed. Archived ones (md O3) are always skipped."""
    unique = {t.external_id: t for t in items}
    fresh = [t for key, t in unique.items() if key not in ctx.have and key not in ctx.archived]
    stats = {
        "created": 0, "skipped": len(unique) - len(fresh) + len(items) - len(unique),
        "updated": 0, "new_messages": 0, "unknown_agent_messages": 0,
        "files": 0, "files_linked": 0,
    }  # fmt: skip
    cfg = ctx.cfg = ctx.cfg or await triage.config(s)
    ids: list[UUID] = []
    if fresh:
        # The numbers are taken in their own short transaction: the counter row stays locked
        # only for that statement, not for the whole batch.
        async with workspace_session(ctx.sm, ctx.ws) as reserved:
            first = await ticket_repo.reserve_numbers(reserved, ctx.ws, len(fresh))
        for offset, ticket in enumerate(fresh):
            ids.append(await _ticket(s, ctx, cfg, ticket, first + offset, stats))
            stats["created"] += 1
    if ctx.update_existing:
        for key, ticket in unique.items():
            if key in ctx.have:
                ticket_id, changed = await _refresh(s, ctx, cfg, ctx.have[key], ticket, stats)
                if changed:  # one with nothing new stays "skipped"
                    stats["skipped"] -= 1
                    ids.append(ticket_id)
    # An old open ticket has a due time in the past; the SLA code files that breach as
    # already alerted when a change (an import) makes it, so nobody is paged for it.
    if ids:
        await sla.refresh(s, ticket_ids=ids)
    # Their failures are resolved only when this read got all of them: a ticket noted on this
    # page (part of its history missing) stays open however often it is imported (P0-2).
    complete = [key for key in unique if key not in ctx.unresolved]
    await repo.resolve_failures(s, ctx.source, "ticket", complete)
    return stats


_CUT_NOTE = "\n\n[This message is longer than CeDesk shows: the whole text is in full-message.txt.]"


async def _add_message(
    s: AsyncSession,
    ctx: _Ctx,
    cfg: triage.Config,
    ticket_id: UUID,
    t: Ticket,
    message: Message,
    seen: set[str],
    removed: set[Any],
    stats: dict[str, int],
) -> set[Any]:
    """One source message on a ticket, with its files; returns the card numbers removed so far."""
    body = rules.body(message.body) or ("(attachment)" if message.attachments else "")
    if len(body) > rules.BODY_MAX:  # the whole text is on the message as a .txt (`_body_file`)
        body = body[: rules.BODY_MAX - len(_CUT_NOTE)] + _CUT_NOTE
    author_member, name = None, message.author_name
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
    message_id = await repo.insert_message(
        s, workspace_id=ctx.ws, ticket_id=ticket_id, kind=message.kind, body=body,
        created_at=message.created_at, author_member_id=author_member, author_name=name,
        channel_message_id=external,
    )  # fmt: skip
    for file in ctx.files.get(id(message), []):
        if isinstance(file, _Kept):
            await attach_repo.insert(
                s, attachment_id=file.attachment_id, workspace_id=ctx.ws, ticket_id=ticket_id,
                object_key=file.key, name=file.name, size=file.size, mime=file.mime,
                uploaded_by=None, message_id=message_id,
            )  # fmt: skip
            stats["files"] += 1
        else:
            await repo.insert_message(
                s, workspace_id=ctx.ws, ticket_id=ticket_id, kind="system", body=file.line,
                created_at=message.created_at + timedelta(milliseconds=1),
                author_member_id=None, author_name=None, channel_message_id=None,
            )  # fmt: skip
            stats["files_linked"] += 1
    return removed


async def _refresh(
    s: AsyncSession,
    ctx: _Ctx,
    cfg: triage.Config,
    ticket_id: UUID,
    t: Ticket,
    stats: dict[str, int],
) -> tuple[UUID, bool]:
    """A ticket already imported, seen again (a sync look, or the import run again): its
    messages that aren't here yet, where the source now has its status (M9), its assignee
    and team when they map to CeDesk, and tags it gained. No message is changed or removed,
    no tag is taken off, and an owner the source doesn't name is left as it is. Returns the
    ticket and whether anything changed."""
    known = ctx.seen.get(ticket_id, (set(), set()))
    messages = sorted((m for m in t.messages if _worth_keeping(m)), key=lambda m: m.created_at)
    fresh = [m for m in messages if _is_new(m, known)]
    seen = set(known[0])
    removed: set[Any] = set()
    for message in fresh:
        removed = await _add_message(s, ctx, cfg, ticket_id, t, message, seen, removed, stats)
    last = messages[-1].created_at if messages else t.created_at
    if removed:
        await repo.insert_message(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, kind="system",
            body=triage.removed_line(len(removed)), created_at=last, author_member_id=None,
            author_name=None, channel_message_id=None,
        )  # fmt: skip
    state = rules.status(t.status, ctx.status_map)
    resolved_at = (t.resolved_at or t.updated_at or last) if state == "resolved" else None
    changed = await repo.refresh_ticket(
        s, ticket_id, status=state, priority=rules.priority(t.priority), resolved_at=resolved_at,
        first_reply_at=next((m.created_at for m in messages if m.kind == "agent"), None),
        updated_at=max(t.updated_at or t.created_at, last),
    )  # fmt: skip
    if t.csat is not None:  # a rating is history whatever the ticket's status now (P1-12)
        contact_id = await _contact(s, ctx, t.requester)
        await repo.insert_csat(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, contact_id=contact_id,
            score=t.csat.score, comment=(t.csat.comment or "")[:2000] or None,
            source=ctx.source, rated_at=t.csat.rated_at or resolved_at or last,
        )  # fmt: skip
    if fresh:
        await triage.retriage(s, ticket_id, cfg=cfg)
        stats["new_messages"] += len(fresh)
    assignee = await _member(s, ctx, t.assignee_email)
    team = ctx.team_map.get(t.group_id) if t.group_id else None
    new_owner, new_team, tags_added = await repo.refresh_people(
        s, ticket_id, assignee_id=assignee["id"] if assignee else None, team_id=team,
        tags=rules.clean_tags(t.tags, ctx.source), tag_max=rules.TAG_MAX,
    )  # fmt: skip
    notes = []
    if new_owner and assignee:
        notes.append(f"assigned to {assignee['name'] or assignee['email']}")
    if new_team:
        notes.append(f"team {ctx.group_names.get(t.group_id or '', t.group_id)}")
    if tags_added:
        notes.append("tags added: " + ", ".join(tags_added))
    if notes:
        await repo.insert_message(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, kind="system",
            body=f"Updated from {rules.label(ctx.source)}: " + "; ".join(notes),
            created_at=datetime.now(UTC), author_member_id=None, author_name=None,
            channel_message_id=None,
        )  # fmt: skip
    touched = bool(fresh or changed or notes)
    if touched:  # a status-only change is an update too
        stats["updated"] += 1
    return ticket_id, touched


def _fields(t: Ticket, source: str) -> dict[str, str]:
    """The ticket's custom fields, plus what CeDesk has no column for yet, so nothing the
    source gave is lost: tags past CeDesk's 20 (`more_tags`), the source's ticket type and
    its address in the source."""
    out = rules.clean_fields(t.fields)
    extra = {
        "more_tags": rules.more_tags(t.tags, source),
        "source_type": t.ticket_type,
        "source_url": t.source_url,
    }
    for key, value in extra.items():
        if value and key not in out:
            out[key] = str(value)[: rules.FIELD_TEXT_MAX]
    if extra["more_tags"]:
        rules.loss("tags_over_limit")
    return out


async def _ticket(
    s: AsyncSession, ctx: _Ctx, cfg: triage.Config, t: Ticket, number: int, stats: dict[str, int]
) -> UUID:
    contact_id = await _contact(s, ctx, t.requester)
    state = rules.status(t.status, ctx.status_map)
    messages = sorted((m for m in t.messages if _worth_keeping(m)), key=lambda m: m.created_at)
    last = messages[-1].created_at if messages else t.created_at
    resolved_at = (t.resolved_at or t.updated_at or last) if state == "resolved" else None
    assignee = await _member(s, ctx, t.assignee_email)
    subject = triage.clean(cfg, rules.subject(t.subject), rules.SUBJECT_MAX)
    removed = set(subject.numbers)
    ticket_id = await repo.insert_ticket(
        s,
        {
            "id": ctx.ticket_ids[t.external_id],
            "workspace_id": ctx.ws,
            "number": number,
            "subject": subject.text or rules.subject(None),
            "status": state,
            "priority": rules.priority(t.priority),
            "team_id": ctx.team_map.get(t.group_id) if t.group_id else None,
            "assignee_id": assignee["id"] if assignee else None,
            "contact_id": contact_id,
            "channel": rules.channel(t.channel),
            "fields": _fields(t, ctx.source),
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
    removed_numbers: set[Any] = set(removed)
    for message in messages:
        removed_numbers = await _add_message(
            s, ctx, cfg, ticket_id, t, message, seen, removed_numbers, stats
        )
    removed = removed_numbers
    if removed:
        await repo.insert_message(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, kind="system",
            body=triage.removed_line(len(removed)), created_at=last, author_member_id=None,
            author_name=None, channel_message_id=None,
        )  # fmt: skip
    if t.csat is not None:  # a rating is history whatever the ticket's status now (P1-12)
        await repo.insert_csat(
            s, workspace_id=ctx.ws, ticket_id=ticket_id, contact_id=contact_id,
            score=t.csat.score, comment=(t.csat.comment or "")[:2000] or None,
            source=ctx.source, rated_at=t.csat.rated_at or resolved_at or last,
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
