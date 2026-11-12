"""Cloud plan limits, the usage meter and retention (md O2, O4, O5). Rules in
domain/limits.py.

`configure` is called once at start (the API and the worker) with the edition and the
plans from config. `check` runs inside the transaction of the change it guards, so two
changes at once can't both slip under a limit by more than one.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import limits as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.limits import Kind, Plan
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.reports import IMPORT_SOURCES
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import limits as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.audit import require
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, OverPlan
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
        updated_at=row["updated_at"] if row is not None else None,
        updated_by_name=row["updated_by_name"] if row is not None else None,
    )


async def set_retention(sm: Sessionmaker, caller: Caller, days: int | None) -> RetentionView:
    require(caller, Action.settings)
    if days is not None and not rules.MIN_RETENTION_DAYS <= days <= rules.MAX_RETENTION_DAYS:
        raise Invalid(
            f"Keep resolved tickets {rules.MIN_RETENTION_DAYS} to "
            f"{rules.MAX_RETENTION_DAYS} days, or for good (null)."
        )
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.retention(s)
        old = before["closed_ticket_days"] if before is not None else None
        await repo.save_retention(s, caller.workspace_id, days, caller.member_id)
        if old != days:
            await audit.record(
                s, caller, Area.retention, "changed", "Delete resolved tickets after",
                detail={"changes": {"closed_ticket_days": [old, days]}},
            )  # fmt: skip
        row = await repo.retention(s)
        plan = plan_named(await repo.plan_name(s))
    return _retention_view(row, plan)


async def run_retention(
    sm: Sessionmaker, store: ObjectStore | None, *, now: datetime | None = None
) -> int:
    """Once a day (the worker): in each workspace with a retention (its setting or its
    plan's history), deletes resolved tickets older than that and their files, and audits
    the run. Returns how many tickets went."""
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
    cutoff = now - timedelta(days=days)
    deleted = 0
    while True:
        async with workspace_session(sm, workspace_id) as s:
            batch = await repo.resolved_before(s, cutoff, rules.RETENTION_BATCH)
            if not batch:
                break
            deleted += await repo.delete_tickets(s, [r["id"] for r in batch])
        # Files after the rows are gone: a file left behind by a crash is only space.
        for r in batch:
            for key in r["keys"]:
                if store is None:
                    continue
                try:
                    await store.delete(str(key))
                except ObjectMissing:
                    pass
        if len(batch) < rules.RETENTION_BATCH:
            break
    if deleted:
        async with workspace_session(sm, workspace_id) as s:
            await audit.record_server(
                s, workspace_id, Area.retention, "deleted",
                f"{deleted} resolved tickets older than {days} days",
                detail={"tickets": deleted, "days": days},
            )  # fmt: skip
        logger.info("retention", extra={"workspace_id": workspace_id, "event": "retention"})
    return deleted
