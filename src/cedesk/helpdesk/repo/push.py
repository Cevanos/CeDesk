"""SQL for push devices and banners (md P8). RLS applies: only the session's workspace."""

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo.auth import TAKES_TICKETS

_DEVICE = "SELECT id, platform, token, created_at, last_seen_at FROM hd_push_device "
# The sign-in that registered a device is still live (not signed out, not expired).
LIVE_SESSION = (
    "EXISTS (SELECT 1 FROM auth_session a WHERE a.id = {d}.session_id "
    "AND a.revoked_at IS NULL AND a.expires_at > now())"
)


async def register(
    session: AsyncSession,
    workspace_id: UUID,
    member_id: UUID,
    session_id: UUID,
    platform: str,
    token: str,
) -> UUID | None:
    """The same token again is the same phone: refreshed for the same person (now under
    this sign-in). Another person's phone is taken over only when the sign-in that
    registered it has ended; else None (the caller answers 409)."""
    result = await session.execute(
        text(
            "INSERT INTO hd_push_device (workspace_id, member_id, session_id, platform, token) "
            "VALUES (:ws, :member, :session, :platform, :token) "
            "ON CONFLICT (workspace_id, token) DO UPDATE SET member_id = EXCLUDED.member_id, "
            "session_id = EXCLUDED.session_id, platform = EXCLUDED.platform, "
            "last_seen_at = now() WHERE hd_push_device.member_id = EXCLUDED.member_id "
            "OR NOT " + LIVE_SESSION.format(d="hd_push_device") + " RETURNING id"
        ),
        {
            "ws": workspace_id,
            "member": member_id,
            "session": session_id,
            "platform": platform,
            "token": token,
        },
    )
    found = result.scalar_one_or_none()
    return None if found is None else UUID(str(found))


async def keep_newest(session: AsyncSession, member_id: UUID, keep: int) -> None:
    await session.execute(
        text(
            "DELETE FROM hd_push_device WHERE member_id = :member AND id NOT IN ("
            "SELECT id FROM hd_push_device WHERE member_id = :member "
            "ORDER BY last_seen_at DESC, id LIMIT :keep)"
        ),
        {"member": member_id, "keep": keep},
    )


async def devices_of(session: AsyncSession, member_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(_DEVICE + "WHERE member_id = :member ORDER BY last_seen_at DESC, id"),
        {"member": member_id},
    )
    return result.mappings().all()


async def remove(session: AsyncSession, member_id: UUID, device_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_push_device WHERE id = :id AND member_id = :member RETURNING id"),
        {"id": device_id, "member": member_id},
    )
    return result.first() is not None


async def remove_tokens(session: AsyncSession, tokens: Sequence[str]) -> None:
    """Tokens the push service says are gone (the app was removed)."""
    await session.execute(
        text("DELETE FROM hd_push_device WHERE token = ANY(CAST(:tokens AS text[]))"),
        {"tokens": list(tokens)},
    )


# Claim with a lease: what a worker sends (or waits to retry) is skipped by the others
# until `push_claimed_until`. A worker that dies mid-send leaves its banners for the next
# pass once the lease ends (they may then go twice; never lost).
_CLAIM = """
WITH picked AS (
    SELECT id FROM hd_notification WHERE push_handled_at IS NULL
      AND (push_claimed_until IS NULL OR push_claimed_until <= now())
    ORDER BY created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED
)
UPDATE hd_notification n SET push_claimed_until = now() + CAST(:lease AS interval)
FROM picked p WHERE n.id = p.id
RETURNING n.id, n.member_id, n.ticket_id, n.kind, n.created_at, n.push_attempts
"""


async def claim(
    session: AsyncSession, limit: int, lease: timedelta = timedelta(minutes=2)
) -> Sequence[RowMapping]:
    """Notifications to look at, held for this worker until the lease ends. Rows another
    worker holds (locked, or under its lease) are skipped."""
    result = await session.execute(text(_CLAIM), {"limit": limit, "lease": lease})
    return result.mappings().all()


async def handled(session: AsyncSession, ids: Sequence[UUID]) -> None:
    """Done for push: sent, given up, or nothing to send."""
    if ids:
        await session.execute(
            text(
                "UPDATE hd_notification SET push_handled_at = now(), push_claimed_until = NULL "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(ids)},
        )


async def release(session: AsyncSession, ids: Sequence[UUID]) -> None:
    """Not tried this pass (out of time, or the key was refused): the next pass sends."""
    if ids:
        await session.execute(
            text(
                "UPDATE hd_notification SET push_claimed_until = NULL "
                "WHERE id = ANY(CAST(:ids AS uuid[])) AND push_handled_at IS NULL"
            ),
            {"ids": list(ids)},
        )


async def failed(
    session: AsyncSession, ids: Sequence[UUID], retry_after: timedelta, max_attempts: int
) -> None:
    """A try failed (network, push service busy): retried after `retry_after`, given up
    (handled) at `max_attempts` tries."""
    if ids:
        await session.execute(
            text(
                "UPDATE hd_notification SET push_attempts = push_attempts + 1, "
                "push_claimed_until = now() + CAST(:after AS interval), "
                "push_handled_at = CASE WHEN push_attempts + 1 >= :max THEN now() END "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(ids), "after": retry_after, "max": max_attempts},
        )


async def targets(
    session: AsyncSession, member_ids: Sequence[UUID], ticket_ids: Sequence[UUID]
) -> tuple[dict[UUID, list[RowMapping]], dict[UUID, RowMapping]]:
    """The active members' devices whose sign-in is still live, and the tickets' numbers
    (and whether each is accepted)."""
    devices = await session.execute(
        text(
            "SELECT d.member_id, d.token FROM hd_push_device d JOIN member m ON m.id = "  # noqa: S608
            "d.member_id WHERE d.member_id = ANY(CAST(:ids AS uuid[])) AND m.is_active AND "
            + LIVE_SESSION.format(d="d")
        ),
        {"ids": list(member_ids)},
    )
    by_member: dict[UUID, list[RowMapping]] = {}
    for d in devices.mappings().all():
        by_member.setdefault(d["member_id"], []).append(d)
    tickets = await session.execute(
        text("SELECT id, number, accepted FROM hd_ticket WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": list(ticket_ids)},
    )
    return by_member, {r["id"]: r for r in tickets.mappings().all()}


async def handover_recipients(session: AsyncSession, ticket_id: UUID) -> list[UUID]:
    """Who hears of a chat waiting for Accept: the assignee, else the ticket's team's
    active agents who are Available and may work on the team's tickets."""
    result = await session.execute(
        text("SELECT assignee_id, team_id FROM hd_ticket WHERE id = :id"),
        {"id": ticket_id},
    )
    row = result.mappings().first()
    if row is None:
        return []
    if row["assignee_id"] is not None:
        return [row["assignee_id"]]
    if row["team_id"] is None:
        return []
    agents = await session.execute(
        text(
            "SELECT m.id FROM hd_team_member tm JOIN member m ON m.id = tm.member_id "  # noqa: S608
            "JOIN hd_agent_state s ON s.member_id = m.id "
            "WHERE tm.team_id = :team AND m.is_active AND s.status = 'available' AND "
            + TAKES_TICKETS
            + " AND (m.role <> 'custom' OR EXISTS (SELECT 1 FROM hd_role sr WHERE "
            "sr.id = m.role_id AND (sr.scope = 'all' OR sr.team_id = :team))) "
            "ORDER BY tm.position, m.id"
        ),
        {"team": row["team_id"]},
    )
    return [r["id"] for r in agents.mappings().all()]
