"""SQL for automation (feature-gap plan phase 5): the run log and waiting flow runs, the
flows and their versions, and the extra ticket facts the plan 5.1 conditions read. Always
run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

# Ticket facts


async def reply_times(session: AsyncSession, ticket_id: UUID) -> tuple[Any, Any]:
    """When the customer, and an agent (or an automation), last wrote a public message."""
    result = await session.execute(
        text(
            "SELECT max(created_at) FILTER (WHERE kind = 'customer'), "
            "max(created_at) FILTER (WHERE kind = 'agent') "
            "FROM hd_message WHERE ticket_id = :id"
        ),
        {"id": ticket_id},
    )
    row = result.one()
    return row[0], row[1]


async def contact_state(session: AsyncSession, contact_id: UUID) -> tuple[dict[str, Any], bool]:
    """The contact's attribute values, and whether they were erased (no email goes to an
    erased customer, and their values are gone)."""
    result = await session.execute(
        text("SELECT attributes, erased_at IS NOT NULL FROM hd_contact WHERE id = :id"),
        {"id": contact_id},
    )
    row = result.first()
    if row is None:
        return {}, True
    return (dict(row[0]) if isinstance(row[0], dict) else {}), bool(row[1])


async def followers(session: AsyncSession, ticket_id: UUID) -> frozenset[UUID]:
    result = await session.execute(
        text("SELECT member_id FROM hd_ticket_follower WHERE ticket_id = :id"), {"id": ticket_id}
    )
    return frozenset(UUID(str(r[0])) for r in result.all())


async def segment_filters(session: AsyncSession, segment_ids: Sequence[UUID]) -> Sequence[Row]:
    result = await session.execute(
        text("SELECT id, filters FROM hd_segment WHERE id = ANY(:ids)"),
        {"ids": list(segment_ids)},
    )
    return result.mappings().all()


async def ids_in(session: AsyncSession, table: str, ids: Sequence[UUID]) -> frozenset[UUID]:
    """Which of these ids exist (for a rule's or flow's problems)."""
    if table not in ("hd_segment", "hd_webhook"):
        raise ValueError(table)
    result = await session.execute(
        text(f"SELECT id FROM {table} WHERE id = ANY(:ids)"),  # noqa: S608 (fixed names)
        {"ids": list(ids)},
    )
    return frozenset(UUID(str(r[0])) for r in result.all())


async def active_member_ids(session: AsyncSession, ids: Sequence[UUID]) -> frozenset[UUID]:
    result = await session.execute(
        text("SELECT id FROM member WHERE id = ANY(:ids) AND is_active"), {"ids": list(ids)}
    )
    return frozenset(UUID(str(r[0])) for r in result.all())


async def field_keys(session: AsyncSession) -> frozenset[str]:
    result = await session.execute(text("SELECT key FROM hd_contact_field"))
    return frozenset(str(r[0]) for r in result.all())


async def webhooks(session: AsyncSession, ids: Sequence[UUID]) -> Sequence[Row]:
    """id, url and enabled: never the secret."""
    result = await session.execute(
        text("SELECT id, url, enabled FROM hd_webhook WHERE id = ANY(:ids)"), {"ids": list(ids)}
    )
    return result.mappings().all()


# The run log


_RUN = (
    "SELECT r.id, r.rule_id, r.flow_id, r.flow_version, r.name, r.ticket_id, t.number AS "
    "ticket_number, r.trigger, r.state, r.result, r.steps, r.error, r.depth, r.outward, "
    "r.node_id, r.waits, r.until_reply, r.replied, r.resume_at, r.created_at, r.updated_at, "
    "r.finished_at FROM hd_automation_run r JOIN hd_ticket t ON t.id = r.ticket_id "
)


async def insert_run(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    rule_id: UUID | None,
    flow_id: UUID | None,
    flow_version: int | None,
    name: str,
    ticket_id: UUID,
    trigger: str,
    state: str,
    result: str,
    steps: Sequence[Mapping[str, Any]],
    depth: int,
    outward: bool,
    error: str | None = None,
    node_id: str | None = None,
    waits: int = 0,
    until_reply: bool = False,
    resume_at: datetime | None = None,
) -> UUID:
    row = await session.execute(
        text(
            "INSERT INTO hd_automation_run (workspace_id, rule_id, flow_id, flow_version, name, "
            "ticket_id, trigger, state, result, steps, error, depth, outward, node_id, waits, "
            "until_reply, resume_at, finished_at) VALUES (:ws, :rule, :flow, :version, :name, "
            ":ticket, :trigger, :state, :result, CAST(:steps AS jsonb), :error, :depth, "
            ":outward, :node, :waits, :until_reply, :resume_at, "
            "CASE WHEN :state = 'waiting' THEN NULL ELSE now() END) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "rule": rule_id,
            "flow": flow_id,
            "version": flow_version,
            "name": name[:80],
            "ticket": ticket_id,
            "trigger": trigger,
            "state": state,
            "result": result[:300],
            "steps": json.dumps(list(steps)),
            "error": error[:300] if error else None,
            "depth": depth,
            "outward": outward,
            "node": node_id,
            "waits": waits,
            "until_reply": until_reply,
            "resume_at": resume_at,
        },
    )
    return UUID(str(row.scalar_one()))


async def update_run(
    session: AsyncSession,
    run_id: UUID,
    *,
    state: str,
    result: str,
    steps: Sequence[Mapping[str, Any]],
    outward: bool,
    error: str | None = None,
    node_id: str | None = None,
    waits: int = 0,
    until_reply: bool = False,
    resume_at: datetime | None = None,
) -> None:
    """A resumed run moved on: waiting again, or finished."""
    await session.execute(
        text(
            "UPDATE hd_automation_run SET state = :state, result = :result, "
            "steps = CAST(:steps AS jsonb), outward = outward OR :outward, error = :error, "
            "node_id = :node, waits = :waits, until_reply = :until_reply, replied = false, "
            "resume_at = :resume_at, updated_at = now(), "
            "finished_at = CASE WHEN :state = 'waiting' THEN NULL ELSE now() END WHERE id = :id"
        ),
        {
            "id": run_id,
            "state": state,
            "result": result[:300],
            "steps": json.dumps(list(steps)),
            "outward": outward,
            "error": error[:300] if error else None,
            "node": node_id,
            "waits": waits,
            "until_reply": until_reply,
            "resume_at": resume_at,
        },
    )


async def recent_runs(
    session: AsyncSession, *, rule_id: UUID | None, flow_id: UUID | None, since: datetime
) -> int:
    """Runs of one rule or flow since a time that did something (not blocked ones)."""
    column = "rule_id" if rule_id is not None else "flow_id"
    result = await session.execute(
        text(
            f"SELECT count(*) FROM hd_automation_run WHERE {column} = :id "  # noqa: S608
            "AND created_at >= :since AND state <> 'blocked'"
        ),
        {"id": rule_id if rule_id is not None else flow_id, "since": since},
    )
    return int(result.scalar_one())


async def recent_on_ticket(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    rule_id: UUID | None,
    flow_id: UUID | None,
    since: datetime,
) -> int:
    column = "rule_id" if rule_id is not None else "flow_id"
    result = await session.execute(
        text(
            f"SELECT count(*) FROM hd_automation_run WHERE ticket_id = :t AND {column} = :id "  # noqa: S608
            "AND created_at >= :since AND state <> 'blocked'"
        ),
        {"t": ticket_id, "id": rule_id if rule_id is not None else flow_id, "since": since},
    )
    return int(result.scalar_one())


async def chain_depth(session: AsyncSession, ticket_id: UUID, since: datetime) -> int:
    """The deepest recent run on the ticket that emailed the customer or snoozed it (what
    could have caused this event); 0 when none."""
    result = await session.execute(
        text(
            "SELECT coalesce(max(depth), 0) FROM hd_automation_run WHERE ticket_id = :t "
            "AND outward AND updated_at >= :since"
        ),
        {"t": ticket_id, "since": since},
    )
    return int(result.scalar_one())


async def list_runs(
    session: AsyncSession,
    *,
    rule_id: UUID | None,
    flow_id: UUID | None,
    ticket_id: UUID | None,
    state: str | None,
    trigger: str | None,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[Row]:
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            _RUN + "WHERE (CAST(:rule AS uuid) IS NULL OR r.rule_id = CAST(:rule AS uuid)) "
            "AND (CAST(:flow AS uuid) IS NULL OR r.flow_id = CAST(:flow AS uuid)) "
            "AND (CAST(:ticket AS uuid) IS NULL OR r.ticket_id = CAST(:ticket AS uuid)) "
            "AND (CAST(:state AS text) IS NULL OR r.state = CAST(:state AS text)) "
            "AND (CAST(:trigger AS text) IS NULL OR r.trigger = CAST(:trigger AS text)) "
            "AND (CAST(:at AS timestamptz) IS NULL OR (r.created_at, r.id) < "
            "(CAST(:at AS timestamptz), CAST(:rid AS uuid))) "
            "ORDER BY r.created_at DESC, r.id DESC LIMIT :limit"
        ),
        {
            "rule": rule_id,
            "flow": flow_id,
            "ticket": ticket_id,
            "state": state,
            "trigger": trigger,
            "at": at,
            "rid": row_id,
            "limit": limit,
        },
    )
    return result.mappings().all()


async def get_run(session: AsyncSession, run_id: UUID, *, lock: bool = False) -> Row | None:
    result = await session.execute(
        text(_RUN + "WHERE r.id = :id" + (" FOR UPDATE OF r" if lock else "")), {"id": run_id}
    )
    return result.mappings().first()


async def due_runs(session: AsyncSession, now: datetime, limit: int) -> list[UUID]:
    result = await session.execute(
        text(
            "SELECT id FROM hd_automation_run WHERE state = 'waiting' AND resume_at <= :now "
            "ORDER BY resume_at, id LIMIT :limit"
        ),
        {"now": now, "limit": limit},
    )
    return [UUID(str(r[0])) for r in result.all()]


async def claim_due(session: AsyncSession, run_id: UUID, now: datetime) -> Row | None:
    """The run, locked, if it still waits and is due; None when another worker holds it or
    already moved it on (so it is never resumed twice)."""
    result = await session.execute(
        text(
            "SELECT id, flow_id, flow_version, name, ticket_id, trigger, steps, depth, node_id, "
            "waits, until_reply, replied, resume_at FROM hd_automation_run "
            "WHERE id = :id AND state = 'waiting' AND resume_at <= :now "
            "FOR UPDATE SKIP LOCKED"
        ),
        {"id": run_id, "now": now},
    )
    return result.mappings().first()


async def customer_replied(session: AsyncSession, ticket_id: UUID, now: datetime) -> int:
    """Waits until the customer replies, on this ticket, are due now (by `replied`)."""
    result = await session.execute(
        text(
            "UPDATE hd_automation_run SET replied = true, resume_at = :now, updated_at = now() "
            "WHERE ticket_id = :t AND state = 'waiting' AND until_reply AND NOT replied "
            "RETURNING id"
        ),
        {"t": ticket_id, "now": now},
    )
    return len(result.all())


async def cancel_waiting(
    session: AsyncSession,
    *,
    reason: str,
    run_id: UUID | None = None,
    flow_id: UUID | None = None,
    ticket_ids: Sequence[UUID] | None = None,
    state: str = "cancelled",
) -> int:
    """Waiting runs stop (cancelled, or failed): one, all of a flow, or all on some
    tickets."""
    if state not in ("cancelled", "failed"):
        raise ValueError(state)
    result = await session.execute(
        text(
            "UPDATE hd_automation_run SET state = :state, result = :reason, "
            "resume_at = NULL, node_id = NULL, updated_at = now(), finished_at = now() "
            "WHERE state = 'waiting' "
            "AND (CAST(:run AS uuid) IS NULL OR id = CAST(:run AS uuid)) "
            "AND (CAST(:flow AS uuid) IS NULL OR flow_id = CAST(:flow AS uuid)) "
            "AND (CAST(:tickets AS uuid[]) IS NULL OR ticket_id = ANY(CAST(:tickets AS uuid[]))) "
            "RETURNING id"
        ),
        {
            "reason": reason[:300],
            "run": run_id,
            "flow": flow_id,
            "state": state,
            "tickets": list(ticket_ids) if ticket_ids is not None else None,
        },
    )
    return len(result.all())


async def runs_for_tickets(session: AsyncSession, ticket_ids: Sequence[UUID]) -> Sequence[Row]:
    """For a customer's data export: what automation did on their tickets."""
    result = await session.execute(
        text(
            "SELECT r.ticket_id, t.number AS ticket_number, r.name, r.trigger, r.state, "
            "r.result, r.created_at, r.finished_at FROM hd_automation_run r "
            "JOIN hd_ticket t ON t.id = r.ticket_id WHERE r.ticket_id = ANY(:ids) "
            "ORDER BY r.created_at, r.id"
        ),
        {"ids": list(ticket_ids)},
    )
    return result.mappings().all()


