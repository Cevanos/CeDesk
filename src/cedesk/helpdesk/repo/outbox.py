"""SQL for the outbox. Always run inside a `workspace_session`, in the transaction of the
change the event describes."""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def add(
    session: AsyncSession,
    workspace_id: UUID,
    event: str,
    ticket_id: UUID | None,
    payload: Mapping[str, Any],
) -> UUID:
    """Stores the event; its `id` and `occurred_at` go into the payload too (PL5)."""
    result = await session.execute(
        text(
            "WITH v AS (SELECT gen_random_uuid() AS id, clock_timestamp() AS at) "
            "INSERT INTO hd_outbox (id, workspace_id, event, ticket_id, payload, occurred_at) "
            "SELECT v.id, :ws, :event, :ticket_id, CAST(:payload AS jsonb) "
            "|| jsonb_build_object('id', CAST(v.id AS text), 'occurred_at', v.at), v.at "
            "FROM v RETURNING id"
        ),
        {
            "ws": workspace_id,
            "event": event,
            "ticket_id": ticket_id,
            "payload": json.dumps(payload),
        },
    )
    return UUID(str(result.scalar_one()))


async def for_ticket(session: AsyncSession, ticket_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT id, event, ticket_id, payload, occurred_at, published_at FROM hd_outbox "
            "WHERE ticket_id = :id ORDER BY seq"
        ),
        {"id": ticket_id},
    )
    return result.mappings().all()
