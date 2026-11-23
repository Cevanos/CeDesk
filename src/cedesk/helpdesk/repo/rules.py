"""SQL for automation rules, and the ticket facts rules look at. Always run inside a
`workspace_session`."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_COLUMNS = (
    "SELECT id, name, enabled, position, event, conditions, actions, max_runs_per_hour, "
    "max_runs_per_day, created_at, updated_at FROM hd_rule"
)


_INSERT_SQL = (
    "INSERT INTO hd_rule (workspace_id, name, enabled, event, conditions, actions, created_by, "
    "max_runs_per_hour, max_runs_per_day, position) VALUES (:ws, :name, :enabled, :event, "
    "CAST(:conditions AS jsonb), CAST(:actions AS jsonb), :by, :per_hour, :per_day, "
    "(SELECT coalesce(max(position), -1) + 1 FROM hd_rule))"
)
_INSERT = text(_INSERT_SQL + " RETURNING id")
_INSERT_SKIP = text(_INSERT_SQL + " ON CONFLICT (workspace_id, name) DO NOTHING RETURNING id")


async def list_rules(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_COLUMNS + " ORDER BY position, created_at, id"))
    return result.mappings().all()


async def enabled_rules(session: AsyncSession, event: str) -> Sequence[Row]:
    result = await session.execute(
        text(_COLUMNS + " WHERE enabled AND event = :event ORDER BY position, created_at, id"),
        {"event": event},
    )
    return result.mappings().all()


async def get_rule(session: AsyncSession, rule_id: UUID) -> Row | None:
    result = await session.execute(text(_COLUMNS + " WHERE id = :id"), {"id": rule_id})
    return result.mappings().first()


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_rule"))
    return int(result.scalar_one())


async def insert_rule(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    name: str,
    enabled: bool,
    event: str,
    conditions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    created_by: UUID | None,
    skip_existing: bool = False,
    max_runs_per_hour: int = 1000,
    max_runs_per_day: int = 10000,
) -> UUID | None:
    """Added last. With `skip_existing`, a rule with that name is left alone (None)."""
    result = await session.execute(
        _INSERT_SKIP if skip_existing else _INSERT,
        {
            "ws": workspace_id,
            "name": name,
            "enabled": enabled,
            "event": event,
            "conditions": json.dumps(conditions),
            "actions": json.dumps(actions),
            "by": created_by,
            "per_hour": max_runs_per_hour,
            "per_day": max_runs_per_day,
        },
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def update_rule(
    session: AsyncSession,
    rule_id: UUID,
    *,
    name: str | None,
    enabled: bool | None,
    event: str | None,
    conditions: list[dict[str, Any]] | None,
    actions: list[dict[str, Any]] | None,
    max_runs_per_hour: int | None = None,
    max_runs_per_day: int | None = None,
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_rule SET name = coalesce(:name, name), "
            "enabled = coalesce(:enabled, enabled), event = coalesce(:event, event), "
            "conditions = coalesce(CAST(:conditions AS jsonb), conditions), "
            "actions = coalesce(CAST(:actions AS jsonb), actions), "
            "max_runs_per_hour = coalesce(CAST(:per_hour AS integer), max_runs_per_hour), "
            "max_runs_per_day = coalesce(CAST(:per_day AS integer), max_runs_per_day), "
            "updated_at = now() WHERE id = :id RETURNING id"
        ),
        {
            "per_hour": max_runs_per_hour,
            "per_day": max_runs_per_day,
            "id": rule_id,
            "name": name,
            "enabled": enabled,
            "event": event,
            "conditions": None if conditions is None else json.dumps(conditions),
            "actions": None if actions is None else json.dumps(actions),
        },
    )
    return result.first() is not None


async def delete_rule(session: AsyncSession, rule_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_rule WHERE id = :id RETURNING id"), {"id": rule_id}
    )
    return result.first() is not None


async def lock_rules(session: AsyncSession) -> list[UUID]:
    """Every rule id, locked, so two reorders can't interleave."""
    result = await session.execute(text("SELECT id FROM hd_rule ORDER BY id FOR UPDATE"))
    return [UUID(str(r[0])) for r in result.all()]


async def reorder_rules(session: AsyncSession, rule_ids: Sequence[UUID]) -> None:
    await session.execute(
        text(
            "UPDATE hd_rule r SET position = o.pos - 1, updated_at = now() "
            "FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS o(id, pos) WHERE r.id = o.id"
        ),
        {"ids": list(rule_ids)},
    )


async def names(session: AsyncSession, table: str) -> dict[UUID, str]:
    """Ticket type or team names by id, for rule lines ("Team Billing")."""
    if table not in ("hd_ticket_type", "hd_team"):
        raise ValueError(table)
    result = await session.execute(text(f"SELECT id, name FROM {table}"))  # noqa: S608
    return {UUID(str(r[0])): str(r[1]) for r in result.all()}


async def name_ids(session: AsyncSession, table: str) -> dict[str, UUID]:
    """The reverse, for the defaults: the mock's rules name their team and type."""
    return {name: rid for rid, name in (await names(session, table)).items()}


async def customer_text(session: AsyncSession, ticket_id: UUID) -> str:
    """Every customer message on the ticket, oldest first, for text conditions."""
    result = await session.execute(
        text(
            "SELECT coalesce(string_agg(body, ' ' ORDER BY created_at, id), '') "
            "FROM hd_message WHERE ticket_id = :id AND kind = 'customer'"
        ),
        {"id": ticket_id},
    )
    return str(result.scalar_one())


async def to_address(session: AsyncSession, ticket_id: UUID) -> str | None:
    """The address the customer first wrote to (email tickets): the first customer
    message's `email.to`."""
    result = await session.execute(
        text(
            "SELECT email->>'to' FROM hd_message WHERE ticket_id = :id AND kind = 'customer' "
            "AND email IS NOT NULL ORDER BY created_at, id LIMIT 1"
        ),
        {"id": ticket_id},
    )
    value = result.scalar_one_or_none()
    return None if value is None else str(value)
