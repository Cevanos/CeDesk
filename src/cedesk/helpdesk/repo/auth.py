"""SQL for workspaces, members and sign-in sessions.

Every function except `workspace_for_slug` and `add_directory_entry` runs inside a
`workspace_session`, so RLS limits it to that workspace.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping


async def workspace_for_slug(session: AsyncSession, slug: str) -> UUID | None:
    result = await session.execute(
        text("SELECT workspace_id FROM workspace_directory WHERE slug = :slug"), {"slug": slug}
    )
    value = result.scalar_one_or_none()
    return value if value is None else UUID(str(value))


async def insert_workspace(session: AsyncSession, workspace_id: UUID, name: str) -> None:
    await session.execute(
        text("INSERT INTO workspace (id, name) VALUES (:id, :name)"),
        {"id": workspace_id, "name": name},
    )


async def add_directory_entry(session: AsyncSession, slug: str, workspace_id: UUID) -> None:
    await session.execute(
        text("INSERT INTO workspace_directory (slug, workspace_id) VALUES (:slug, :id)"),
        {"slug": slug, "id": workspace_id},
    )


async def workspace_row(session: AsyncSession) -> Row | None:
    result = await session.execute(
        text(
            "SELECT w.id, w.name, d.slug FROM workspace w "
            "LEFT JOIN workspace_directory d ON d.workspace_id = w.id"
        )
    )
    return result.mappings().first()


async def insert_member(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    email: str,
    name: str,
    role: str,
    password_hash: str,
) -> Row:
    result = await session.execute(
        text(
            "INSERT INTO member (workspace_id, email, name, role, password_hash) "
            "VALUES (:ws, :email, :name, :role, :hash) "
            "RETURNING id, workspace_id, email, name, role, is_active, created_at, updated_at"
        ),
        {"ws": workspace_id, "email": email, "name": name, "role": role, "hash": password_hash},
    )
    return result.mappings().one()


async def member_for_login(session: AsyncSession, email: str) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, role, password_hash, is_active, failed_logins, locked_until "
            "FROM member WHERE email = :email FOR UPDATE"
        ),
        {"email": email},
    )
    return result.mappings().first()


async def record_login_failure(
    session: AsyncSession, member_id: UUID, failed_logins: int, locked_until: datetime | None
) -> None:
    await session.execute(
        text(
            "UPDATE member SET failed_logins = :failed, "
            "locked_until = COALESCE(:locked, locked_until) WHERE id = :id"
        ),
        {"id": member_id, "failed": failed_logins, "locked": locked_until},
    )


async def record_login_success(
    session: AsyncSession, member_id: UUID, new_hash: str | None
) -> None:
    await session.execute(
        text(
            "UPDATE member SET failed_logins = 0, locked_until = NULL, "
            "password_hash = COALESCE(:hash, password_hash) WHERE id = :id"
        ),
        {"id": member_id, "hash": new_hash},
    )


async def insert_session(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    member_id: UUID,
    token_hash: bytes,
    expires_at: datetime,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO auth_session (workspace_id, member_id, token_hash, expires_at) "
            "VALUES (:ws, :member, :hash, :expires) RETURNING id"
        ),
        {"ws": workspace_id, "member": member_id, "hash": token_hash, "expires": expires_at},
    )
    return UUID(str(result.scalar_one()))


async def live_session(session: AsyncSession, token_hash: bytes) -> Row | None:
    """The session and its member, only if unexpired, not revoked and the member is active."""
    result = await session.execute(
        text(
            "SELECT s.id AS session_id, m.id AS member_id, m.role "
            "FROM auth_session s JOIN member m ON m.id = s.member_id "
            "WHERE s.token_hash = :hash AND s.revoked_at IS NULL "
            "AND s.expires_at > now() AND m.is_active"
        ),
        {"hash": token_hash},
    )
    return result.mappings().first()


async def revoke_session(session: AsyncSession, session_id: UUID) -> None:
    await session.execute(
        text("UPDATE auth_session SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"),
        {"id": session_id},
    )


async def revoke_member_sessions(session: AsyncSession, member_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE auth_session SET revoked_at = now() "
            "WHERE member_id = :id AND revoked_at IS NULL"
        ),
        {"id": member_id},
    )


async def list_members(session: AsyncSession, after: UUID | None, limit: int) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT id, workspace_id, email, name, role, is_active, created_at, updated_at "
            "FROM member "
            "WHERE (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid)) "
            "ORDER BY id LIMIT :limit"
        ),
        {"after": after, "limit": limit},
    )
    return result.mappings().all()


async def get_member(session: AsyncSession, member_id: UUID, *, lock: bool = False) -> Row | None:
    sql = (
        "SELECT id, workspace_id, email, name, role, is_active, created_at, updated_at "
        "FROM member WHERE id = :id"
    )
    result = await session.execute(text(sql + " FOR UPDATE" if lock else sql), {"id": member_id})
    return result.mappings().first()


async def lock_active_admins(session: AsyncSession) -> int:
    """Locks the active admins and counts them, so two concurrent demotions can't both pass."""
    result = await session.execute(
        text("SELECT id FROM member WHERE role = 'admin' AND is_active ORDER BY id FOR UPDATE")
    )
    return len(result.all())


async def update_member(
    session: AsyncSession,
    member_id: UUID,
    *,
    name: str | None,
    role: str | None,
    is_active: bool | None,
) -> Row:
    result = await session.execute(
        text(
            "UPDATE member SET name = COALESCE(:name, name), role = COALESCE(:role, role), "
            "is_active = COALESCE(:active, is_active), updated_at = now() "
            "WHERE id = :id "
            "RETURNING id, workspace_id, email, name, role, is_active, created_at, updated_at"
        ),
        {"id": member_id, "name": name, "role": role, "active": is_active},
    )
    return result.mappings().one()
