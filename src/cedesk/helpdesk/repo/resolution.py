"""Resolution receipts (differentiator plan #5): `hd_resolution` and the workspace's
`hd_resolution_setting`. Run inside a workspace session (RLS)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_RECEIPT = (
    "SELECT r.id, r.ticket_id, r.request, r.action_taken, r.result, r.verified, "
    "r.verified_at, r.customer_notified, r.pending_item, r.resolved_by, "
    "m.name AS resolved_by_name, r.created_at, r.updated_at "
    "FROM hd_resolution r LEFT JOIN member m ON m.id = r.resolved_by "
    "WHERE r.ticket_id = :t"
)


async def setting(session: AsyncSession) -> Row | None:
    result = await session.execute(
        text(
            "SELECT s.mode, s.required_teams, s.updated_at, m.name AS updated_by_name "
            "FROM hd_resolution_setting s LEFT JOIN member m ON m.id = s.updated_by"
        )
    )
    return result.mappings().first()


async def save_setting(
    session: AsyncSession, workspace_id: UUID, mode: str, teams: Sequence[UUID], by: UUID
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_resolution_setting (workspace_id, mode, required_teams, updated_by) "
            "VALUES (:ws, :mode, CAST(:teams AS uuid[]), :by) ON CONFLICT (workspace_id) DO "
            "UPDATE SET mode = EXCLUDED.mode, required_teams = EXCLUDED.required_teams, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"ws": workspace_id, "mode": mode, "teams": [str(t) for t in teams], "by": by},
    )


async def unknown_teams(session: AsyncSession, teams: Sequence[UUID]) -> list[UUID]:
    if not teams:
        return []
    result = await session.execute(
        text("SELECT id FROM hd_team WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(t) for t in teams]},
    )
    found = {UUID(str(r[0])) for r in result.all()}
    return [t for t in teams if t not in found]


async def get(session: AsyncSession, ticket_id: UUID) -> Row | None:
    result = await session.execute(text(_RECEIPT), {"t": ticket_id})
    return result.mappings().first()


async def last_public_reply(session: AsyncSession, ticket_id: UUID) -> datetime | None:
    """The last reply the customer could see (an agent's, the workspace's or the AI's)."""
    result = await session.execute(
        text(
            "SELECT max(created_at) FROM hd_message "
            "WHERE ticket_id = :t AND kind IN ('agent', 'ai')"
        ),
        {"t": ticket_id},
    )
    value = result.scalar_one_or_none()
    return value if isinstance(value, datetime) else None


async def save(
    session: AsyncSession, workspace_id: UUID, ticket_id: UUID, values: Mapping[str, Any]
) -> None:
    """Writes the ticket's receipt, replacing one from an earlier resolve."""
    await session.execute(
        text(
            "INSERT INTO hd_resolution (workspace_id, ticket_id, request, action_taken, result, "
            "verified, verified_at, customer_notified, pending_item, resolved_by) "
            "VALUES (:ws, :t, :request, :action_taken, :result, :verified, :verified_at, "
            ":customer_notified, :pending_item, :resolved_by) "
            "ON CONFLICT (workspace_id, ticket_id) DO UPDATE SET "
            "request = EXCLUDED.request, action_taken = EXCLUDED.action_taken, "
            "result = EXCLUDED.result, verified = EXCLUDED.verified, "
            "verified_at = EXCLUDED.verified_at, "
            "customer_notified = EXCLUDED.customer_notified, "
            "pending_item = EXCLUDED.pending_item, resolved_by = EXCLUDED.resolved_by, "
            "created_at = now(), updated_at = now()"
        ),
        {"ws": workspace_id, "t": ticket_id, **values},
    )


# Columns an edit may set (the keys come from domain.resolution.changes, never a request).
EDITABLE = ("request", "action_taken", "result", "verified", "verified_at",
            "customer_notified", "pending_item")  # fmt: skip


_UPDATE = (
    "UPDATE hd_resolution SET "
    "request = CASE WHEN :set_request THEN :request ELSE request END, "
    "action_taken = CASE WHEN :set_action_taken THEN :action_taken ELSE action_taken END, "
    "result = CASE WHEN :set_result THEN :result ELSE result END, "
    "verified = CASE WHEN :set_verified THEN :verified ELSE verified END, "
    "verified_at = CASE WHEN :set_verified_at THEN :verified_at ELSE verified_at END, "
    "customer_notified = CASE WHEN :set_customer_notified THEN :customer_notified "
    "ELSE customer_notified END, "
    "pending_item = CASE WHEN :set_pending_item THEN :pending_item ELSE pending_item END, "
    "updated_at = now() WHERE ticket_id = :t"
)


async def update(session: AsyncSession, ticket_id: UUID, values: Mapping[str, Any]) -> None:
    """Sets the EDITABLE keys present in `values`; the others keep their value."""
    if not any(k in values for k in EDITABLE):
        return
    params: dict[str, Any] = {"t": ticket_id}
    for k in EDITABLE:
        params[f"set_{k}"] = k in values
        params[k] = values.get(k)
    await session.execute(text(_UPDATE), params)


_REPORT = """
    SELECT t.team_id, t.assignee_id,
           count(*) AS resolved,
           count(r.id) AS with_receipt,
           count(r.id) FILTER (WHERE r.verified <> 'unverified') AS verified,
           count(r.id) FILTER (WHERE r.verified = 'agent') AS verified_agent,
           count(r.id) FILTER (WHERE r.verified = 'shopify') AS verified_shopify,
           count(r.id) FILTER (WHERE r.verified = 'stripe') AS verified_stripe,
           count(r.id) FILTER (WHERE r.pending_item IS NOT NULL) AS pending,
           count(r.id) FILTER (WHERE r.customer_notified) AS notified,
           GROUPING(t.team_id) AS g_team, GROUPING(t.assignee_id) AS g_agent
    FROM hd_ticket t
    LEFT JOIN hd_resolution r ON r.ticket_id = t.id
    WHERE t.status = 'resolved' AND t.merged_into_id IS NULL
      AND t.resolved_at >= :start AND t.resolved_at < :end
      AND (CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))
    GROUP BY GROUPING SETS ((), (t.team_id), (t.assignee_id))
"""


async def report(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None
) -> Sequence[Row]:
    result = await session.execute(text(_REPORT), {"start": start, "end": end, "team": team_id})
    return result.mappings().all()


async def names(session: AsyncSession, table: str, ids: Sequence[UUID]) -> dict[UUID, str]:
    """Team or member names for a report's rows (inactive members too)."""
    query = {
        "team": "SELECT id, name FROM hd_team WHERE id = ANY(CAST(:ids AS uuid[]))",
        "member": "SELECT id, name FROM member WHERE id = ANY(CAST(:ids AS uuid[]))",
    }[table]
    if not ids:
        return {}
    result = await session.execute(text(query), {"ids": [str(i) for i in ids]})
    return {UUID(str(r[0])): str(r[1]) for r in result.all()}
