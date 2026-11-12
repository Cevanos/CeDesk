"""SQL for Jira issues on tickets (md X10). RLS applies."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def add(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID,
    key: str,
    url: str,
    made_here: bool,
    by: UUID,
) -> bool:
    """False when the ticket already has this issue."""
    result = await session.execute(
        text(
            "INSERT INTO hd_jira_issue (workspace_id, ticket_id, issue_key, url, made_here, "
            "created_by) VALUES (:ws, :ticket, :key, :url, :made, :by) "
            "ON CONFLICT (workspace_id, ticket_id, issue_key) DO NOTHING RETURNING issue_key"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "key": key,
            "url": url,
            "made": made_here,
            "by": by,
        },
    )
    return result.first() is not None


async def for_ticket(session: AsyncSession, ticket_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT j.issue_key, j.url, j.made_here, j.created_at, m.name AS created_by_name "
            "FROM hd_jira_issue j LEFT JOIN member m ON m.id = j.created_by "
            "WHERE j.ticket_id = :ticket ORDER BY j.created_at, j.issue_key"
        ),
        {"ticket": ticket_id},
    )
    return result.mappings().all()


async def count(session: AsyncSession, ticket_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_jira_issue WHERE ticket_id = :t"), {"t": ticket_id}
    )
    return int(result.scalar_one())


async def remove(session: AsyncSession, ticket_id: UUID, key: str) -> bool:
    result = await session.execute(
        text(
            "DELETE FROM hd_jira_issue WHERE ticket_id = :t AND issue_key = :key "
            "RETURNING issue_key"
        ),
        {"t": ticket_id, "key": key},
    )
    return result.first() is not None
