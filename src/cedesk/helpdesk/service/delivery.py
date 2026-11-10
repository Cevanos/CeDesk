"""Delivering outbox events (md 3.2 step 7): after a change commits, the worker hands each
of its events to the handlers that want that kind of event.

- One ticket's events go in order; different tickets and workspaces don't wait on each other.
- Each (event, handler) runs until it succeeds once, then is recorded as done, so a retry
  only repeats the handlers that failed. A handler gets `effect_key` to send on to the
  outside service, so an answer lost on the way back is not a second effect there.
- A failed try waits 1 min, 5 min, 30 min, 2 h, then 12 h; after the 6th the event is
  failed and listed for Admins, who can send it again.
- An event no handler wants is simply marked delivered.

Handlers: the Cevanos connector (take-over, replies) comes next; webhooks in Phase 3.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import delivery as rules
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import outbox as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk.delivery")

BATCH = 50
LEASE_SECONDS = 300
ROUNDS = 20
"""Rounds per workspace per pass: a claim takes only the earliest waiting event of each
ticket, so a ticket's next event is taken in the next round."""


@dataclass(frozen=True)
class Event:
    id: UUID
    workspace_id: UUID
    event: str
    ticket_id: UUID | None
    payload: dict[str, Any]
    occurred_at: datetime


class HandlerError(Exception):
    """`permanent`: trying again won't help, so the event fails at once."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Handler(Protocol):
    name: str  # lower-case, stable: it is part of each effect's key
    events: frozenset[str]  # the event kinds it wants

    async def handle(self, event: Event, effect_key: str) -> None: ...


@dataclass
class DeliveryResult:
    delivered: int = 0
    retrying: int = 0
    failed: int = 0


@dataclass(frozen=True)
class FailedPage:
    events: Sequence[Any]  # newest failure first
    next_cursor: str | None


def check_handlers(handlers: Sequence[Handler]) -> None:
    names = [h.name for h in handlers]
    if len(set(names)) != len(names):
        raise ValueError("Two outbox handlers share a name.")
    for name in names:
        if not rules.HANDLER_NAME.match(name):
            raise ValueError(f"Bad outbox handler name: {name!r}")


async def _deliver_workspace(
    sm: Sessionmaker,
    handlers: Sequence[Handler],
    workspace_id: UUID,
    result: DeliveryResult,
    batch: int,
) -> int:
    """One round; returns how many events it took."""
    async with workspace_session(sm, workspace_id) as s:
        if not await repo.has_due(s):
            return 0
        rows = await repo.claim_due(s, batch, LEASE_SECONDS)
        done = await repo.done_handlers(s, [r["id"] for r in rows]) if rows else set()
    for row in rows:
        event = Event(
            id=row["id"],
            workspace_id=workspace_id,
            event=str(row["event"]),
            ticket_id=row["ticket_id"],
            payload=dict(row["payload"]),
            occurred_at=row["occurred_at"],
        )
        error: HandlerError | None = None
        # Sent outside any transaction: a slow service never holds a database lock.
        for handler in handlers:
            if event.event not in handler.events or (event.id, handler.name) in done:
                continue
            try:
                await handler.handle(event, rules.effect_key(event.id, handler.name))
            except HandlerError as exc:
                message = f"{handler.name}: {exc}"[: rules.ERROR_MAX]
                error = HandlerError(message, permanent=exc.permanent)
                break
            except Exception as exc:  # a bug in a handler must not stop the worker
                logger.exception("outbox handler crashed", extra={"workspace_id": workspace_id})
                error = HandlerError(f"{handler.name}: {type(exc).__name__}"[: rules.ERROR_MAX])
                break
            async with workspace_session(sm, workspace_id) as s:
                await repo.mark_done(s, workspace_id, event.id, handler.name)
        async with workspace_session(sm, workspace_id) as s:
            if error is None:
                await repo.publish(s, event.id)
                result.delivered += 1
                continue
            wait = None if error.permanent else rules.retry_after(int(row["attempts"]))
            logger.warning(
                "outbox event not delivered",
                extra={"workspace_id": workspace_id, "error": str(error), "event": event.event},
            )
            if wait is None:
                await repo.fail(s, event.id, str(error))
                result.failed += 1
            else:
                at = datetime.now(UTC) + timedelta(seconds=wait)
                await repo.retry_later(s, event.id, str(error), at)
                result.retrying += 1
    return len(rows)


async def deliver_due(
    sm: Sessionmaker, handlers: Sequence[Handler], *, batch: int = BATCH
) -> DeliveryResult:
    """One pass over every workspace, each under its own RLS."""
    check_handlers(handlers)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    result = DeliveryResult()
    for workspace_id in workspace_ids:
        try:
            for _ in range(ROUNDS):
                if not await _deliver_workspace(sm, handlers, workspace_id, result, batch):
                    break
        except Exception:
            # One workspace's trouble must not stop the others; its claimed events come
            # back when their lease runs out.
            logger.exception("outbox pass failed", extra={"workspace_id": workspace_id})
    return result


# The failed list (Admins)


async def failed_events(
    sm: Sessionmaker, caller: Caller, *, cursor: str | None = None, limit: int = 50
) -> FailedPage:
    require(caller, Action.settings)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "outbox")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.failed(s, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "outbox")
    return FailedPage(events=page, next_cursor=next_cursor)


async def retry_event(sm: Sessionmaker, caller: Caller, event_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.retry_failed(s, event_id):
            raise NotFound("No failed event with that id.")
