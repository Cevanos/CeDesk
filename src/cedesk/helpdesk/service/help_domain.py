"""Custom domain for the help center (feature-gap plan 4.2).

An Admin (Settings) registers a domain, publishes a TXT record with the workspace's token and
asks for a check; once it is verified the platform can answer "which workspace is this host?"
(`host_workspace`) and tell Caddy it may get a certificate (`tls_allowed`). The worker
re-reads every verified domain's record daily and sends one back to pending after three
failed checks in a row, so a domain the customer let go stops being served.

DNS is read outside any transaction (it can be slow), then the answer is written under a lock
and ignored when the domain was changed in the meantime.
"""

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.dnscheck import LookupFailed, TxtLookup
from cedesk.helpdesk.domain import help_domain as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import help_domain as repo
from cedesk.helpdesk.repo import help_locale as locale_repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger(__name__)

NO_DOMAIN = "This workspace has no help center domain."
NO_HOST = "No help center is served on that host."
PLATFORM_HOST = "That is one of this platform's own addresses: use a domain you own."
TAKEN = "Another workspace has already proven that domain."
DNS_FAILED = "DNS didn't answer; try again in a moment."

RECHECK_AFTER = timedelta(hours=20)  # a verified domain is re-read about once a day
RECHECK_BUDGET_SECONDS = 120.0  # the worker never spends longer than this on it per pass
RECHECK_CONCURRENCY = 10


def _missing(domain: str) -> str:
    return (
        f"No TXT record at {rules.txt_name(domain)} holds the token yet. "
        "DNS changes can take a while to spread."
    )


def _is_due(row: RowMapping, now: datetime) -> bool:
    """Not checked for about a day: a failure now is a new one, not the same one again."""
    last = row["last_checked_at"]
    return last is None or last < now - RECHECK_AFTER


async def get_domain(sm: Sessionmaker, caller: Caller) -> RowMapping | None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.get(s)


async def set_domain(
    sm: Sessionmaker, caller: Caller, value: str, *, reserved: Iterable[str]
) -> RowMapping:
    """Registers the domain (pending, with a new token), replacing an earlier one. Sending the
    domain it already has changes nothing."""
    require(caller, Action.settings)
    try:
        domain = rules.clean_domain(value)
    except rules.BadDomain as exc:
        raise Invalid(str(exc)) from exc
    if rules.is_reserved(domain, reserved):
        raise Invalid(PLATFORM_HOST)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.get(s, lock=True)
        if current is not None and current["domain"] == domain:
            return current
        await repo.put(
            s, workspace_id=caller.workspace_id, domain=domain, token=rules.new_token(),
            member_id=caller.member_id,
        )  # fmt: skip
        await audit.record(
            s, caller, Area.help_domain, "changed" if current else "created",
            f"Help center domain {domain}",
            detail={"domain": domain, "was": current["domain"] if current else None},
        )  # fmt: skip
        found = await repo.get(s)
    assert found is not None
    return found


async def remove_domain(sm: Sessionmaker, caller: Caller) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.get(s, lock=True)
        if current is None or not await repo.delete(s):
            raise NotFound(NO_DOMAIN)
        await audit.record(
            s, caller, Area.help_domain, "deleted", f"Help center domain {current['domain']}",
            detail={"domain": current["domain"], "was": current["status"]},
        )  # fmt: skip


async def check_domain(sm: Sessionmaker, caller: Caller, lookup: TxtLookup) -> RowMapping:
    """Reads the TXT record now. A verified domain stays verified until it has failed three
    checks in a row, a day or so apart: a failure within a day of the last check is shown but
    not counted, so only the worker's daily pass takes a domain down."""
    require(caller, Action.settings)
    found = await _check(sm, caller.workspace_id, lookup, caller)
    if found is None:
        raise NotFound(NO_DOMAIN)
    return found