async def delete_for_tickets(session: AsyncSession, ticket_ids: Sequence[UUID]) -> int:
    result = await session.execute(
        text("DELETE FROM hd_automation_run WHERE ticket_id = ANY(:ids) RETURNING id"),
        {"ids": list(ticket_ids)},
    )
    return len(result.all())


async def prune(session: AsyncSession, days: int) -> int:
    """Finished runs older than `days`; waiting ones stay."""
    result = await session.execute(
        text(
            "DELETE FROM hd_automation_run WHERE state <> 'waiting' "
            "AND coalesce(finished_at, updated_at) < now() - make_interval(days => :d) "
            "RETURNING id"
        ),
        {"d": days},
    )
    return len(result.all())


# Flows

_FLOW = (
    "SELECT f.id, f.name, f.enabled, f.trigger, f.version, f.graph, f.max_runs_per_hour, "
    "f.max_runs_per_day, f.created_by, m.name AS created_by_name, f.created_at, f.updated_at, "
    "(SELECT count(*) FROM hd_automation_run r WHERE r.flow_id = f.id "
    "AND r.state = 'waiting') AS waiting_runs "
    "FROM hd_flow f LEFT JOIN member m ON m.id = f.created_by "
)


async def all_flows(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_FLOW + "ORDER BY lower(f.name), f.id"))
    return result.mappings().all()


