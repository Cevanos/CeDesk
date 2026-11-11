"""SLA on tickets (md W9, W13, T9), built on the clock logic in `domain/sla.py`.

- `config` reads the workspace's settings; anything not saved yet is the mock's default.
- `state` works out a ticket's pill when it is read.
- `refresh` stores each ticket's current due time (`sla_due_at`) for the `sla` list sort.
  Every change to an input (priority, status, first reply, VIP, targets) calls it, in the
  same transaction.
- Admins change the priority targets (W9). Policies, calendar and holidays are read-only
  for now (editing them is Phase 2, W10-W12).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import sla as clocks
from cedesk.helpdesk.domain import sla_settings as defaults
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import sla as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid
from cedesk.helpdesk.service.members import require


@dataclass(frozen=True)
class Config:
    policies: tuple[clocks.Policy, ...]
    targets: Mapping[str, clocks.Target]
    calendar: clocks.Calendar
    holiday_names: Mapping[Any, str]


@dataclass(frozen=True)
class Settings:
    config: Config
    saved_targets: frozenset[str]  # priorities an Admin has set (the rest are defaults)
    custom_policies: bool
    custom_calendar: bool


def _policy(row: Mapping[Any, Any]) -> clocks.Policy:
    return clocks.Policy(
        name=str(row["name"]),
        first_reply_minutes=row["first_reply_min"],
        resolve_minutes=row["resolve_min"],
        vip_only=bool(row["vip_only"]),
        channels=frozenset(row["channels"]),
        priorities=frozenset(row["priorities"]),
        enabled=bool(row["enabled"]),
    )


async def _settings(s: AsyncSession) -> Settings:
    target_rows = await repo.targets(s)
    saved = {
        str(r["priority"]): clocks.Target(r["first_reply_min"], r["resolve_min"])
        for r in target_rows
    }
    policy_rows = await repo.policies(s)
    calendar_row = await repo.calendar(s)
    holiday_rows = await repo.holidays(s)
    days = frozenset(r["day"] for r in holiday_rows)
    if calendar_row is None:
        calendar = clocks.Calendar(defaults.DEFAULT_TIMEZONE, holidays=days)
    else:
        calendar = clocks.Calendar(
            timezone=str(calendar_row["timezone"]),
            days=frozenset(calendar_row["days"]),
            start=calendar_row["opens"],
            end=calendar_row["closes"],
            holidays=days,
            always_open=bool(calendar_row["always_open"]),
        )
    return Settings(
        config=Config(
            policies=tuple(_policy(r) for r in policy_rows) or clocks.DEFAULT_POLICIES,
            targets=defaults.with_defaults(saved),
            calendar=calendar,
            holiday_names={r["day"]: str(r["name"]) for r in holiday_rows},
        ),
        saved_targets=frozenset(saved),
        custom_policies=bool(policy_rows),
        custom_calendar=calendar_row is not None,
    )


async def config(s: AsyncSession) -> Config:
    return (await _settings(s)).config


def facts(row: Mapping[Any, Any]) -> clocks.SlaTicket:
    """From a ticket row (the detail/list rows carry `contact_vip`; fact rows `vip`)."""
    vip = row["contact_vip"] if "contact_vip" in row else row["vip"]
    return clocks.SlaTicket(
        priority=str(row["priority"]),
        channel=str(row["channel"]),
        created_at=row["created_at"],
        vip=bool(vip),
        first_reply_at=row["first_reply_at"],
        resolved_at=row["resolved_at"],
    )


def state(row: Mapping[Any, Any], cfg: Config, now: datetime | None = None) -> clocks.TicketSla:
    return clocks.sla_state(
        facts(row),
        policies=cfg.policies,
        targets=cfg.targets,
        calendar=cfg.calendar,
        now=now or datetime.now(UTC),
    )


def first_reply_met(row: Mapping[Any, Any], cfg: Config, now: datetime) -> bool | None:
    return clocks.first_reply_met(
        facts(row), policies=cfg.policies, targets=cfg.targets, calendar=cfg.calendar, now=now
    )


def due(row: Mapping[Any, Any], cfg: Config) -> datetime | None:
    """The current clock's deadline, or None once resolved."""
    if row["resolved_at"] is not None:
        return None
    return state(row, cfg).current.due_at


async def _store(s: AsyncSession, rows: Iterable[Mapping[Any, Any]], cfg: Config) -> None:
    rows = list(rows)
    await repo.set_due(s, [r["id"] for r in rows], [due(r, cfg) for r in rows])


async def refresh(
    s: AsyncSession,
    *,
    ticket_ids: Sequence[UUID] = (),
    contact_id: UUID | None = None,
    all_open: bool = False,
) -> None:
    """Recomputes `sla_due_at` inside the caller's transaction."""
    cfg = await config(s)
    if ticket_ids:
        await _store(s, await repo.facts_for_tickets(s, ticket_ids), cfg)
    if contact_id is not None:
        await _store(s, await repo.facts_for_contact(s, contact_id), cfg)
    if all_open:
        await _store(s, await repo.facts_for_open(s), cfg)


async def refresh_everywhere(sm: Sessionmaker) -> int:
    """Every workspace's open tickets (`cedesk sla-refresh`): for tickets made before
    migration 0019, which have no stored due time yet. Safe to run again. Returns how
    many workspaces were done."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            await repo.lock_settings(s, workspace_id)
            await refresh(s, all_open=True)
    return len(workspace_ids)


# Settings pages


async def read_settings(sm: Sessionmaker, caller: Caller) -> Settings:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _settings(s)


async def set_targets(
    sm: Sessionmaker, caller: Caller, targets: Mapping[str, tuple[int, int]]
) -> Settings:
    """Saves the given priorities' (first reply, resolve) minutes; others keep theirs.
    Every open ticket's due time is recalculated."""
    require(caller, Action.settings)
    unknown = set(targets) - set(defaults.PRIORITIES)
    if unknown:
        raise Invalid(f"Unknown priority: {', '.join(sorted(unknown))}.")
    for priority, (first_reply, resolve) in targets.items():
        problem = defaults.target_problem(first_reply, resolve)
        if problem:
            raise Invalid(f"{priority.capitalize()}: {problem}")
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.lock_settings(s, caller.workspace_id)
        for priority, (first_reply, resolve) in targets.items():
            await repo.set_target(s, caller.workspace_id, priority, first_reply, resolve)
        await refresh(s, all_open=True)
        return await _settings(s)