async def _check(
    sm: Sessionmaker,
    workspace_id: UUID,
    lookup: TxtLookup,
    caller: Caller | None,
    now: datetime | None = None,
) -> RowMapping | None:
    stamp = now or datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.get(s)
    if row is None:
        return None
    domain, token = str(row["domain"]), str(row["verify_token"])
    try:  # outside any transaction: DNS can take its time
        proof = rules.token_present(await lookup.txt(rules.txt_name(domain)), token)
        answered = True
    except LookupFailed:
        proof, answered = False, False

    async def note(s: AsyncSession, action: str, detail: dict[str, object]) -> None:
        target = f"Help center domain {domain}"
        if caller is not None:
            await audit.record(s, caller, Area.help_domain, action, target, detail=detail)
        else:
            await audit.record_server(s, workspace_id, Area.help_domain, action, target,
                                      detail=detail)  # fmt: skip

    try:
        async with workspace_session(sm, workspace_id) as s:
            current = await repo.get(s, lock=True)
            if current is None or (current["domain"], current["verify_token"]) != (domain, token):
                return current  # changed or removed while DNS was being read: this is stale
            if not answered:
                await repo.mark_unknown(s, DNS_FAILED)
            elif proof:
                if current["status"] != "verified":
                    await note(s, "verified", {"domain": domain})
                await repo.mark_verified(s)
            elif current["status"] == "verified" and not _is_due(current, stamp):
                # Checked a moment ago (say, the Admin pressing Check while DNS is being
                # moved): shown, but not counted, so a few clicks never take a domain down.
                await repo.note_error(s, _missing(domain))
            else:
                demote = current["status"] == "verified" and (
                    int(current["failed_checks"]) + 1 >= rules.FAILURES_BEFORE_DEMOTION
                )
                await repo.mark_missing(s, _missing(domain), demote=demote)
                if demote:
                    await note(s, "demoted", {"domain": domain, "reason": "TXT record gone"})
    except IntegrityError:  # the unique index: someone else proved it first
        async with workspace_session(sm, workspace_id) as s:
            await repo.mark_unknown(s, TAKEN)
        raise Conflict(TAKEN) from None
    async with workspace_session(sm, workspace_id) as s:
        return await repo.get(s)


async def recheck_everywhere(
    sm: Sessionmaker,
    lookup: TxtLookup,
    *,
    now: datetime | None = None,
    budget_seconds: float = RECHECK_BUDGET_SECONDS,
    concurrency: int = RECHECK_CONCURRENCY,
) -> int:
    """The worker's daily pass: re-reads each verified domain not checked for about a day,
    never-checked ones first, then the longest ago. Stops starting new checks after the time
    budget (the rest are first in line next time). Returns how many were checked."""
    stamp = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspaces = await repo.directory_workspaces(s)
    due: list[tuple[datetime, UUID]] = []
    for workspace_id in workspaces:
        async with workspace_session(sm, workspace_id) as s:
            row = await repo.due_for_check(s, stamp - RECHECK_AFTER)
        if row is not None:
            due.append((row["last_checked_at"] or datetime.min.replace(tzinfo=UTC), workspace_id))
    due.sort(key=lambda item: item[0])
    deadline = time.monotonic() + budget_seconds
    gate = asyncio.Semaphore(concurrency)

    async def one(workspace_id: UUID) -> int:
        async with gate:
            if time.monotonic() > deadline:
                return 0
            try:
                await _check(sm, workspace_id, lookup, None, stamp)
            except Exception:  # one workspace's trouble never stops the others
                logger.exception("Checking a help center domain failed")
                return 0
            return 1

    return sum(await asyncio.gather(*(one(workspace_id) for _, workspace_id in due)))


# The public side: no sign-in


@dataclass(frozen=True)
class HostWorkspace:
    workspace: str  # the slug the help center's pages use
    default_locale: str


async def _verified_workspace(sm: Sessionmaker, host: str) -> UUID | None:
    try:
        domain = rules.clean_domain(host)
    except rules.BadDomain:
        return None
    async with sm() as s, s.begin():
        return await repo.workspace_for_domain(s, domain)


async def host_workspace(sm: Sessionmaker, host: str) -> HostWorkspace:
    """Which workspace's help center is served on this host: only a verified domain counts."""
    workspace_id = await _verified_workspace(sm, host)
    if workspace_id is None:
        raise NotFound(NO_HOST)
    async with workspace_session(sm, workspace_id) as s:
        row = await auth_repo.workspace_row(s)
        default = await locale_repo.default_locale(s)
    if row is None or not row["slug"]:
        raise NotFound(NO_HOST)
    return HostWorkspace(str(row["slug"]), default)


async def tls_allowed(sm: Sessionmaker, host: str) -> bool:
    """Caddy may ask for a certificate for a verified domain, and nothing else."""
    return await _verified_workspace(sm, host) is not None