async def get_flow(session: AsyncSession, flow_id: UUID, *, lock: bool = False) -> Row | None:
    result = await session.execute(
        text(_FLOW + "WHERE f.id = :id" + (" FOR UPDATE OF f" if lock else "")), {"id": flow_id}
    )
    return result.mappings().first()


async def count_flows(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_flow"))
    return int(result.scalar_one())


async def enabled_flows(session: AsyncSession, trigger: str) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT id, name, version, graph, max_runs_per_hour, max_runs_per_day FROM hd_flow "
            "WHERE enabled AND trigger = :t ORDER BY lower(name), id"
        ),
        {"t": trigger},
    )
    return result.mappings().all()


async def insert_flow(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    name: str,
    enabled: bool,
    trigger: str,
    graph: Mapping[str, Any],
    max_runs_per_hour: int,
    max_runs_per_day: int,
    created_by: UUID | None,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_flow (workspace_id, name, enabled, trigger, graph, max_runs_per_hour, "
            "max_runs_per_day, created_by) VALUES (:ws, :name, :enabled, :trigger, "
            "CAST(:graph AS jsonb), :hour, :day, :by) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "name": name,
            "enabled": enabled,
            "trigger": trigger,
            "graph": json.dumps(dict(graph)),
            "hour": max_runs_per_hour,
            "day": max_runs_per_day,
            "by": created_by,
        },
    )
    flow_id = UUID(str(result.scalar_one()))
    await add_version(session, workspace_id, flow_id, 1, graph, created_by)
    return flow_id


