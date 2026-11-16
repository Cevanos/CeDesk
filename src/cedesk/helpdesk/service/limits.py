"""Cloud plan limits, the usage meter and retention (md O2, O4, O5). Rules in
domain/limits.py.

Retention can export before it deletes (O2, the Admin's `export_before_delete`): each
batch it deletes is first written to one JSON file in storage (the privacy export's
ticket shape, with each ticket's customer), kept 30 days for an Admin to download, then
deleted by the worker. Erasing a customer deletes the files that hold their tickets.
With the switch on and no storage, nothing is deleted (never without the export asked).

`configure` is called once at start (the API and the worker) with the edition and the
plans from config. `check` runs inside the transaction of the change it guards, so two
changes at once can't both slip under a limit by more than one.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import exports
from cedesk.helpdesk.domain import limits as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.limits import Kind, Plan
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.reports import IMPORT_SOURCES
from cedesk.helpdesk.repo import archive as archive_repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import limits as repo
from cedesk.helpdesk.repo import privacy as privacy_repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.audit import require
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound, OverPlan
from cedesk.storage import ObjectMissing, ObjectStore

logger = logging.getLogger("cedesk.limits")


@dataclass
class _Config:
    enabled: bool = True  # cloud by default, as the settings are
    plans: dict[str, Plan] | None = None


_config = _Config(plans=dict(rules.DEFAULT_PLANS))


def configure(*, cloud: bool, plans: dict[str, Plan]) -> None:
    _config.enabled = cloud
    _config.plans = plans


def plan_named(name: str | None) -> Plan | None:
    if name is None or not _config.enabled:
        return None
    return (_config.plans or {}).get(name)


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _used(s: AsyncSession, kind: Kind) -> int:
    if kind is Kind.agents:
        return await repo.agents(s)
    if kind is Kind.tickets_month:
        return await repo.tickets_since(s, month_start(datetime.now(UTC)), IMPORT_SOURCES)
    return await repo.storage_bytes(s)


async def check(s: AsyncSession, kind: Kind, adding: int = 1) -> None:
    """Raises `OverPlan` (402) when `adding` more would go over the workspace's plan."""
    plan = plan_named(await repo.plan_name(s))
    if plan is None or plan.limit(kind) is None:
        return
    reason = rules.refusal(plan, kind, await _used(s, kind), adding)
    if reason is not None:
        raise OverPlan(reason)


@dataclass(frozen=True)
class Usage:
    plan: str | None
    agents: int
    tickets_this_month: int
    storage_bytes: int
    limits: dict[str, int | None]
    retention_days: int | None  # what the nightly job keeps resolved tickets for


async def usage(sm: Sessionmaker, caller: Caller) -> Usage:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        name = await repo.plan_name(s)
        plan = plan_named(name)
        agents = await repo.agents(s)
        tickets = await repo.tickets_since(s, month_start(datetime.now(UTC)), IMPORT_SOURCES)
        storage = await repo.storage_bytes(s)
        kept = await repo.retention(s)
    setting = kept["closed_ticket_days"] if kept is not None else None
    return Usage(
        plan=name if plan is not None else None,
        agents=agents,
        tickets_this_month=tickets,
        storage_bytes=storage,
        limits={k.value: plan.limit(k) if plan else None for k in Kind},
        retention_days=rules.retention_days(setting, plan),
    )


async def set_plan(sm: Sessionmaker, slug: str, plan: str | None) -> UUID:
    """From the server's command line only (`cedesk set-plan`): our cloud's operators give a
    workspace its plan, or take it off (None: no limits). Audited as a server change."""
    if plan is not None and plan not in (_config.plans or {}):
        raise Invalid(f"No plan {plan!r}; plans: {', '.join(sorted(_config.plans or {}))}.")
    async with sm() as s, s.begin():
        workspace_id = await auth_repo.workspace_for_slug(s, slug.strip().lower())
    if workspace_id is None:
        raise Invalid(f"No workspace {slug!r}.")
    async with workspace_session(sm, workspace_id) as s:
        before = await repo.plan_name(s)
        await repo.set_plan(s, workspace_id, plan)
        if before != plan:
            await audit.record_server(
                s, workspace_id, Area.plan, "changed", "Cloud plan",
                detail={"changes": {"plan": [before, plan]}},
            )  # fmt: skip
    return workspace_id


# Retention (md O2)


