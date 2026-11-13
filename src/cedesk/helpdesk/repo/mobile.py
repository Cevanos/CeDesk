"""SQL for the mobile agent app (md P8). RLS applies."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

_QUEUE = """
SELECT t.id, t.number, t.subject, t.status, t.priority, t.channel, t.accepted,
       t.sla_due_at, t.assignee_id, t.updated_at, c.name AS contact_name,
       (t.assignee_id IS NOT NULL) AS mine,
       last.kind AS last_kind, left(last.body, :preview) AS last_body,
       last.created_at AS last_at
FROM hd_ticket t
JOIN hd_contact c ON c.id = t.contact_id
LEFT JOIN LATERAL (
    SELECT m.kind, m.body, m.created_at FROM hd_message m
    WHERE m.workspace_id = t.workspace_id AND m.ticket_id = t.id
      AND m.kind IN ('customer', 'agent', 'ai')
    ORDER BY m.created_at DESC LIMIT 1
) last ON true
WHERE t.status <> 'resolved' AND t.merged_into_id IS NULL
  AND (t.assignee_id = :me OR t.assignee_id IS NULL)
  AND (CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))
ORDER BY (t.assignee_id IS NOT NULL) DESC, t.accepted, t.sla_due_at NULLS LAST,
         t.created_at, t.id
LIMIT :limit
"""


async def queue(
    session: AsyncSession, member_id: UUID, team_id: UUID | None, limit: int, preview: int
) -> Sequence[RowMapping]:
    """Not resolved, mine or unassigned: mine first, then chats waiting to be accepted,
    then by SLA time left, then oldest; each with its last customer-visible message."""
    result = await session.execute(
        text(_QUEUE),
        {"me": member_id, "team": team_id, "limit": limit, "preview": preview},
    )
    return result.mappings().all()
