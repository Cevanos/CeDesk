"""The report engine (feature-gap plan 3.1, 3.2): runs a checked definition
(`domain/report_defs.py`) and gives the groups back, zero-filled for a time series.

- Needs `Action.reports`. A `team_only` role is held to its own team on every run; asking
  for another team is a team it can't see (404), as in the other reports.
- `run_definition` takes the workspace and the team to hold the run to, not a `Caller`, so
  the worker (scheduled reports) and the API run the same code.
- Cost control: every run has a statement timeout (a run that takes longer answers 422 "narrow
  the range"), the API runs at most `ReportSlots.size` at once per process (more wait a few
  seconds, then answer 429), and a grouped answer keeps the biggest `MAX_GROUPS` groups.
- `incomplete` says the range reaches back past the workspace's archive age: resolved
  tickets older than that have left the live tables, so older ranges can under-count.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import archive as archive_rules
from cedesk.helpdesk.domain import backlog as backlog_rules
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.reports import BadPeriod
from cedesk.helpdesk.repo import archive as archive_repo
from cedesk.helpdesk.repo import backlog as backlog_repo
from cedesk.helpdesk.repo import report_engine as repo
from cedesk.helpdesk.repo import reports as reports_repo
from cedesk.helpdesk.service import scope, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound, TooManyAttempts
from cedesk.helpdesk.service.members import require

TIMEOUT_MESSAGE = "That report took too long. Narrow the date range or the grouping."
BUSY_MESSAGE = "Too many reports are running. Try again in a moment."
TAG_NOTE = "A ticket counts once in each of its tags, so the groups add up to more than the total."
NO_LABEL = {
    "team": "No team", "assignee": "Unassigned", "type": "No type", "tag": "No tags",
}  # fmt: skip


class ReportSlots:
    """How many reports one API process runs at once; the rest wait a few seconds."""

    def __init__(self, size: int, wait_seconds: float = 5.0) -> None:
        self.size = size
        self._wait = wait_seconds
        self._semaphore = asyncio.Semaphore(size)

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self._wait)
        except TimeoutError as exc:
            raise TooManyAttempts(BUSY_MESSAGE, retry_after=5) from exc
        try:
            yield
        finally:
            self._semaphore.release()


@dataclass(frozen=True)
class Group:
    key: str | None  # a day (YYYY-MM-DD), an id, or a name; None: the group of "not set"
    label: str
    value: float | int | None  # None: nothing to measure (no tickets in that group)
    n: int  # tickets, ratings or log entries the value rests on


@dataclass(frozen=True)
class Result:
    metric: str
    label: str
    unit: str
    group_by: str
    start: datetime | None  # None: an as-of-now metric
    end: datetime | None
    timezone: str
    total: float | int | None
    total_n: int
    groups: list[Group]
    truncated: bool = False
    incomplete: bool = False
    complete_from: date | None = None
    first_day: date | None = None  # backlog: where the snapshots start
    notes: list[str] = field(default_factory=list)


def _number(value: Any, unit: str) -> float | int | None:
    if value is None:
        return None
    if unit == "count":
        return int(value)
    return round(float(value), 2 if unit == "score" else 1)


def _is_timeout(exc: DBAPIError) -> bool:
    return "statement timeout" in str(exc.orig) or type(exc.orig).__name__ == "QueryCanceledError"


def _label(group_by: str, key: str | None, found: Mapping[str, str]) -> str:
    if key is None:
        return NO_LABEL.get(group_by, "Not set")
    if group_by in ("team", "assignee", "type"):
        return found.get(key, "Removed")
    return key


async def run_definition(
    sm: Sessionmaker,
    workspace_id: UUID,
    scope_team: UUID | None,
    definition: defs.Definition,
    *,
    now: datetime | None = None,
    timeout_ms: int = 10_000,
    slots: ReportSlots | None = None,
) -> Result:
    """Runs a definition, holding it to `scope_team` when that is set."""
    now = now or datetime.now(UTC)
    if slots is None:
        return await _run(sm, workspace_id, scope_team, definition, now, timeout_ms)
    async with slots.acquire():
        return await _run(sm, workspace_id, scope_team, definition, now, timeout_ms)


async def _run(
    sm: Sessionmaker,
    workspace_id: UUID,
    scope_team: UUID | None,
    definition: defs.Definition,
    now: datetime,
    timeout_ms: int,
) -> Result:
    spec = definition.spec
    team = definition.filters.get("team")
    if scope_team is not None and team not in (None, str(scope_team)):
        raise NotFound("Team not found.")
    try:
        async with workspace_session(sm, workspace_id) as s:
            await repo.set_timeout(s, timeout_ms)
            if team is not None and not await reports_repo.team_exists(s, UUID(team)):
                raise NotFound("Team not found.")
            timezone = (await sla.config(s)).calendar.timezone
            if spec.kind == defs.BACKLOG:
                return await _backlog(s, definition, scope_team, now, timezone)
            start = end = None
            if spec.kind == defs.PERIOD:
                start, end = _resolve(definition, now, timezone)
            return await _period_or_now(s, definition, scope_team, start, end, now, timezone)
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise Invalid(TIMEOUT_MESSAGE) from exc
        raise


def _resolve(
    definition: defs.Definition, now: datetime, timezone: str
) -> tuple[datetime, datetime]:
    try:
        return defs.resolve_range(definition.range, now, timezone)
    except BadPeriod as exc:
        raise Invalid(str(exc)) from exc


async def _backlog(
    s: Any, definition: defs.Definition, scope_team: UUID | None, now: datetime, timezone: str
) -> Result:
    spec = definition.spec
    start, end = _resolve(definition, now, timezone)
    team = scope_team or (
        UUID(definition.filters["team"]) if "team" in definition.filters else None
    )
    first, last = backlog_rules.days_covering(start, end, timezone)
    rows = await backlog_repo.days(s, first, last, team)
    groups = [
        Group(r["day"].isoformat(), r["day"].isoformat(), int(r["open"]) + int(r["pending"]), 1)
        for r in rows
    ]
    return Result(
        metric=spec.key, label=spec.label, unit=spec.unit, group_by="day", start=start, end=end,
        timezone=timezone, total=None, total_n=len(groups), groups=groups,
        first_day=await backlog_repo.first_day(s),
        notes=["Taken once a day; a day the worker was down has no number."],
    )  # fmt: skip


async def _archive_cutoff(s: Any, now: datetime, timezone: str) -> date:
    row = await archive_repo.setting(s)
    days = archive_rules.archive_days(row["archive_after_days"] if row else None)
    return backlog_rules.local_date(now - timedelta(days=days), timezone)


async def _period_or_now(
    s: Any,
    definition: defs.Definition,
    scope_team: UUID | None,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
    timezone: str,
) -> Result:
    spec, group_by = definition.spec, definition.group_by
    args: dict[str, Any] = {
        "scope_team": scope_team,
        "start": start,
        "end": end,
        "timezone": timezone,
    }
    total_rows = await repo.run(s, spec.key, "none", definition.filters, **args)
    rows = (
        await repo.run(s, spec.key, group_by, definition.filters, **args)
        if group_by != "none"
        else []
    )
    total_value, total_n = (
        (total_rows[0]["value"], int(total_rows[0]["n"])) if total_rows else (None, 0)
    )

    groups: list[Group] = []
    truncated = False
    notes: list[str] = []
    if group_by in defs.TIME_GROUPS:
        assert start is not None and end is not None
        found = {r["gkey"]: r for r in rows}
        for day in defs.buckets(start, end, timezone, group_by):
            r = found.get(day.isoformat())
            value = _number(r["value"], spec.unit) if r else (0 if spec.unit == "count" else None)
            groups.append(Group(day.isoformat(), day.isoformat(), value, int(r["n"]) if r else 0))
    elif group_by != "none":
        if len(rows) > defs.MAX_GROUPS:
            rows, truncated = rows[: defs.MAX_GROUPS], True
        keys = [str(r["gkey"]) for r in rows if r["gkey"] is not None]
        found_names = await repo.names(s, group_by, keys)
        for r in rows:
            key = None if r["gkey"] is None else str(r["gkey"])
            groups.append(
                Group(
                    key,
                    _label(group_by, key, found_names),
                    _number(r["value"], spec.unit),
                    int(r["n"]),
                )
            )
        if group_by == "tag":
            notes.append(TAG_NOTE)

    incomplete, complete_from = False, None
    if spec.kind == defs.PERIOD and spec.key not in ("time_logged",) and start is not None:
        complete_from = await _archive_cutoff(s, now, timezone)
        incomplete = backlog_rules.local_date(start, timezone) < complete_from
        if incomplete:
            notes.append(
                "This range reaches back past the archive age: resolved tickets older than "
                f"{complete_from.isoformat()} are archived, so older numbers can be too low."
            )
    return Result(
        metric=spec.key, label=spec.label, unit=spec.unit, group_by=group_by, start=start, end=end,
        timezone=timezone, total=_number(total_value, spec.unit), total_n=total_n,
        groups=groups, truncated=truncated, incomplete=incomplete, complete_from=complete_from,
        notes=notes,
    )  # fmt: skip


def require_reports(caller: Caller) -> None:
    """Refuses a role without the reports right (for routes that only list options)."""
    require(caller, Action.reports)


def _parse(raw: Mapping[str, Any]) -> defs.Definition:
    try:
        return defs.parse(raw)
    except defs.DefinitionError as exc:
        raise Invalid(str(exc)) from exc


async def run(
    sm: Sessionmaker,
    caller: Caller,
    raw: Mapping[str, Any] | defs.Definition,
    *,
    timeout_ms: int,
    slots: ReportSlots | None,
) -> Result:
    """Runs a definition (a JSON object, or one already checked) for a person."""
    require(caller, Action.reports)
    definition = raw if isinstance(raw, defs.Definition) else _parse(raw)
    return await run_definition(
        sm, caller.workspace_id, scope.visible_team(caller), definition,
        timeout_ms=timeout_ms, slots=slots,
    )  # fmt: skip


async def run_preset(
    sm: Sessionmaker, caller: Caller, key: str, *, timeout_ms: int, slots: ReportSlots | None,
    range_: Mapping[str, Any] | None = None,
) -> Result:  # fmt: skip
    """A built-in report, optionally with another range (`{"preset": ...}` or from/to)."""
    require(caller, Action.reports)
    try:
        definition = defs.preset_definition(key)
    except defs.DefinitionError as exc:
        raise NotFound(str(exc)) from exc
    if range_ is not None:
        definition = _parse(definition.stored() | {"range": range_})
    return await run(sm, caller, definition, timeout_ms=timeout_ms, slots=slots)
