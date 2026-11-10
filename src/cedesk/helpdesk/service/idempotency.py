"""`Idempotency-Key` (md 2B): a POST sent again with the same key returns what the first
one made instead of making a second one. A phone that lost the answer to "send reply" can
simply send it again.

The key is taken inside the transaction of the change (`begin`), and the made thing's id
is saved in it (`remember`): both commit together or not at all. A repeat that arrives
while the first is still running waits for it."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import idempotency as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import idempotency as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid


@dataclass
class Idempotency:
    key: str
    fingerprint: bytes
    replayed: bool = False
    """Set by the service: the answer is the earlier request's (the API says so in the
    `Idempotent-Replayed` header)."""


async def begin(s: AsyncSession, caller: Caller, idem: Idempotency | None) -> UUID | None:
    """None: go ahead (and call `remember`). Otherwise the id the earlier request made."""
    if idem is None:
        return None
    earlier = await repo.claim(
        s, caller.workspace_id, caller.member_id, idem.key, idem.fingerprint, rules.LIFETIME
    )
    if earlier is None:
        return None
    if bytes(earlier["fingerprint"]) != idem.fingerprint:
        raise Invalid("This Idempotency-Key was already used for a different request.")
    if earlier["resource_id"] is None:  # can't happen: saved in the same transaction
        raise Invalid("This Idempotency-Key's request did not finish. Use a new key.")
    idem.replayed = True
    return UUID(str(earlier["resource_id"]))


async def remember(
    s: AsyncSession, caller: Caller, idem: Idempotency | None, resource_id: UUID
) -> None:
    if idem is not None:
        await repo.remember(s, caller.member_id, idem.key, resource_id)


async def sweep_everywhere(sm: Sessionmaker) -> int:
    """Removes keys past their 24 hours, in every workspace (the worker, each pass)."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    removed = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            removed += await repo.sweep(s, rules.LIFETIME)
    return removed