async def add_version(
    session: AsyncSession,
    workspace_id: UUID,
    flow_id: UUID,
    version: int,
    graph: Mapping[str, Any],
    created_by: UUID | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_flow_version (workspace_id, flow_id, version, graph, created_by) "
            "VALUES (:ws, :flow, :v, CAST(:graph AS jsonb), :by)"
        ),
        {
            "ws": workspace_id,
            "flow": flow_id,
            "v": version,
            "graph": json.dumps(dict(graph)),
            "by": created_by,
        },
    )


async def update_flow(session: AsyncSession, flow_id: UUID, values: Mapping[str, Any]) -> None:
    """`values`: any of name, enabled, trigger, graph, version, max_runs_per_hour,
    max_runs_per_day (fixed column names only)."""
    allowed = (
        "name", "enabled", "trigger", "graph", "version", "max_runs_per_hour", "max_runs_per_day"
    )  # fmt: skip
    sets = []
    params: dict[str, Any] = {"id": flow_id}
    for key in allowed:
        if key in values:
            value = values[key]
            if key == "graph":
                sets.append("graph = CAST(:graph AS jsonb)")
                value = json.dumps(dict(value))
            else:
                sets.append(f"{key} = :{key}")
            params[key] = value
    if not sets:
        return
    await session.execute(
        text(f"UPDATE hd_flow SET {', '.join(sets)}, updated_at = now() WHERE id = :id"),  # noqa: S608
        params,
    )


async def delete_flow(session: AsyncSession, flow_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_flow WHERE id = :id RETURNING id"), {"id": flow_id}
    )
    return result.first() is not None


async def versions(session: AsyncSession, flow_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT v.version, v.graph, v.created_at, m.name AS created_by_name "
            "FROM hd_flow_version v LEFT JOIN member m ON m.id = v.created_by "
            "WHERE v.flow_id = :id ORDER BY v.version DESC"
        ),
        {"id": flow_id},
    )
    return result.mappings().all()


async def version_graph(session: AsyncSession, flow_id: UUID, version: int) -> Any:
    result = await session.execute(
        text("SELECT graph FROM hd_flow_version WHERE flow_id = :id AND version = :v"),
        {"id": flow_id, "v": version},
    )
    return result.scalar_one_or_none()


async def flow_state(session: AsyncSession, flow_id: UUID) -> Row | None:
    """For a resumed run: is the flow still there and on."""
    result = await session.execute(
        text("SELECT enabled, name FROM hd_flow WHERE id = :id"), {"id": flow_id}
    )
    return result.mappings().first()
