"""Parent and child tickets (md X4), used wherever an agent resolves a ticket:
a parent can't be resolved while a child is open, and resolving a child posts a line on
the parent and tells the parent's assignee."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import ticket_tools as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.errors import Conflict


async def refuse_open_children(s: AsyncSession, ticket_id: UUID) -> None:
    """Conflict when the ticket still has an open child (checked before resolving it)."""
    numbers = await repo.open_children(s, ticket_id)
    if numbers:
        open_ones = ", ".join(label(n) for n in numbers[:5])
        more = f" and {len(numbers) - 5} more" if len(numbers) > 5 else ""
        raise Conflict(f"Resolve the child tickets first: {open_ones}{more} still open.")


async def child_resolved(
    s: AsyncSession, workspace_id: UUID, child: Any, who: str, actor_id: UUID | None
) -> None:
    """After a child was resolved: a line on its parent, and a notification for the
    parent's assignee (unless they resolved it themselves). No parent: nothing."""
    parent_id = child["parent_id"]
    if parent_id is None:
        return
    parent = await repo.brief(s, parent_id)
    if parent is None:
        return
    body = f"Child ticket {label(child['number'])} resolved by {who}"
    await ticket_repo.insert_message(
        s,
        workspace_id=workspace_id,
        ticket_id=parent_id,
        kind="system",
        body=body,
        author_member_id=None,
        author_name=None,
    )
    await repo.touch(s, parent_id)
    if parent["assignee_id"] is not None and parent["assignee_id"] != actor_id:
        await message_repo.notify(
            s,
            workspace_id=workspace_id,
            member_ids=[parent["assignee_id"]],
            ticket_id=parent_id,
            kind="child_resolved",
            body=f"{body} (on {label(parent['number'])}: {parent['subject']})"[:500],
        )
