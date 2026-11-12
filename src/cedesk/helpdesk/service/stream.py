"""Live updates (md C11, P7): stream tickets, opening a stream, and the stream itself.

How a stream works:

1. A signed-in client asks for a **ticket** (`issue_agent_ticket`, with its normal token). A
   browser's EventSource can't send an Authorization header, so it opens the stream with the
   ticket in the address instead. The ticket is random, lasts a minute and works once.
2. `open_agent` uses the ticket and checks the sign-in behind it is still live.
3. `run` is the stream: it looks for new events about once a second and sends them, sends a
   comment line every 15 seconds so nothing closes the idle connection, and checks every
   minute that the person is still signed in (and still allowed what they were). A database
   session is open only for each short look, never while waiting, so an open stream holds no
   connection from the pool.

Events are read, not pushed: the worker (a separate process) and the API share only the
database, so the log is the channel. `Last-Event-ID` carries on without losing one; one that
is older than what is kept gets a `reset` (refetch what you show). Events written in the last
30 seconds are read again, because a transaction can commit after a later one did, and an id
already sent is never sent twice on one connection. After a reconnect an id may repeat: a
client ignores one it has seen.
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import RowMapping

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as auth_rules
from cedesk.helpdesk.domain import stream as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Scope
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import stream as repo
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.errors import NotSignedIn, TooManyAttempts
from cedesk.helpdesk.service.portal import Customer

BAD_TICKET = "That stream ticket didn't work. Ask for a new one."
Row = RowMapping


@dataclass(frozen=True)
class IssuedTicket:
    ticket: str
    expires_in: int


@dataclass(frozen=True)
class Audience:
    """Who a stream is for. Built from a used ticket; refreshed while the stream is open."""

    kind: Literal["agent", "customer"]
    workspace_id: UUID
    session_id: UUID
    member_id: UUID | None = None
    contact_id: UUID | None = None
    team_only: UUID | None = None  # a role limited to one team's tickets: that team


# Tickets


async def _issue(
    sm: Sessionmaker,
    workspace_id: UUID,
    *,
    audience: str,
    session_id: UUID,
    member_id: UUID | None,
    contact_id: UUID | None,
) -> IssuedTicket:
    ticket = auth_rules.new_token(workspace_id)
    async with workspace_session(sm, workspace_id) as s:
        await repo.issue_ticket(
            s,
            workspace_id,
            token_hash=auth_rules.token_hash(ticket),
            audience=audience,
            session_id=session_id,
            member_id=member_id,
            contact_id=contact_id,
            expires_at=datetime.now(UTC) + rules.TICKET_LIFETIME,
        )
    return IssuedTicket(ticket, int(rules.TICKET_LIFETIME.total_seconds()))


async def issue_agent_ticket(sm: Sessionmaker, caller: Caller) -> IssuedTicket:
    return await _issue(
        sm,
        caller.workspace_id,
        audience="agent",
        session_id=caller.session_id,
        member_id=caller.member_id,
        contact_id=None,
    )


async def issue_customer_ticket(sm: Sessionmaker, customer: Customer) -> IssuedTicket:
    return await _issue(
        sm,
        customer.workspace_id,
        audience="customer",
        session_id=customer.session_id,
        member_id=None,
        contact_id=customer.contact_id,
    )


def _agent(workspace_id: UUID, session_id: UUID, row: Row) -> Audience:
    access = member_access(row)
    return Audience(
        "agent",
        workspace_id,
        session_id,
        member_id=row["member_id"],
        team_only=access.team_id if access.scope is Scope.team_only else None,
    )


async def open_agent(sm: Sessionmaker, ticket: str) -> Audience:
    """Uses the ticket. 401 when it is unknown, used, expired, or its sign-in has ended."""
    workspace_id = auth_rules.token_workspace(ticket)
    if workspace_id is None:
        raise NotSignedIn(BAD_TICKET)
    async with workspace_session(sm, workspace_id) as s:
        used = await repo.consume_ticket(s, auth_rules.token_hash(ticket), "agent")
        if used is None:
            raise NotSignedIn(BAD_TICKET)
        row = await repo.live_agent(s, used["session_id"])
    if row is None:
        raise NotSignedIn(BAD_TICKET)
    return _agent(workspace_id, used["session_id"], row)


async def open_customer(sm: Sessionmaker, ticket: str) -> Audience:
    workspace_id = auth_rules.token_workspace(ticket)
    if workspace_id is None:
        raise NotSignedIn(BAD_TICKET)
    async with workspace_session(sm, workspace_id) as s:
        used = await repo.consume_ticket(s, auth_rules.token_hash(ticket), "customer")
        if used is None:
            raise NotSignedIn(BAD_TICKET)
        row = await repo.live_customer(s, used["session_id"])
    if row is None:
        raise NotSignedIn(BAD_TICKET)
    return Audience("customer", workspace_id, used["session_id"], contact_id=used["contact_id"])


# At most a few open streams per person (a browser tab storm must not use up the pool)


class Slots:
    """Open streams per signed-in person. It lives in the API process, as the other limits do
    (`ratelimit.py`); with several API processes each counts its own."""

    def __init__(self, limit: int = rules.MAX_STREAMS_PER_MEMBER) -> None:
        self._limit = limit
        self._open: dict[tuple[UUID, UUID], int] = {}

    @staticmethod
    def _who(who: Audience) -> tuple[UUID, UUID]:
        person = who.member_id or who.contact_id
        assert person is not None
        return who.workspace_id, person

    def check(self, who: Audience) -> None:
        """429 before the response starts, when the person already has the limit open."""
        if self._open.get(self._who(who), 0) >= self._limit:
            raise TooManyAttempts(
                f"You already have {self._limit} live streams open. Close one first.", 5
            )

    @contextmanager
    def hold(self, who: Audience) -> Iterator[None]:
        key = self._who(who)
        self._open[key] = self._open.get(key, 0) + 1
        try:
            yield
        finally:
            left = self._open.get(key, 1) - 1
            if left <= 0:
                self._open.pop(key, None)
            else:
                self._open[key] = left


# The stream


async def _read(s: Any, who: Audience, *, after: int, floor: int, overlap: bool) -> Sequence[Row]:
    if who.kind == "agent":
        assert who.member_id is not None
        return await repo.read_agent(
            s,
            who.workspace_id,
            who.member_id,
            who.team_only,
            after=after,
            floor=floor,
            limit=rules.BATCH,
            overlap=overlap,
        )
    assert who.contact_id is not None
    return await repo.read_customer(
        s,
        who.workspace_id,
        who.contact_id,
        after=after,
        floor=floor,
        limit=rules.BATCH,
        overlap=overlap,
    )


async def _still_signed_in(sm: Sessionmaker, who: Audience) -> Audience | None:
    """The audience as it is now (a changed role changes what is sent), or None when the
    sign-in behind the stream has ended or the person was deactivated."""
    async with workspace_session(sm, who.workspace_id) as s:
        if who.kind == "agent":
            row = await repo.live_agent(s, who.session_id)
            return None if row is None else _agent(who.workspace_id, who.session_id, row)
        found = await repo.live_customer(s, who.session_id)
    return None if found is None else who


def _frame(row: Row) -> str:
    data: dict[str, Any] = dict(row["payload"])
    data.setdefault("ticket_id", str(row["ticket_id"]) if row["ticket_id"] else None)
    return rules.frame(int(row["seq"]), str(row["kind"]), data)


async def run(
    sm: Sessionmaker,
    who: Audience,
    *,
    last_event_id: int | None = None,
    poll: float = rules.POLL_SECONDS,
    heartbeat: float = rules.HEARTBEAT_SECONDS,
    recheck: float = rules.RECHECK_SECONDS,
    max_polls: int | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    """The text of an event stream, until the client goes away (the generator is closed), the
    sign-in ends, or (tests) `max_polls` looks have been made."""
    yield rules.hello()
    workspace_id = who.workspace_id
    async with workspace_session(sm, workspace_id) as s:
        start = await repo.newest(s, workspace_id)
        if last_event_id is None:
            after, gap = start, False
        elif await repo.exists(s, workspace_id, last_event_id):
            after, gap = last_event_id, False
        else:
            after, gap = start, True  # older than what is kept
    if gap:
        yield rules.reset()
    floor = after
    seen: deque[int] = deque(maxlen=rules.SEEN)
    seen_set: set[int] = set()
    quiet_since = checked_at = clock()
    polls = 0
    while True:
        async with workspace_session(sm, workspace_id) as s:
            fresh = await _read(s, who, after=after, floor=floor, overlap=False)
            late = await _read(s, who, after=after, floor=floor, overlap=True)
        if fresh:
            after = max(after, int(fresh[-1]["seq"]))
        sent = False
        for row in sorted([*late, *fresh], key=lambda r: int(r["seq"])):
            seq = int(row["seq"])
            if seq in seen_set:
                continue
            if len(seen) == seen.maxlen:
                seen_set.discard(seen[0])
            seen.append(seq)
            seen_set.add(seq)
            sent = True
            yield _frame(row)
        now = clock()
        if sent:
            quiet_since = now
        elif now - quiet_since >= heartbeat:
            yield rules.heartbeat()
            quiet_since = now
        if now - checked_at >= recheck:
            current = await _still_signed_in(sm, who)
            if current is None:
                yield rules.end("signed_out")
                return
            who, checked_at = replace(current), now
        polls += 1
        if max_polls is not None and polls >= max_polls:
            return
        await sleep(poll)


# Keeping the log small (the worker, with the other sweeps)


async def prune_everywhere(sm: Sessionmaker) -> int:
    """Removes events older than 24 hours, and old stream tickets, in every workspace."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    removed = 0
    cutoff = datetime.now(UTC) - rules.RETENTION
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            removed += await repo.prune(s, cutoff)
    return removed
