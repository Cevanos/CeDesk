"""SQL for push devices and banners (md P8). RLS applies: only the session's workspace."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

_DEVICE = "SELECT id, platform, token, created_at, last_seen_at FROM hd_push_device "


async def register(
    session: AsyncSession, workspace_id: UUID, member_id: UUID, platform: str, token: str
) -> UUID:
    """The same token again is the same phone: it now belongs to whoever registered it
    last (someone else signed in on it)."""
    result = await session.execute(
        text(
            "INSERT INTO hd_push_device (workspace_id, member_id, platform, token) "
            "VALUES (:ws, :member, :platform, :token) ON CONFLICT (workspace_id, token) DO "
            "UPDATE SET member_id = EXCLUDED.member_id, platform = EXCLUDED.platform, "
            "last_seen_at = now() RETURNING id"
        ),
        {"ws": workspace_id, "member": member_id, "platform": platform, "token": token},
    )
    return UUID(str(result.scalar_one()))


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


_CLAIM = """
WITH picked AS (
    SELECT id FROM hd_notification WHERE push_handled_at IS NULL
    ORDER BY created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED
)
UPDATE hd_notification n SET push_handled_at = now() FROM picked p WHERE n.id = p.id
RETURNING n.id, n.member_id, n.ticket_id, n.kind, n.created_at
"""


async def claim(session: AsyncSession, limit: int) -> Sequence[RowMapping]:
    """Notifications not looked at yet, marked handled in this transaction (a banner is
    sent at most once). Rows another worker holds are skipped."""
    result = await session.execute(text(_CLAIM), {"limit": limit})
    return result.mappings().all()


async def targets(
    session: AsyncSession, member_ids: Sequence[UUID], ticket_ids: Sequence[UUID]
) -> tuple[dict[UUID, list[RowMapping]], dict[UUID, int]]:
    """The active members' devices, and the tickets' numbers."""
    devices = await session.execute(
        text(
            "SELECT d.member_id, d.token FROM hd_push_device d JOIN member m ON m.id = "
            "d.member_id WHERE d.member_id = ANY(CAST(:ids AS uuid[])) AND m.is_active"
        ),
        {"ids": list(member_ids)},
    )
    by_member: dict[UUID, list[RowMapping]] = {}
    for d in devices.mappings().all():
        by_member.setdefault(d["member_id"], []).append(d)
    numbers = await session.execute(
        text("SELECT id, number FROM hd_ticket WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": list(ticket_ids)},
    )
    return by_member, {r["id"]: int(r["number"]) for r in numbers.mappings().all()}
