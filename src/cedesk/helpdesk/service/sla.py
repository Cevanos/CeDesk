"""SLA on tickets (md W9, W13, T9), built on the clock logic in `domain/sla.py`.

- `config` reads the workspace's settings; anything not saved yet is the mock's default.
- `state` works out a ticket's pill when it is read.
- `refresh` stores each ticket's current due time (`sla_due_at`) for the `sla` list sort.
  Every change to an input (priority, status, first reply, VIP, targets) calls it, in the
  same transaction.
- Admins change the priority targets (W9), the ordered policies (W10), business hours
  (W11) and holidays (W12). Each change works out every open ticket's due time again,
  under the workspace's settings lock.
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import sla as clocks
from cedesk.helpdesk.domain import sla_settings as defaults
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import sla as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require


@dataclass(frozen=True)
class Config:
    policies: tuple[clocks.Policy, ...]
    targets: Mapping[str, clocks.Target]
    calendar: clocks.Calendar
    holiday_names: Mapping[Any, str]


@dataclass(frozen=True)
class Holiday:
    id: UUID
    day: date
    name: str


@dataclass(frozen=True)
class Settings:
    config: Config
    saved_targets: frozenset[str]  # priorities an Admin has set (the rest are defaults)
    custom_policies: bool
    custom_calendar: bool
    holidays: tuple[Holiday, ...] = ()


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
        holidays=tuple(Holiday(r["id"], r["day"], str(r["name"])) for r in holiday_rows),
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
    return clocks.current_due(
        facts(row), policies=cfg.policies, targets=cfg.targets, calendar=cfg.calendar
    )


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
        before = await _settings(s)
        for priority, (first_reply, resolve) in targets.items():
            await repo.set_target(s, caller.workspace_id, priority, first_reply, resolve)
        await refresh(s, all_open=True)
        after = await _settings(s)
        await _audit(s, caller, before, after)
        return after


async def _changed(
    sm: Sessionmaker, caller: Caller, change: Callable[[AsyncSession], Awaitable[None]]
) -> Settings:
    """Runs `change(s)` under the settings lock, then works out every open ticket again."""
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.lock_settings(s, caller.workspace_id)
        before = await _settings(s)
        await change(s)
        await refresh(s, all_open=True)
        after = await _settings(s)
        await _audit(s, caller, before, after)
        return after


def _audit_view(settings: Settings) -> dict[str, Any]:
    return {
        "targets": dict(settings.config.targets),
        "policies": settings.config.policies,
        "calendar": settings.config.calendar,
        "holidays": [{"day": h.day, "name": h.name} for h in settings.holidays],
    }


async def _audit(s: AsyncSession, caller: Caller, before: Settings, after: Settings) -> None:
    changed = audit_rules.changes(_audit_view(before), _audit_view(after))
    if changed:
        await audit.record(s, caller, Area.sla, "changed", "SLA", detail={"changes": changed})


def _checked(check: Any, *args: Any) -> Any:
    try:
        return check(*args)
    except defaults.SettingsError as exc:
        raise Invalid(str(exc)) from exc


async def set_policies(
    sm: Sessionmaker, caller: Caller, policies: Sequence[clocks.Policy] | None
) -> Settings:
    """The whole ordered list (W10); None goes back to the mock's three."""
    require(caller, Action.settings)
    if policies is not None:
        _checked(defaults.check_policies, policies)
    rows = [
        {
            "name": p.name,
            "enabled": p.enabled,
            "vip_only": p.vip_only,
            "channels": sorted(p.channels),
            "priorities": sorted(p.priorities),
            "first_reply": p.first_reply_minutes,
            "resolve": p.resolve_minutes,
        }
        for p in policies or ()
    ]

    async def change(s: AsyncSession) -> None:
        await repo.replace_policies(s, caller.workspace_id, rows)

    return await _changed(sm, caller, change)


async def set_calendar(
    sm: Sessionmaker,
    caller: Caller,
    *,
    timezone: str,
    days: Sequence[int],
    opens: time,
    closes: time,
    always_open: bool,
) -> Settings:
    """Business hours (W11): the days, the hours and the time zone, or 24/7."""
    require(caller, Action.settings)
    cal = _checked(defaults.check_calendar, timezone, days, opens, closes, always_open)
    values = {"timezone": cal.timezone, "days": sorted(cal.days), "opens": cal.start,
              "closes": cal.end, "always_open": cal.always_open}  # fmt: skip

    async def change(s: AsyncSession) -> None:
        await repo.set_calendar(s, caller.workspace_id, values)

    return await _changed(sm, caller, change)


HOLIDAY_TAKEN = "There is already a holiday on that day."


async def add_holiday(sm: Sessionmaker, caller: Caller, day: date, name: str) -> Settings:
    require(caller, Action.settings)
    _checked(defaults.check_holiday, day, name)

    async def change(s: AsyncSession) -> None:
        await repo.add_holiday(s, caller.workspace_id, day, name)

    try:
        return await _changed(sm, caller, change)
    except IntegrityError as exc:
        raise Conflict(HOLIDAY_TAKEN) from exc


async def change_holiday(
    sm: Sessionmaker, caller: Caller, holiday_id: UUID, *, day: date | None, name: str | None
) -> Settings:
    require(caller, Action.settings)
    _checked(defaults.check_holiday, day or date(2026, 1, 1), name if name is not None else "x")

    async def change(s: AsyncSession) -> None:
        if not await repo.change_holiday(s, holiday_id, day, name):
            raise NotFound("Holiday not found.")

    try:
        return await _changed(sm, caller, change)
    except IntegrityError as exc:
        raise Conflict(HOLIDAY_TAKEN) from exc


async def remove_holiday(sm: Sessionmaker, caller: Caller, holiday_id: UUID) -> Settings:
    require(caller, Action.settings)

    async def change(s: AsyncSession) -> None:
        if not await repo.remove_holiday(s, holiday_id):
            raise NotFound("Holiday not found.")

    return await _changed(sm, caller, change)