@dataclass(frozen=True)
class RetentionView:
    closed_ticket_days: int | None  # the Admin's setting; None: keep
    plan_history_days: int | None  # the plan's cap, when there is one
    effective_days: int | None
    export_before_delete: bool  # write what is deleted to a file first (O2)
    updated_at: datetime | None
    updated_by_name: str | None


async def get_retention(sm: Sessionmaker, caller: Caller) -> RetentionView:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.retention(s)
        plan = plan_named(await repo.plan_name(s))
    return _retention_view(row, plan)


def _retention_view(row: Any, plan: Plan | None) -> RetentionView:
    days = row["closed_ticket_days"] if row is not None else None
    return RetentionView(
        closed_ticket_days=days,
        plan_history_days=plan.history_days if plan else None,
        effective_days=rules.retention_days(days, plan),
        export_before_delete=bool(row["export_before_delete"]) if row is not None else False,
        updated_at=row["updated_at"] if row is not None else None,
        updated_by_name=row["updated_by_name"] if row is not None else None,
    )


async def set_retention(
    sm: Sessionmaker, caller: Caller, days: int | None, export_before_delete: bool | None = None
) -> RetentionView:
    """`export_before_delete` None keeps the saved switch."""
    require(caller, Action.settings)
    if days is not None and not rules.MIN_RETENTION_DAYS <= days <= rules.MAX_RETENTION_DAYS:
        raise Invalid(
            f"Keep resolved tickets {rules.MIN_RETENTION_DAYS} to "
            f"{rules.MAX_RETENTION_DAYS} days, or for good (null)."
        )
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.retention(s)
        old = before["closed_ticket_days"] if before is not None else None
        old_export = bool(before["export_before_delete"]) if before is not None else False
        await repo.save_retention(
            s, caller.workspace_id, days, caller.member_id, export=export_before_delete
        )
        changes: dict[str, list[Any]] = {}
        if old != days:
            changes["closed_ticket_days"] = [old, days]
        if export_before_delete is not None and export_before_delete != old_export:
            changes["export_before_delete"] = [old_export, export_before_delete]
        if changes:
            await audit.record(
                s, caller, Area.retention, "changed", "Delete resolved tickets after",
                detail={"changes": changes},
            )  # fmt: skip
        row = await repo.retention(s)
        plan = plan_named(await repo.plan_name(s))
    return _retention_view(row, plan)


