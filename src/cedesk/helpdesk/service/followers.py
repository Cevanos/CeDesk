"""Ticket followers (feature-gap plan 1.5).

- A follower hears (notification kind `following`, the bell and the live stream) about
  the ticket's public replies, from an agent or the customer, and its status changes.
  Whoever made the change is never told about it, and the assignee is not told twice
  about a customer's reply.
- Members follow a ticket by hand, or are added when they are assigned it or @mentioned
  on it. They stay following until they (or someone who can change the ticket) remove
  them.
- Following yourself needs only the right to read the ticket. Adding or removing someone
  else needs the right to change it (`change_fields`, and the role's team scope); the
  person added must be active and able to see the ticket.
- Every add and remove by hand is audited (area `tickets`).
"""

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import followers as repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.followers import Row
from cedesk.helpdesk.service import audit, scope
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

KIND = "following"
NOT_FOUND = "Ticket not found."


async def follow(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    member_ids: Iterable[UUID | None],
    added_by: UUID | None = None,
) -> None:
    """Adds them (in the caller's transaction); those already following stay as they were."""
    ids = sorted({m for m in member_ids if m is not None})
    await repo.add(s, workspace_id, ticket_id, ids, added_by)


async def tell(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    body: str,
    *,
    exclude: Iterable[UUID | None] = (),
) -> None:
    """Notifies the ticket's active followers, except `exclude` (who made the change, and
    anyone already told about it)."""
    skip = {m for m in exclude if m is not None}
    people = [m for m in await repo.active_followers(s, ticket_id) if m not in skip]
    if people:
        await message_repo.notify(
            s,
            workspace_id=workspace_id,
            member_ids=people,
            ticket_id=ticket_id,
            kind=KIND,
            body=body[:500],
        )


async def _ticket(s: AsyncSession, caller: Caller, ticket_id: UUID, *, edit: bool) -> Row:
    await scope.check(s, caller, ticket_id, edit=edit)
    ticket = await ticket_repo.ticket_detail(s, ticket_id)
    if ticket is None:
        raise NotFound(NOT_FOUND)
    return ticket


async def list_followers(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        await _ticket(s, caller, ticket_id, edit=False)
        return await repo.list_for(s, ticket_id)


def _for_someone_else(caller: Caller, member_id: UUID) -> bool:
    if member_id == caller.member_id:
        return False
    require(caller, Action.change_fields)
    return True


async def add_follower(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, member_id: UUID
) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    other = _for_someone_else(caller, member_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await _ticket(s, caller, ticket_id, edit=other)
        member = await ticket_repo.member_brief(s, member_id)
        if member is None:
            raise Invalid("That person isn't a member of this workspace.")
        if not member["is_active"]:
            raise Invalid(f"{member['name']} is deactivated, so can't follow tickets.")
        if not member_access(member).sees(ticket["team_id"]):
            raise Invalid(f"{member['name']}'s role can't see this ticket.")
        added = await repo.add(
            s, caller.workspace_id, ticket_id, [member_id], caller.member_id if other else None
        )
        if added:
            await audit.record(
                s, caller, Area.tickets, "follower_added", label(int(ticket["number"])),
                target_id=ticket_id,
                detail={"member_id": str(member_id), "member": str(member["name"])},
            )  # fmt: skip
        return await repo.list_for(s, ticket_id)


async def remove_follower(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, member_id: UUID
) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    other = _for_someone_else(caller, member_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await _ticket(s, caller, ticket_id, edit=other)
        if not await repo.remove(s, ticket_id, member_id):
            raise NotFound("That person isn't following this ticket.")
        member = await ticket_repo.member_brief(s, member_id)
        await audit.record(
            s, caller, Area.tickets, "follower_removed", label(int(ticket["number"])),
            target_id=ticket_id,
            detail={"member_id": str(member_id), "member": str(member["name"]) if member else ""},
        )  # fmt: skip
        return await repo.list_for(s, ticket_id)
