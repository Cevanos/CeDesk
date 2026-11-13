"""SQL for the demo data (md D1-D3). RLS applies: only the caller's workspace."""

from collections.abc import Collection, Sequence
from datetime import timedelta
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.seeds import DEMO_EMAILS, DEMO_SOURCE


async def lock(session: AsyncSession, workspace_id: UUID) -> None:
    """One demo call at a time per workspace: held until this transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{workspace_id}:demo"},
    )


async def demo_ticket_ids(session: AsyncSession) -> list[UUID]:
    """The demo tickets (`source = 'demo'`) of the workspace; nothing else."""
    result = await session.execute(
        text("SELECT id FROM hd_ticket WHERE source = :demo"), {"demo": DEMO_SOURCE}
    )
    return [row[0] for row in result.all()]


async def demo_subjects(session: AsyncSession, subjects: Collection[str]) -> int:
    """How many demo tickets have one of these subjects."""
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_ticket WHERE source = :demo AND subject IN :subjects"
        ).bindparams(bindparam("subjects", expanding=True)),
        {"demo": DEMO_SOURCE, "subjects": list(subjects)},
    )
    return int(result.scalar_one())


async def is_demo(session: AsyncSession, ticket_id: UUID) -> bool:
    """For the outbox handlers: a demo ticket never reaches a webhook, Slack or an email."""
    result = await session.execute(
        text("SELECT source FROM hd_ticket WHERE id = :id"), {"id": ticket_id}
    )
    return result.scalar_one_or_none() == DEMO_SOURCE


async def file_keys(session: AsyncSession, ticket_ids: Sequence[UUID]) -> list[str]:
    """Stored files on these tickets (the seed makes none; an agent may add some)."""
    if not ticket_ids:
        return []
    result = await session.execute(
        text("SELECT object_key FROM hd_attachment WHERE ticket_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": list(ticket_ids)},
    )
    return [str(row[0]) for row in result.all()]


async def delete_tickets(session: AsyncSession, ticket_ids: Sequence[UUID]) -> int:
    """These demo tickets, with what hangs off one (messages, notes, tasks, time,
    approvals, ratings, links, notifications, file rows: ON DELETE CASCADE). Only rows
    with `source = 'demo'`: a ticket made by hand, imported or from a channel is never
    deleted, whatever the list says."""
    if not ticket_ids:
        return 0
    result = await session.execute(
        text("DELETE FROM hd_ticket WHERE source = :demo AND id IN :ids RETURNING id").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"demo": DEMO_SOURCE, "ids": list(ticket_ids)},
    )
    return len(result.all())


async def delete_unused_contacts(session: AsyncSession) -> int:
    """The demo customers (their `.invalid` addresses) with no ticket left. A customer
    with any other ticket, or a privacy request, stays."""
    result = await session.execute(
        text(
            """
            DELETE FROM hd_contact c
            WHERE c.email IN :emails
              AND NOT EXISTS (SELECT 1 FROM hd_ticket t WHERE t.contact_id = c.id)
              AND NOT EXISTS (SELECT 1 FROM hd_privacy_request p WHERE p.contact_id = c.id)
            RETURNING c.id
            """
        ).bindparams(bindparam("emails", expanding=True)),
        {"emails": sorted(DEMO_EMAILS)},
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
