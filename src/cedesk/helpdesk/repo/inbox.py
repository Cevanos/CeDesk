"""SQL for the inbox tools' background passes and the transcript (feature-gap plan phase
1): waking snoozed tickets, the auto-close setting and its candidates, and a ticket's
public messages. Run inside a `workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.transcript import PUBLIC_KINDS

Row = RowMapping

# The columns `tickets.lock_ticket` reads, plus the source.
_COLUMNS = (
    "t.id, t.number, t.subject, t.status, t.priority, t.channel, t.type_id, t.team_id, "
    "t.assignee_id, t.fields, t.tags, t.resolved_at, t.reopen_count, t.updated_at, "
    "t.parent_id, t.merged_into_id, t.snoozed_until, t.source"
)


async def due_snoozes(session: AsyncSession, now: datetime, limit: int) -> Sequence[Row]:
    """Snoozed tickets whose time has come, soonest first, locked; tickets another worker
    holds are skipped (it wakes them)."""
    result = await session.execute(
        text(
            # Fixed strings only: _COLUMNS is a constant.
            "SELECT " + _COLUMNS + " FROM hd_ticket t "  # noqa: S608
            "WHERE t.snoozed_until IS NOT NULL AND t.snoozed_until <= :now "
            "ORDER BY t.snoozed_until, t.id LIMIT :limit FOR UPDATE SKIP LOCKED"
        ),
        {"now": now, "limit": limit},
    )
    return result.mappings().all()


# Auto-close


async def auto_close_setting(session: AsyncSession) -> Row | None:
    result = await session.execute(
        text(
            "SELECT a.enabled, a.after_days, a.notice, a.updated_at, m.name AS updated_by_name "
            "FROM hd_auto_close a LEFT JOIN member m ON m.id = a.updated_by"
        )
    )
    return result.mappings().first()


async def save_auto_close(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    enabled: bool,
    after_days: int,
    notice: str | None,
    by: UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_auto_close (workspace_id, enabled, after_days, notice, updated_by) "
            "VALUES (:ws, :enabled, :days, :notice, :by) ON CONFLICT (workspace_id) DO UPDATE "
            "SET enabled = EXCLUDED.enabled, after_days = EXCLUDED.after_days, "
            "notice = EXCLUDED.notice, updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"ws": workspace_id, "enabled": enabled, "days": after_days, "notice": notice, "by": by},
    )


async def auto_close_candidates(
    session: AsyncSession, cutoff: datetime, skip_sources: Sequence[str], limit: int
) -> Sequence[Row]:
    """Pending tickets untouched since `cutoff` with no customer message since then, that
    nothing holds (domain/auto_close.py lists what does). Locked; tickets another worker
    holds are skipped."""
    result = await session.execute(
        text(
            # Fixed strings only: _COLUMNS is a constant.
            "SELECT " + _COLUMNS + " FROM hd_ticket t "  # noqa: S608
            "WHERE t.status = 'pending' AND t.snoozed_until IS NULL AND t.accepted "
            "AND t.merged_into_id IS NULL AND t.updated_at < :cutoff "
            "AND (t.source IS NULL OR NOT t.source = ANY(CAST(:skip AS text[]))) "
            "AND NOT EXISTS (SELECT 1 FROM hd_message m WHERE m.ticket_id = t.id "
            "                AND m.kind = 'customer' AND m.created_at >= :cutoff) "
            "AND NOT EXISTS (SELECT 1 FROM hd_approval a WHERE a.ticket_id = t.id "
            "                AND a.state = 'pending') "
            "AND NOT EXISTS (SELECT 1 FROM hd_ticket c WHERE c.parent_id = t.id "
            "                AND c.status <> 'resolved') "
            "ORDER BY t.updated_at, t.id LIMIT :limit FOR UPDATE OF t SKIP LOCKED"
        ),
        {"cutoff": cutoff, "skip": list(skip_sources), "limit": limit},
    )
    return result.mappings().all()


# Transcript


async def public_messages(session: AsyncSession, ticket_id: UUID) -> Sequence[Row]:
    """What the customer saw, oldest first: customer, agent and AI messages (never notes
    or system lines), with the names of their files."""
    result = await session.execute(
        text(
            "SELECT m.kind, m.author_name, m.body, m.created_at, "
            "coalesce((SELECT array_agg(f.name ORDER BY f.created_at, f.id) "
            "          FROM hd_attachment f WHERE f.message_id = m.id), '{}') AS files "
            "FROM hd_message m WHERE m.ticket_id = :ticket "
            "AND m.kind = ANY(CAST(:kinds AS text[])) ORDER BY m.created_at, m.id"
        ),
        {"ticket": ticket_id, "kinds": list(PUBLIC_KINDS)},
    )
    return result.mappings().all()
