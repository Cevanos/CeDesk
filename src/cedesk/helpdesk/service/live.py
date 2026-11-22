"""The live view (feature-gap plan 3.4); rules in domain/live.py.

`Action.reports`; a `team_only` role gets its own team's queue (asking for another team is
a team it can't see, 404). The page refreshes on the live stream's ticket events and every
30 seconds; many people watching the same scope share one answer for a few seconds
(`LiveCache`), so the queries run once per scope, not once per page.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import live as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import live as repo
from cedesk.helpdesk.repo import report_engine as engine_repo
from cedesk.helpdesk.repo import reports as reports_repo
from cedesk.helpdesk.service import reports
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_TEAM = "No team"
TIMEOUT_MESSAGE = "The live view took too long to work out. Try again in a moment."


@dataclass(frozen=True)
class TeamCounts:
    team_id: UUID | None
    name: str
    open: int
    pending: int
    snoozed: int
    breached: int
    at_risk: int


@dataclass(frozen=True)
class WaitingTicket:
    id: UUID
    label: str
    subject: str
    priority: str
    team_id: UUID | None
    assignee_id: UUID | None
    created_at: datetime
    waiting_minutes: int


@dataclass(frozen=True)
class DueTicket:
    id: UUID
    label: str
    subject: str
    priority: str
    team_id: UUID | None
    assignee_id: UUID | None
    sla_due_at: datetime
    minutes_left: int


@dataclass(frozen=True)
class AgentCounts:
    available: int
    on_break: int
    transfers_only: int
    offline: int


@dataclass(frozen=True)
class Live:
    generated_at: datetime
    team_id: UUID | None
    window_minutes: int
    total: TeamCounts
    teams: list[TeamCounts]
    waiting: list[WaitingTicket]
    due_soon: list[DueTicket]
    agents: AgentCounts


class LiveCache:
    """A short-lived answer per scope, built once even when many ask at the same moment."""

    def __init__(self, ttl: timedelta = rules.CACHE_TTL) -> None:
        self._ttl = ttl.total_seconds()
        self._items: dict[Any, tuple[float, Live]] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    def _fresh(self, key: Any) -> Live | None:
        entry = self._items.get(key)
        return entry[1] if entry is not None and entry[0] > time.monotonic() else None

    async def get(self, key: Any, make: Callable[[], Awaitable[Live]]) -> Live:
        found = self._fresh(key)
        if found is not None:
            return found
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            found = self._fresh(key)
            if found is not None:
                return found
            made = await make()
            now = time.monotonic()
            self._items[key] = (now + self._ttl, made)
            if len(self._items) > 200:  # forget what has expired
                for old in [k for k, (until, _) in self._items.items() if until <= now]:
                    self._items.pop(old, None)
                    self._locks.pop(old, None)
            return made


def _counts(row: Any, name: str) -> TeamCounts:
    return TeamCounts(
        row["team_id"], name, int(row["open"]), int(row["pending"]), int(row["snoozed"]),
        int(row["breached"]), int(row["at_risk"]),
    )  # fmt: skip


async def _compute(
    sm: Sessionmaker,
    workspace_id: UUID,
    team_id: UUID | None,
    window_minutes: int,
    timeout_ms: int,
) -> Live:
    now = datetime.now(UTC)
    window = timedelta(minutes=window_minutes)
    try:
        async with workspace_session(sm, workspace_id) as s:
            await engine_repo.set_timeout(s, timeout_ms)
            if team_id is not None and not await reports_repo.team_exists(s, team_id):
                raise NotFound("Team not found.")
            count_rows = await repo.counts(s, team_id, now, window)
            waiting_rows = await repo.waiting(s, team_id, rules.WAITING_LIMIT)
            due_rows = await repo.due_soon(s, team_id, now, window, rules.DUE_LIMIT)
            team_rows = await reports_repo.teams(s)
            agent_rows = await reports_repo.agents(s, team_id)
    except DBAPIError as exc:
        if "statement timeout" in str(exc.orig):
            raise Invalid(TIMEOUT_MESSAGE) from exc
        raise

    names = {r["id"]: str(r["name"]) for r in team_rows}
    by_team = {r["team_id"]: r for r in count_rows if not r["by_all_teams"]}
    empty = {"team_id": None, "open": 0, "pending": 0, "snoozed": 0, "breached": 0, "at_risk": 0}
    total_row = next((r for r in count_rows if r["by_all_teams"]), empty)
    shown = [(tid, n) for tid, n in names.items() if team_id is None or tid == team_id]
    if team_id is None and None in by_team:
        shown.append((None, NO_TEAM))
    teams = [_counts(by_team.get(tid, empty | {"team_id": tid}), name) for tid, name in shown]
    statuses = [str(a["status"]) for a in agent_rows]
    return Live(
        generated_at=now,
        team_id=team_id,
        window_minutes=window_minutes,
        total=_counts(total_row, "All teams"),
        teams=teams,
        waiting=[
            WaitingTicket(
                r["id"], label(int(r["number"])), str(r["subject"]), str(r["priority"]),
                r["team_id"], r["assignee_id"], r["created_at"],
                max(0, int((now - r["created_at"]).total_seconds() // 60)),
            )
            for r in waiting_rows
        ],
        due_soon=[
            DueTicket(
                r["id"], label(int(r["number"])), str(r["subject"]), str(r["priority"]),
                r["team_id"], r["assignee_id"], r["sla_due_at"],
                max(0, int((r["sla_due_at"] - now).total_seconds() // 60)),
            )
            for r in due_rows
        ],
        agents=AgentCounts(**{k: statuses.count(k) for k in rules.STATUSES}),
    )  # fmt: skip


async def live_view(
    sm: Sessionmaker,
    caller: Caller,
    *,
    team_id: UUID | None,
    window_minutes: int,
    cache: LiveCache,
    timeout_ms: int,
) -> Live:
    require(caller, Action.reports)
    team_id = reports.team_filter(caller, team_id)
    try:
        window_minutes = rules.check_window(window_minutes)
    except rules.LiveError as exc:
        raise Invalid(str(exc)) from exc
    key = (caller.workspace_id, team_id, window_minutes)
    return await cache.get(
        key, lambda: _compute(sm, caller.workspace_id, team_id, window_minutes, timeout_ms)
    )
