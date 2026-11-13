"""Push notifications to the mobile app (md P8). Rules in domain/push.py; sending in
cedesk/push_client.py (Firebase Cloud Messaging).

- Devices: anyone signed in (not an API key) who reads tickets registers their phone's
  token, under their current sign-in; they list their own devices (never the token itself,
  only its end) and remove one. Nobody sees or removes another person's device, and a
  phone registered under someone else's live sign-in is refused (409). Ending a sign-in
  removes its devices (repo/auth.py), and the worker skips any whose sign-in has ended.
- `push_due` (the worker, every pass): in each workspace, notifications not looked at yet
  are claimed with SKIP LOCKED under a lease (other workers skip them). Ones with nothing
  to send are marked handled at once. The rest are sent outside the transaction, at most
  10 at a time, until the pass's 20 s are up; then each is marked handled (sent, or
  refused for good), failed (retried later, given up after 5 tries) or released (not
  tried: the next pass). Tokens the push service says are gone are deleted. With push off
  (no settings), notifications are only marked handled.
- A refused key stops sending for this pass, logged once (the reason only, never the key
  or a token); what wasn't sent stays for the next pass.
- `tell_handover`: a Cevanos chat waiting for Accept puts a `handover` notification in
  the bell of its assignee, else of the team's Available agents (and so a banner).
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import push as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import push as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.push_client import PushError, Sent

logger = logging.getLogger("cedesk.push")


class Sender(Protocol):
    def opened(self) -> AbstractAsyncContextManager[None]: ...

    async def send(
        self, device_token: str, title: str, body: str, data: dict[str, str]
    ) -> Sent: ...


@dataclass(frozen=True)
class Device:
    id: UUID
    platform: str
    token_hint: str
    created_at: datetime
    last_seen_at: datetime


def _device(r: RowMapping) -> Device:
    return Device(r["id"], str(r["platform"]), rules.hint(str(r["token"])),
                  r["created_at"], r["last_seen_at"])  # fmt: skip


async def register(sm: Sessionmaker, caller: Caller, *, platform: str, token: str) -> Device:
    require(caller, Action.read_ticket)
    if caller.api_key_id is not None:
        raise Forbidden("Register a phone signed in from the app, not with an API key.")
    try:
        clean = rules.check_token(token)
        kind = rules.Platform(platform)
    except (rules.BadToken, ValueError) as exc:
        raise Invalid(str(exc) if isinstance(exc, rules.BadToken) else
                      "platform is ios or android.") from exc  # fmt: skip
    async with workspace_session(sm, caller.workspace_id) as s:
        device_id = await repo.register(
            s, caller.workspace_id, caller.member_id, caller.session_id, kind.value, clean
        )
        if device_id is None:
            raise Conflict(
                "This phone is registered to someone who is still signed in on it. "
                "They sign out first."
            )
        await repo.keep_newest(s, caller.member_id, rules.MAX_DEVICES)
        rows = await repo.devices_of(s, caller.member_id)
    return next(_device(r) for r in rows if r["id"] == device_id)


async def devices(sm: Sessionmaker, caller: Caller) -> list[Device]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return [_device(r) for r in await repo.devices_of(s, caller.member_id)]


async def unregister(sm: Sessionmaker, caller: Caller, device_id: UUID) -> None:
    """Your own device only (another person's answers 404, like one that doesn't exist)."""
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.remove(s, caller.member_id, device_id):
            raise NotFound("Device not found.")


async def tell_handover(s: AsyncSession, workspace_id: UUID, ticket_id: UUID, number: int) -> None:
    """A Cevanos chat waits for Accept: its assignee, else the team's Available agents,
    get a `handover` notification (and a banner on their phones)."""
    who = await repo.handover_recipients(s, ticket_id)
    if who:
        await message_repo.notify(s, workspace_id=workspace_id, member_ids=who,
                                  ticket_id=ticket_id, kind="handover",
                                  body=rules.handover_text(label(number)))  # fmt: skip


# Sending (the worker)


@dataclass
class _Pass:
    sender: Sender | None
    now: datetime
    deadline: float
    clock: Callable[[], float]
    gate: asyncio.Semaphore
    refused: bool = False  # the key was refused: no more sends this pass
    sent: int = 0

    def over(self) -> bool:
        return self.refused or self.clock() >= self.deadline


@dataclass
class _Job:
    """One notification's banner to one person's phones, and how it went."""

    id: UUID
    tokens: list[str]
    title: str
    body: str
    data: dict[str, str]
    ok: int = 0
    retry: int = 0
    final: int = 0  # refused for good (gone, or the message refused)
    gone: list[str] = field(default_factory=list)

    def outcome(self) -> str:
        if self.ok or self.final == len(self.tokens):
            return "handled"
        if self.retry:
            return "failed"
        return "release"  # not tried (or not all): the next pass


async def push_due(
    sm: Sessionmaker,
    sender: Sender | None,
    *,
    now: datetime | None = None,
    budget: float = rules.PASS_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Returns how many banners were sent, in every workspace."""
    run = _Pass(sender, now or datetime.now(UTC), clock() + budget, clock,
                asyncio.Semaphore(rules.CONCURRENCY))  # fmt: skip
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    opened = sender.opened() if sender is not None else contextlib.nullcontext()
    async with opened:
        for workspace_id in workspace_ids:
            await _workspace(sm, run, workspace_id)
            if run.over():
                break
    if run.refused:
        logger.error("push stopped: the push service refused the key; banners wait",
                     extra={"event": "push"})  # fmt: skip
    return run.sent


