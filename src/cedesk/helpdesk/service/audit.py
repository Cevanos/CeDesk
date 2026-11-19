"""Audit log (md S9): every settings change, who made it, when and from which address.

`record` is called by each settings write inside its own transaction, so the change and
its entry are saved together. Reading the log, verifying the chain and the CSV export
need the `settings` right (md permission table).
"""

import csv
import io
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as rules
from cedesk.helpdesk.domain.audit import Area, Entry, Verified
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import audit as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Forbidden

SERVER = "Server (command line)"
SIGNUP = "Self-service sign-up"


def require(caller: Caller, action: Action) -> None:
    """As members.require; kept here so every service can import this module."""
    if not caller.can(action):
        raise Forbidden("Your role can't do this.")


async def record(
    s: AsyncSession,
    caller: Caller,
    area: Area,
    action: str,
    target: str,
    *,
    target_id: UUID | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """One entry for a change the caller is making in this transaction."""
    name = await repo.member_name(s, caller.member_id)
    await repo.append(
        s,
        caller.workspace_id,
        at=datetime.now(UTC),
        actor_id=caller.member_id,
        actor_name=caller.acting(name or "Someone"),
        ip=caller.ip,
        area=area.value,
        action=action,
        target=target,
        target_id=target_id,
        detail=detail or {},
    )


async def record_server(
    s: AsyncSession,
    workspace_id: UUID,
    area: Area,
    action: str,
    target: str,
    *,
    detail: Mapping[str, Any] | None = None,
    actor_name: str = SERVER,
    ip: str | None = None,
) -> None:
    """A change made with no signed-in member: from the server's command line, or (with
    `actor_name` SIGNUP and the client's `ip`) by self-service sign-up."""
    await repo.append(
        s,
        workspace_id,
        at=datetime.now(UTC),
        actor_id=None,
        actor_name=actor_name,
        ip=ip,
        area=area.value,
        action=action,
        target=target,
        target_id=None,
        detail=detail or {},
    )


def _entry(row: Mapping[Any, Any]) -> Entry:
    return Entry(
        seq=int(row["seq"]),
        at=row["at"],
        actor_id=row["actor_id"],
        actor_name=str(row["actor_name"]),
        ip=row["ip"],
        area=str(row["area"]),
        action=str(row["action"]),
        target=str(row["target"]),
        target_id=row["target_id"],
        detail=row["detail"] or {},
    )


@dataclass(frozen=True)
class Page:
    entries: list[RowMapping]
    next_before: int | None  # pass as `before` for the next page


def _area(area: str | None) -> str | None:
    return Area(area).value if area else None


async def page(
    sm: Sessionmaker, caller: Caller, *, area: str | None, before: int | None, limit: int
) -> Page:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = list(await repo.page(s, area=_area(area), before_seq=before, limit=limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(rows, int(rows[-1]["seq"]) if more and rows else None)


async def verify(sm: Sessionmaker, caller: Caller) -> Verified:
    """Re-hashes the whole chain, oldest first."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        found = [
            (_entry(row), bytes(row["prev_hash"]), bytes(row["hash"]))
            async for row in repo.in_order(s)
        ]
    return rules.verify(found)


CSV_HEADER = ("seq", "when (UTC)", "who", "ip", "area", "action", "what", "changes")


async def export_csv(sm: Sessionmaker, caller: Caller, *, area: str | None) -> AsyncIterator[str]:
    """The log as CSV, oldest first; yields chunks so a long log isn't held in memory."""
    require(caller, Action.settings)
    chosen = _area(area)

    async def lines() -> AsyncIterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        async with workspace_session(sm, caller.workspace_id) as s:
            async for row in repo.in_order(s, area=chosen):
                writer.writerow(
                    (
                        row["seq"],
                        row["at"].astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                        csv_cell(row["actor_name"]),
                        row["ip"] or "",
                        row["area"],
                        row["action"],
                        csv_cell(row["target"]),
                        csv_cell(json.dumps(row["detail"] or {}, ensure_ascii=False)),
                    )
                )
                if buffer.tell() > 64_000:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate()
        yield buffer.getvalue()

    return lines()


def csv_cell(value: object) -> str:
    """No formula injection when the CSV is opened in a spreadsheet."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
