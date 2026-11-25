"""Contact merge (feature-gap plan 2.4); what moves is in domain/contact_merge.py.

- `merge_contacts` (Admins and Supervisors; a custom role with the settings right).
- A preview first (`preview`): what moves, the conflicts, and what the merge would end.
  The merge itself needs `confirm: true` and can't be undone: the duplicate's row is
  deleted.
- Never an erased customer (md S11), and never a duplicate with privacy requests (those
  are a record about that customer; erase or keep it as it is).
- The duplicate's email becomes another address of the survivor (`hd_contact_alias`): new
  mail, tickets, imports and portal sign-ins from it land on the survivor. Its portal
  sign-ins end (they were for the other address): that customer signs in again.
- Locks: both customers' tickets first, then both contacts in id order, the order a
  ticket change takes, so a merge can't deadlock with one.
- Audited (area `contacts`) by ids and counts only, never names or addresses (md S11).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import contact_merge as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import campaigns as campaign_repo
from cedesk.helpdesk.repo import contacts as repo
from cedesk.helpdesk.repo.contacts import Row
from cedesk.helpdesk.service import audit, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.contacts import NOT_FOUND, contact_target
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

CONFIRM = "Send confirm: true to merge. A merge can't be undone."


@dataclass(frozen=True)
class Preview:
    survivor: Row
    duplicate: Row
    moves: dict[str, int]
    plan: rules.MergePlan
    blocked: str | None
    warnings: list[str] = field(default_factory=list)


def _blocked(survivor: Row, duplicate: Row, moves: Mapping[str, int]) -> str | None:
    if survivor["erased_at"] is not None or duplicate["erased_at"] is not None:
        return "An erased customer can't be merged."
    if moves["privacy_requests"]:
        return (
            "The duplicate has privacy requests (an export or erase). They are a record "
            "about that customer, so it can't be merged."
        )
    return None


def _warnings(duplicate: Row, moves: Mapping[str, int]) -> list[str]:
    out = [
        "Mail, tickets and portal sign-ins from the duplicate's address will go to the "
        "survivor, and that address will see all the survivor's tickets in the portal.",
        "This can't be undone.",
    ]
    if moves["portal_sessions"]:
        out.insert(0, "The duplicate is signed in to the portal: those sign-ins will end.")
    return out


async def _both(s: AsyncSession, survivor_id: UUID, duplicate_id: UUID) -> tuple[Row, Row]:
    if survivor_id == duplicate_id:
        raise Invalid("Choose two different customers.")
    survivor = await repo.get(s, survivor_id)
    duplicate = await repo.get(s, duplicate_id)
    if survivor is None or duplicate is None:
        raise NotFound(NOT_FOUND)
    return survivor, duplicate


async def _preview(s: AsyncSession, survivor: Row, duplicate: Row) -> Preview:
    moves = await repo.merge_counts(s, duplicate["id"])
    moves["other_emails"] += 1  # its own address
    return Preview(
        survivor=survivor,
        duplicate=duplicate,
        moves=moves,
        plan=rules.plan(survivor, duplicate),
        blocked=_blocked(survivor, duplicate, moves),
        warnings=_warnings(duplicate, moves),
    )


async def preview(
    sm: Sessionmaker, caller: Caller, survivor_id: UUID, duplicate_id: UUID
) -> Preview:
    require(caller, Action.merge_contacts)
    async with workspace_session(sm, caller.workspace_id) as s:
        survivor, duplicate = await _both(s, survivor_id, duplicate_id)
        return await _preview(s, survivor, duplicate)


async def merge(
    sm: Sessionmaker, caller: Caller, survivor_id: UUID, duplicate_id: UUID, *, confirm: bool
) -> tuple[Row, dict[str, Any]]:
    """The survivor after the merge, and what moved."""
    require(caller, Action.merge_contacts)
    if not confirm:
        raise Invalid(CONFIRM)
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.lock_tickets(s, [survivor_id, duplicate_id])
        await repo.lock(s, [survivor_id, duplicate_id])
        survivor, duplicate = await _both(s, survivor_id, duplicate_id)
        found = await _preview(s, survivor, duplicate)
        if found.blocked:
            raise Conflict(found.blocked)
        moved = await repo.move_everything(s, duplicate_id, survivor_id)
        await repo.add_alias(s, caller.workspace_id, survivor_id, str(duplicate["email"]))
        moved["other_emails"] += 1
        p = found.plan
        await repo.merge_profile(
            s,
            survivor_id,
            phone=p.phone,
            organization=p.organization,
            vip=p.vip,
            customer_since=p.customer_since,
            attributes=p.attributes,
        )
        await repo.record_merge(
            s,
            caller.workspace_id,
            survivor_id=survivor_id,
            duplicate=duplicate,
            merged_by=caller.member_id,
            moved=moved,
        )
        await campaign_repo.skip_merged(s, duplicate_id)
        await repo.delete_contact(s, duplicate_id)
        # Moved tickets now belong to the survivor, whose VIP flag picks their SLA policy.
        if moved["tickets"] or p.vip != survivor["vip"]:
            await sla.refresh(s, contact_id=survivor_id)
        await audit.record(
            s,
            caller,
            Area.contacts,
            "merged",
            contact_target(survivor_id),
            target_id=survivor_id,
            detail={
                "merged_id": str(duplicate_id),
                "moved": moved,
                "taken": p.taken,
                "conflicts": [c.field for c in p.conflicts],
            },
        )
        after = await repo.get(s, survivor_id)
    assert after is not None
    return after, moved
