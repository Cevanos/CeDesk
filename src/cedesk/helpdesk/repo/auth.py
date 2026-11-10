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


_LOGIN = (
    "SELECT id, role, password_hash, is_active, failed_logins, locked_until "
    "FROM member WHERE email = :email"
)


async def member_for_login(session: AsyncSession, email: str) -> Row | None:
    """Read without a lock: the slow password check runs before any row is locked."""
    result = await session.execute(text(_LOGIN), {"email": email})
    return result.mappings().first()


async def lock_member_for_login(session: AsyncSession, member_id: UUID) -> Row | None:
    """The same row, locked, only to save the result of a sign-in."""
    result = await session.execute(
        text(
            "SELECT id, role, password_hash, is_active, failed_logins, locked_until "
            "FROM member WHERE id = :id FOR UPDATE"
        ),
        {"id": member_id},
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


async def revoke_member_sessions(
    session: AsyncSession, member_id: UUID, *, keep: UUID | None = None
) -> None:
    """Ends every live session of the member, except `keep` when given."""
    await session.execute(
        text(
            "UPDATE auth_session SET revoked_at = now() "
            "WHERE member_id = :id AND revoked_at IS NULL "
            "AND (CAST(:keep AS uuid) IS NULL OR id <> CAST(:keep AS uuid))"
        ),
        {"id": member_id, "keep": keep},
    )


async def member_password_hash(
    session: AsyncSession, member_id: UUID, *, lock: bool = False
) -> str | None:
    sql = "SELECT password_hash FROM member WHERE id = :id"
    result = await session.execute(text(sql + " FOR UPDATE" if lock else sql), {"id": member_id})
    value = result.scalar_one_or_none()
    return None if value is None else str(value)


async def set_password(session: AsyncSession, member_id: UUID, password_hash: str) -> None:
    """A new password also clears failed sign-ins and any lockout."""
    await session.execute(
        text(
            "UPDATE member SET password_hash = :hash, failed_logins = 0, locked_until = NULL, "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": member_id, "hash": password_hash},
    )


async def prune_sessions(
    session: AsyncSession, ended_before: datetime, member_id: UUID | None = None
) -> int:
    """Deletes sessions that expired or were signed out before `ended_before`;
    only one member's when `member_id` is given. Returns how many."""
    result = await session.execute(
        text(
            "DELETE FROM auth_session "
            "WHERE (CAST(:member AS uuid) IS NULL OR member_id = CAST(:member AS uuid)) "
            "AND (expires_at < :before OR revoked_at < :before)"
        ),
        {"member": member_id, "before": ended_before},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def all_workspace_ids(session: AsyncSession) -> list[UUID]:
    """Every workspace, from the global slug directory (it has no RLS)."""
    result = await session.execute(text("SELECT workspace_id FROM workspace_directory"))
    return [UUID(str(v)) for v in result.scalars().all()]


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


async def lock_admins_and_member(
    session: AsyncSession, member_id: UUID
) -> tuple[set[UUID], Row | None]:
    """Locks every active Admin and the member being changed, in one id-ordered sweep.

    Returns the active Admin ids and the member's row (None if not in this workspace).
    Taking all these locks in one statement, in id order, means two member changes
    always lock rows in the same order, so they queue instead of deadlocking (B5);
    and two demotions can't both see a second Admin."""
    result = await session.execute(
        text(
            "SELECT id, workspace_id, email, name, role, is_active, created_at, updated_at "
            "FROM member WHERE (role = 'admin' AND is_active) OR id = :id "
            "ORDER BY id FOR UPDATE"
        ),
        {"id": member_id},
    )
    rows = result.mappings().all()
    admins = {UUID(str(r["id"])) for r in rows if r["role"] == "admin" and r["is_active"]}
    target = next((r for r in rows if r["id"] == member_id), None)
    return admins, target


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


async def member_email(session: AsyncSession, member_id: UUID) -> str | None:
    result = await session.execute(
        text("SELECT email FROM member WHERE id = :id"), {"id": member_id}
    )
    value = result.scalar_one_or_none()
    return None if value is None else str(value)


# Password reset links


async def active_member_for_email(session: AsyncSession, email: str) -> Row | None:
    result = await session.execute(
        text("SELECT id, name, email FROM member WHERE email = :e AND is_active"),
        {"e": email},
    )
    return result.mappings().first()


async def recent_resets(session: AsyncSession, member_id: UUID, since: datetime) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM password_reset WHERE member_id = :m AND created_at >= :since"),
        {"m": member_id, "since": since},
    )
    return int(result.scalar_one())


async def insert_reset(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    member_id: UUID,
    token_hash: bytes,
    expires_at: datetime,
) -> None:
    # Expired links of this member are tidied away at the same time.
    await session.execute(
        text("DELETE FROM password_reset WHERE member_id = :m AND expires_at < now()"),
        {"m": member_id},
    )
    await session.execute(
        text(
            "INSERT INTO password_reset (workspace_id, member_id, token_hash, expires_at) "
            "VALUES (:ws, :m, :h, :exp)"
        ),
        {"ws": workspace_id, "m": member_id, "h": token_hash, "exp": expires_at},
    )


async def take_reset(session: AsyncSession, token_hash: bytes) -> Row | None:
    """The live reset link with this hash and its member (locked), or None."""
    result = await session.execute(
        text(
            "SELECT r.member_id, m.is_active FROM password_reset r "
            "JOIN member m ON m.id = r.member_id "
            "WHERE r.token_hash = :h AND r.expires_at > now() FOR UPDATE OF r, m"
        ),
        {"h": token_hash},
    )
    return result.mappings().first()


async def drop_resets(session: AsyncSession, member_id: UUID) -> None:
    await session.execute(text("DELETE FROM password_reset WHERE member_id = :m"), {"m": member_id})
