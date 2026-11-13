"""SQL for the demo data (md D1-D3). RLS applies: only the caller's workspace."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def delete_tickets(session: AsyncSession, workspace_id: UUID) -> int:
    """Every ticket of the workspace, with what hangs off one (messages, notes, tasks,
    time, approvals, ratings, links, notifications: ON DELETE CASCADE). Contacts stay."""
    result = await session.execute(
        text("DELETE FROM hd_ticket WHERE workspace_id = :ws RETURNING id"), {"ws": workspace_id}
    )
    return len(result.all())


_MESSAGES = """
UPDATE hd_message m
SET created_at = m.created_at - CASE
    WHEN m.created_at < (SELECT min(r.created_at) FROM hd_message r
                         WHERE r.workspace_id = m.workspace_id AND r.ticket_id = m.ticket_id
                           AND r.kind IN ('agent', 'ai'))
    THEN CAST(:early AS interval) ELSE CAST(:late AS interval) END
WHERE m.workspace_id = :ws AND m.ticket_id = :id
"""

_TICKET = """
UPDATE hd_ticket
SET created_at = created_at - CAST(:early AS interval),
    sla_due_at = sla_due_at - CAST(:early AS interval),
    first_reply_at = first_reply_at - CAST(:late AS interval),
    resolved_at = resolved_at - CAST(:late AS interval),
    updated_at = updated_at - CAST(:late AS interval)
WHERE workspace_id = :ws AND id = :id
"""

_CSAT = """
UPDATE hd_csat
SET created_at = created_at - CAST(:late AS interval),
    rated_at = rated_at - CAST(:late AS interval)
WHERE workspace_id = :ws AND ticket_id = :id
"""


async def backdate(
    session: AsyncSession, workspace_id: UUID, ticket_id: UUID, *, ago: timedelta, took: timedelta
) -> None:
    """A resolved demo ticket moves `ago` into the past. What came before the first agent
    reply (the customer's message, the routing lines) moves `took` further, so reports
    show a first reply and a resolution that took `took`."""
    values = {"ws": workspace_id, "id": ticket_id, "early": ago + took, "late": ago}
    await session.execute(text(_MESSAGES), values)
    await session.execute(text(_TICKET), values)
    await session.execute(text(_CSAT), values)
