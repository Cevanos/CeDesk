"""SQL for API keys (md PL1). RLS applies."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo.auth import ROLE_COLUMNS, ROLE_JOIN

_COLUMNS = (
    "k.id, k.name, k.access, k.last4, k.created_at, k.last_used_at, k.revoked_at, "
    "k.created_by, cm.name AS created_by_name, k.revoked_by, rm.name AS revoked_by_name"
)
_FROM = (
    "FROM hd_api_key k JOIN member cm ON cm.id = k.created_by "
    "LEFT JOIN member rm ON rm.id = k.revoked_by "
)


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    name: str,
    access: str,
    key_hash: bytes,
    last4: str,
    created_by: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_api_key (workspace_id, name, access, key_hash, last4, created_by) "
            "VALUES (:ws, :name, :access, :hash, :last4, :by) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "name": name,
            "access": access,
            "hash": key_hash,
            "last4": last4,
            "by": created_by,
        },
    )
    return UUID(str(result.scalar_one()))


async def get(session: AsyncSession, key_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(f"SELECT {_COLUMNS} {_FROM} WHERE k.id = :id"),
        {"id": key_id},
    )
    return result.mappings().first()


async def all_keys(session: AsyncSession) -> Sequence[RowMapping]:
    """Live keys first, newest first; then revoked ones, last revoked first."""
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} {_FROM} "
            "ORDER BY k.revoked_at IS NOT NULL, k.revoked_at DESC, k.created_at DESC, k.id"
        )
    )
    return result.mappings().all()


async def revoke(session: AsyncSession, key_id: UUID, by: UUID) -> bool:
    """False when there is no such key or it was revoked already."""
    result = await session.execute(
        text(
            "UPDATE hd_api_key SET revoked_at = now(), revoked_by = :by "
            "WHERE id = :id AND revoked_at IS NULL RETURNING id"
        ),
        {"id": key_id, "by": by},
    )
    return result.first() is not None


async def live_key(session: AsyncSession, key_hash: bytes) -> RowMapping | None:
    """The key and the member it acts as, only while the key isn't revoked and the
    member is active; with the workspace's IP list (S4)."""
    result = await session.execute(
        text(
            "SELECT k.id AS key_id, k.name AS key_name, k.access, k.last_used_at, "
            "m.id AS member_id, m.role, "
            + ROLE_COLUMNS
            + ", CAST(ss.ip_allowlist AS text[]) AS ip_allowlist "
            "FROM hd_api_key k JOIN member m ON m.id = k.created_by "
            + ROLE_JOIN
            + "LEFT JOIN hd_security_settings ss ON ss.workspace_id = k.workspace_id "
            "WHERE k.key_hash = :hash AND k.revoked_at IS NULL AND m.is_active"
        ),
        {"hash": key_hash},
    )
    return result.mappings().first()


async def touch(session: AsyncSession, key_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_api_key SET last_used_at = now() WHERE id = :id"), {"id": key_id}
    )