async def run_retention(
    sm: Sessionmaker, store: ObjectStore | None, *, now: datetime | None = None
) -> int:
    """Once a day (the worker): in each workspace with a retention (its setting or its
    plan's history), deletes resolved tickets older than that and their files, live or
    archived (md O3), and audits the run. Returns how many tickets went."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    total = 0
    for workspace_id in workspace_ids:
        total += await _retain(sm, store, workspace_id, now)
    return total


async def _retain(
    sm: Sessionmaker, store: ObjectStore | None, workspace_id: UUID, now: datetime
) -> int:
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.retention(s)
        plan = plan_named(await repo.plan_name(s))
    days = rules.retention_days(row["closed_ticket_days"] if row else None, plan)
    if days is None:
        return 0
    export = bool(row["export_before_delete"]) if row else False
    if export and store is None:
        logger.warning("retention: export asked but no storage; nothing deleted",
                       extra={"workspace_id": workspace_id, "event": "retention"})  # fmt: skip
        return 0
    cutoff = now - timedelta(days=days)
    deleted = archived = exported = 0
    for in_archive in (False, True):  # the archive tier (md O3): no longer than retention
        while True:
            key: str | None = None
            try:
                async with workspace_session(sm, workspace_id) as s:
                    if in_archive:
                        batch = await archive_repo.resolved_before(s, cutoff, rules.RETENTION_BATCH)
                    else:
                        batch = await repo.resolved_before(s, cutoff, rules.RETENTION_BATCH)
                    if not batch:
                        break
                    ids = [r["id"] for r in batch]
                    if export:
                        assert store is not None
                        key = await _export(s, store, workspace_id, ids, in_archive, days, now)
                        exported += 1
                    if in_archive:
                        archived += await archive_repo.delete(s, ids)
                    else:
                        deleted += await repo.delete_tickets(s, ids)
            except BaseException:
                if key is not None and store is not None:
                    await store.delete(key)  # the tickets stay: no export without them
                raise
            await _delete_files(store, batch)
            if len(batch) < rules.RETENTION_BATCH:
                break
    if deleted or archived:
        detail: dict[str, int] = {"tickets": deleted, "days": days}
        if archived:
            detail["archived"] = archived
        if exported:
            detail["exports"] = exported
        async with workspace_session(sm, workspace_id) as s:
            await audit.record_server(
                s, workspace_id, Area.retention, "deleted",
                f"{deleted + archived} resolved tickets older than {days} days",
                detail=detail,
            )  # fmt: skip
        logger.info("retention", extra={"workspace_id": workspace_id, "event": "retention"})
    return deleted + archived


def _customer(row: Any) -> dict[str, Any]:
    cid = row["contact_id"]
    return {"customer": {"id": str(cid) if cid else None, "email": row["contact_email"],
                         "name": row["contact_name"]}}  # fmt: skip


def export_key(workspace_id: UUID, export_id: UUID) -> str:
    return f"{workspace_id}/retention/{export_id}.json"


async def _export(
    s: AsyncSession,
    store: ObjectStore,
    workspace_id: UUID,
    ids: Sequence[UUID],
    archived: bool,
    days: int,
    now: datetime,
) -> str:
    """O2: the batch about to be deleted, written to storage first. Returns its key."""
    if archived:
        rows = await archive_repo.for_export(s, ids)
        tickets = [exports.archived_export(r) | _customer(r) for r in rows]
    else:
        rows = await repo.tickets_for_export(s, ids)
        built = exports.export_tickets(
            rows, await privacy_repo.messages_of(s, list(ids)),
            await privacy_repo.files_of(s, list(ids)), await repo.ratings_for_export(s, ids),
        )  # fmt: skip
        tickets = [t | _customer(r) for t, r in zip(built, rows, strict=True)]
    data = {
        "exported_at": now.astimezone(UTC).isoformat(),
        "workspace": await privacy_repo.workspace_name(s),
        "about": f"Resolved tickets deleted by retention, {days} days after they were "
        "resolved, written here first. Messages of kind 'note' are the team's internal "
        "notes. Files are listed by name; the files themselves are deleted.",
        "tickets": tickets,
    }
    body = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode()
    export_id = uuid4()
    key = export_key(workspace_id, export_id)
    await store.put(key, body, content_type="application/json")
    contacts = sorted({r["contact_id"] for r in rows if r["contact_id"]}, key=str)
    await repo.insert_export(
        s, workspace_id, export_id=export_id, key=key, tickets=len(rows), size=len(body),
        contact_ids=contacts, expires_at=now + timedelta(days=rules.KEEP_EXPORT_DAYS),
    )  # fmt: skip
    return key


@dataclass(frozen=True)
class RetentionExport:
    id: UUID
    tickets: int
    size: int
    created_at: datetime
    expires_at: datetime


async def retention_exports(sm: Sessionmaker, caller: Caller) -> list[RetentionExport]:
    """The files retention wrote before deleting, newest first (Admins: customer data)."""
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.exports(s, 100)
    return [
        RetentionExport(r["id"], r["tickets"], r["size"], r["created_at"], r["expires_at"])
        for r in rows
    ]


async def retention_export_file(
    sm: Sessionmaker, store: ObjectStore, caller: Caller, export_id: UUID
) -> exports.ExportFile:
    """One export's JSON, downloaded by an Admin (audited)."""
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.export(s, export_id)
        if row is None:
            raise NotFound("Retention export not found.")
        try:
            data = await store.get(str(row["object_key"]))
        except ObjectMissing as exc:
            raise Conflict("This export's file is gone.") from exc
        await audit.record(
            s, caller, Area.retention, "downloaded", f"Retention export {export_id}",
            target_id=export_id, detail={"tickets": row["tickets"]},
        )  # fmt: skip
    return exports.ExportFile(f"retention-{row['created_at']:%Y-%m-%d}-{export_id}.json", data)


async def remove_expired_retention_exports(sm: Sessionmaker, store: ObjectStore) -> int:
    """Deletes retention exports past their 30 days, in every workspace (the worker)."""
    now = datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    removed = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            for row in await repo.expired_exports(s, now):
                await store.delete(str(row["object_key"]))
                await repo.forget_export(s, row["id"])
                removed += 1
    return removed


async def _delete_files(store: ObjectStore | None, batch: Any) -> None:
    """After the rows are gone: a file left behind by a crash is only space."""
    for r in batch:
        for key in r["keys"]:
            if store is None:
                continue
            try:
                await store.delete(str(key))
            except ObjectMissing:
                pass