async def _workspace(sm: Sessionmaker, run: _Pass, workspace_id: UUID) -> None:
    while not run.over():
        async with workspace_session(sm, workspace_id) as s:
            claimed = await repo.claim(s, rules.BATCH, rules.LEASE)
            jobs, done = await _jobs(s, run, claimed)
            await repo.handled(s, done)
        if jobs:
            await _send_all(run, jobs)
            gone = [t for j in jobs for t in j.gone]
            by = {k: [j.id for j in jobs if j.outcome() == k]
                  for k in ("handled", "failed", "release")}  # fmt: skip
            async with workspace_session(sm, workspace_id) as s:
                await repo.handled(s, by["handled"])
                await repo.failed(s, by["failed"], rules.RETRY_AFTER, rules.MAX_ATTEMPTS)
                await repo.release(s, by["release"])
                if gone:
                    await repo.remove_tokens(s, gone)
        if len(claimed) < rules.BATCH:
            return


async def _jobs(
    s: AsyncSession, run: _Pass, rows: Sequence[RowMapping]
) -> tuple[list[_Job], list[UUID]]:
    """What to send, and the ids with nothing to send (handled now)."""
    wanted = [
        n for n in rows
        if run.sender is not None and n["kind"] in rules.BANNERS
        and run.now - n["created_at"] <= rules.MAX_AGE
    ]  # fmt: skip
    jobs: list[_Job] = []
    if wanted:
        by_member, tickets = await repo.targets(
            s, list({n["member_id"] for n in wanted}), list({n["ticket_id"] for n in wanted})
        )
        for n in wanted:
            ticket = tickets.get(n["ticket_id"])
            tokens = [str(d["token"]) for d in by_member.get(n["member_id"], [])]
            if ticket is None or not tokens:
                continue  # the ticket is gone since, or no phone to send to
            waiting = not ticket["accepted"]
            if (n["kind"] == "assigned" and waiting) or (n["kind"] == "handover" and not waiting):
                # A chat waiting for Accept: the handover banner says it, not "assigned";
                # one accepted since needs no handover banner.
                continue
            shown = rules.banner(str(n["kind"]), label(int(ticket["number"])))
            assert shown is not None
            data = {"ticket_id": str(n["ticket_id"]), "notification_id": str(n["id"]),
                    "kind": str(n["kind"])}  # fmt: skip
            jobs.append(_Job(n["id"], tokens, shown[0], shown[1], data))
    sending = {j.id for j in jobs}
    return jobs, [n["id"] for n in rows if n["id"] not in sending]


async def _send_all(run: _Pass, jobs: list[_Job]) -> None:
    """Every job's sends, 10 at a time, until the pass's time is up (the rest are left)."""
    tasks = [asyncio.create_task(_send(run, j, t)) for j in jobs for t in j.tokens]
    _, pending = await asyncio.wait(tasks, timeout=max(run.deadline - run.clock(), 0))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _send(run: _Pass, job: _Job, token: str) -> None:
    assert run.sender is not None
    async with run.gate:
        if run.over():
            return
        try:
            result = await run.sender.send(token, job.title, job.body, job.data)
        except PushError as exc:
            if exc.config:
                run.refused = True
            else:
                job.retry += 1  # the push service can't be reached now
            return
        except Exception:  # one bad send never ends the pass
            logger.exception("push send failed", extra={"event": "push"})
            job.retry += 1
            return
    if result.ok:
        job.ok += 1
        run.sent += 1
    elif result.retry:
        job.retry += 1
    else:
        job.final += 1
        if result.gone:
            job.gone.append(token)
