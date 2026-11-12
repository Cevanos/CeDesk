"""SQL for live updates (md C11, P7). Always inside a `workspace_session`.

Writers (`publish_*`) run in the transaction of the change they describe, so an event exists
exactly when its change does. A write never fails the change: it is one INSERT of ids.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain import stream as rules
from cedesk.helpdesk.repo.auth import ROLE_COLUMNS, ROLE_JOIN

Row = RowMapping

# Who may read a row: everyone with access to its ticket (no target), or the one it names.
_AGENT_AUDIENCE = (
    "((target_member_id IS NULL AND target_contact_id IS NULL) "
    "OR target_member_id = CAST(:member AS uuid))"
)
# A role limited to one team's tickets: only that team's. Not limited: an event with no
# ticket, and one written for this member alone (their own notification), which names a
# ticket they were told about.
_TEAM_ONLY = (
    "(ticket_id IS NULL OR team_id = CAST(:team AS uuid) "
    "OR target_member_id = CAST(:member AS uuid))"
)
_COLUMNS = "seq, kind, ticket_id, team_id, payload, created_at"


# Writing


async def publish_for_ticket(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    agent: tuple[str, Mapping[str, Any]] | None,
    customer: tuple[str, Mapping[str, Any]] | None = None,
) -> None:
    """The event for a ticket's agents and, when it has one, for the ticket's customer. The
    workspace, team and customer are read from the ticket row, in the same statement."""
    if agent is not None:
        await _insert_for_ticket(session, ticket_id, agent[0], agent[1], to_customer=False)
    if customer is not None:
        await _insert_for_ticket(session, ticket_id, customer[0], customer[1], to_customer=True)


async def _insert_for_ticket(
    session: AsyncSession,
    ticket_id: UUID,
    kind: str,
    payload: Mapping[str, Any],
    *,
    to_customer: bool,
) -> None:
    if kind not in (rules.CUSTOMER_KINDS if to_customer else rules.AGENT_KINDS):
        raise ValueError(f"Not a published kind: {kind}")
    await session.execute(
        text(
            "INSERT INTO hd_event_log (workspace_id, kind, ticket_id, team_id, "
            "target_contact_id, payload) "
            "SELECT t.workspace_id, :kind, t.id, t.team_id, "
            "CASE WHEN CAST(:customer AS boolean) THEN t.contact_id END, CAST(:payload AS jsonb) "
            "FROM hd_ticket t WHERE t.id = :ticket"
        ),
        {
            "kind": kind,
            "ticket": ticket_id,
            "customer": to_customer,
            "payload": json.dumps(dict(payload)),
        },
    )


async def publish_notifications(
    session: AsyncSession,
    workspace_id: UUID,
    member_ids: Sequence[UUID],
    ticket_id: UUID,
    kind: str,
) -> None:
    """A `notification` event for each member who was notified (what kind, which ticket)."""
    if not member_ids:
        return
    await session.execute(
        text(
            "INSERT INTO hd_event_log (workspace_id, kind, ticket_id, target_member_id, payload) "
            "SELECT :ws, 'notification', CAST(:ticket AS uuid), m, "
            "jsonb_build_object('ticket_id', CAST(:ticket AS text), "
            "'notification', CAST(:kind AS text)) "
            "FROM unnest(CAST(:members AS uuid[])) AS m"
        ),
        {"ws": workspace_id, "ticket": ticket_id, "kind": kind, "members": list(member_ids)},
    )


# Reading (an open stream)


async def newest(session: AsyncSession, workspace_id: UUID) -> int:
    """The newest event number of the workspace (0 when none): where a new stream starts."""
    result = await session.execute(
        text("SELECT coalesce(max(seq), 0) FROM hd_event_log WHERE workspace_id = :ws"),
        {"ws": workspace_id},
    )
    return int(result.scalar_one())


async def exists(session: AsyncSession, workspace_id: UUID, seq: int) -> bool:
    """Whether event `seq` is still kept. A Last-Event-ID that is gone means events between
    it and now may have been pruned: the client must refetch (a `reset`)."""
    result = await session.execute(
        text("SELECT 1 FROM hd_event_log WHERE workspace_id = :ws AND seq = :seq"),
        {"ws": workspace_id, "seq": seq},
    )
    return result.first() is not None


async def read_agent(
    session: AsyncSession,
    workspace_id: UUID,
    member_id: UUID,
    team_only: UUID | None,
    *,
    after: int,
    floor: int,
    limit: int,
    overlap: bool,
) -> Sequence[Row]:
    """Events for this member. `overlap` reads, instead of what is new, the last 30 seconds
    of events above `floor` and at or before `after`: an event that committed after a later
    one was sent. `floor` is where this connection began, so nothing older is ever sent."""
    where = _AGENT_AUDIENCE + (f" AND {_TEAM_ONLY}" if team_only is not None else "")
    return await _read(
        session,
        workspace_id,
        where,
        {"member": member_id, "team": team_only},
        after=after,
        floor=floor,
        limit=limit,
        overlap=overlap,
    )


async def read_customer(
    session: AsyncSession,
    workspace_id: UUID,
    contact_id: UUID,
    *,
    after: int,
    floor: int,
    limit: int,
    overlap: bool,
) -> Sequence[Row]:
    return await _read(
        session,
        workspace_id,
        "target_contact_id = CAST(:contact AS uuid)",
        {"contact": contact_id},
        after=after,
        floor=floor,
        limit=limit,
        overlap=overlap,
    )


async def _read(
    session: AsyncSession,
    workspace_id: UUID,
    audience: str,
    params: Mapping[str, Any],
    *,
    after: int,
    floor: int,
    limit: int,
    overlap: bool,
) -> Sequence[Row]:
    if overlap:
        window = (
            "seq <= :after AND seq > :low AND created_at > now() - make_interval(secs => :overlap)"
        )
    else:
        window = "seq > :after"
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM hd_event_log "  # noqa: S608 - fixed fragments only
            f"WHERE workspace_id = :ws AND {window} AND {audience} "
            "ORDER BY seq LIMIT :limit"
        ),
        {
            **params,
            "ws": workspace_id,
            "after": after,
            "low": max(floor, after - rules.OVERLAP_SEQS),
            "overlap": rules.OVERLAP.total_seconds(),
            "limit": limit,
        },
    )
    return result.mappings().all()


async def prune(session: AsyncSession, older_than: datetime) -> int:
    """Removes events (and spent or expired stream tickets) older than `older_than`."""
    events = await session.execute(
        text("DELETE FROM hd_event_log WHERE created_at < :t"), {"t": older_than}
    )
    tickets = await session.execute(
        text("DELETE FROM hd_stream_ticket WHERE expires_at < :t"), {"t": older_than}
    )
    return int(events.rowcount) + int(tickets.rowcount)  # type: ignore[attr-defined]


# Stream tickets


async def issue_ticket(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    token_hash: bytes,
    audience: str,
    session_id: UUID,
    member_id: UUID | None,
    contact_id: UUID | None,
    expires_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_stream_ticket (workspace_id, token_hash, audience, session_id, "
            "member_id, contact_id, expires_at) VALUES (:ws, :hash, :audience, :session, "
            ":member, :contact, :expires)"
        ),
        {
            "ws": workspace_id,
            "hash": token_hash,
            "audience": audience,
            "session": session_id,
            "member": member_id,
            "contact": contact_id,
            "expires": expires_at,
        },
    )


async def consume_ticket(session: AsyncSession, token_hash: bytes, audience: str) -> Row | None:
    """Uses the ticket: it is unexpired and unused, and now it is used. One statement, so two
    requests with the same ticket can never both get it."""
    result = await session.execute(
        text(
            "UPDATE hd_stream_ticket SET used_at = now() "
            "WHERE token_hash = :hash AND audience = :audience AND used_at IS NULL "
            "AND expires_at > now() RETURNING session_id, member_id, contact_id"
        ),
        {"hash": token_hash, "audience": audience},
    )
    return result.mappings().first()


# The sign-in behind a stream, checked again while it is open


async def live_agent(session: AsyncSession, session_id: UUID) -> Row | None:
    """The member of a session, only while it is unexpired, not revoked and past the 2FA
    step, and the member is active. Carries what `member_access` needs."""
    result = await session.execute(
        text(
            "SELECT m.id AS member_id, m.role, "  # noqa: S608
            + ROLE_COLUMNS
            + " FROM auth_session s JOIN member m ON m.id = s.member_id "
            + ROLE_JOIN
            + "WHERE s.id = :id AND s.revoked_at IS NULL AND s.expires_at > now() "
            "AND NOT s.second_factor_pending AND m.is_active"
        ),
        {"id": session_id},
    )
    return result.mappings().first()


async def live_customer(session: AsyncSession, session_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT s.contact_id FROM hd_portal_session s "
            "WHERE s.id = :id AND s.revoked_at IS NULL AND s.expires_at > now()"
        ),
        {"id": session_id},
    )
    return result.mappings().first()
