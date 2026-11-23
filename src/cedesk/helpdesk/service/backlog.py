"""Backlog over time (feature-gap plan 3.1); rules in domain/backlog.py.

- The worker writes one snapshot per workspace and day (`run_snapshots`, every pass of its
  10 minute sweep: a workspace whose yesterday is already written costs one lookup). A
  restart or two workers at once is harmless: the write is an upsert.
- `GET /reports/backlog` (`Action.reports`; a `team_only` role gets its own team's rows)
  reads them back. Days with no snapshot are not in the answer.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import backlog as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import backlog as repo
from cedesk.helpdesk.repo import reports as reports_repo
from cedesk.helpdesk.service import reports, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk.backlog")


@dataclass(frozen=True)
class BacklogDay:
    day: date
    open: int
    pending: int
    breaching: int
    taken_at: datetime  # when it was really taken (can be hours after the day ended)


@dataclass(frozen=True)
class BacklogReport:
    start: datetime
    end: datetime
    team_id: UUID | None
    timezone: str
    first_day: date | None  # where this workspace's history starts (None: none yet)
    days: list[BacklogDay]


async def capture_workspace(sm: Sessionmaker, workspace_id: UUID, now: datetime) -> bool:
    """Writes yesterday's snapshot unless it is there. True when it wrote one."""
    async with workspace_session(sm, workspace_id) as s:
        timezone = (await sla.config(s)).calendar.timezone
        day = rules.day_to_capture(now, timezone)
        if await repo.has_day(s, day):
            return False
        await repo.capture(s, workspace_id, day, now)
        return True


async def run_snapshots(sm: Sessionmaker, *, now: datetime | None = None) -> int:
    """Every workspace (the worker). Returns how many snapshots were written."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    written = 0
    for workspace_id in workspace_ids:
        try:
            written += await capture_workspace(sm, workspace_id, now)
        except Exception:  # one workspace's problem never stops the others
            logger.exception("backlog snapshot failed", extra={"workspace_id": workspace_id})
    return written


async def prune_everywhere(sm: Sessionmaker, *, now: datetime | None = None) -> int:
    """Deletes snapshots older than `rules.KEEP_DAYS` in every workspace."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    removed = 0
    for workspace_id in workspace_ids:
        try:
            async with workspace_session(sm, workspace_id) as s:
                timezone = (await sla.config(s)).calendar.timezone
                removed += await repo.prune(s, rules.keep_from(now, timezone))
        except Exception:
            logger.exception(
                "pruning backlog snapshots failed", extra={"workspace_id": workspace_id}
            )
    return removed


async def backlog_report(
    sm: Sessionmaker,
    caller: Caller,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    team_id: UUID | None = None,
) -> BacklogReport:
    require(caller, Action.reports)
    team_id = reports.team_filter(caller, team_id)
    now = datetime.now(UTC)
    start, end = reports.check_period(start, end, now)
    async with workspace_session(sm, caller.workspace_id) as s:
        if team_id is not None and not await reports_repo.team_exists(s, team_id):
            raise NotFound("Team not found.")
        timezone = (await sla.config(s)).calendar.timezone
        first, last = rules.days_covering(start, end, timezone)
        rows = await repo.days(s, first, last, team_id)
        history_starts = await repo.first_day(s)
    return BacklogReport(
        start,
        end,
        team_id,
        timezone,
        history_starts,
        [
            BacklogDay(
                r["day"], int(r["open"]), int(r["pending"]), int(r["breaching"]), r["taken_at"]
            )
            for r in rows
        ],
    )
