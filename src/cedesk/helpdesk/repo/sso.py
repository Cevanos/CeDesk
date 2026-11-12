"""SQL for single sign-on (md S1). RLS applies."""

from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def settings_row(session: AsyncSession) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT s.enabled, s.enforced, s.idp_entity_id, s.idp_sso_url, s.idp_cert, "
            "s.updated_at, s.updated_by, m.name AS updated_by_name "
            "FROM hd_sso_settings s LEFT JOIN member m ON m.id = s.updated_by"
        )
    )
    return result.mappings().first()


async def lock_settings(session: AsyncSession, workspace_id: UUID) -> RowMapping | None:
    await session.execute(
        text(
            "INSERT INTO hd_sso_settings (workspace_id) VALUES (:ws) "
            "ON CONFLICT (workspace_id) DO NOTHING"
        ),
        {"ws": workspace_id},
    )
    result = await session.execute(
        text(
            "SELECT enabled, enforced, idp_entity_id, idp_sso_url, idp_cert "
            "FROM hd_sso_settings FOR UPDATE"
        )
    )
    return result.mappings().first()


async def save(session: AsyncSession, values: dict[str, Any], by: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_sso_settings SET enabled = :enabled, enforced = :enforced, "
            "idp_entity_id = :idp_entity_id, idp_sso_url = :idp_sso_url, idp_cert = :idp_cert, "
            "updated_by = :by, updated_at = now()"
        ),
        values | {"by": by},
    )


async def enforced(session: AsyncSession) -> bool:
    result = await session.execute(text("SELECT enforced FROM hd_sso_settings"))
    return bool(result.scalar_one_or_none())


async def add_request(
    session: AsyncSession, workspace_id: UUID, request_id: str, browser_hash: bytes
) -> None:
    """Remembers a sign-in we started; also forgets ones over a day old."""
    await session.execute(
        text("DELETE FROM hd_sso_request WHERE created_at < now() - interval '1 day'")
    )
    await session.execute(
        text("INSERT INTO hd_sso_request (id, workspace_id, browser_hash) VALUES (:id, :ws, :bh)"),
        {"id": request_id, "ws": workspace_id, "bh": browser_hash},
    )


async def take_request(session: AsyncSession, request_id: str, browser_hash: bytes) -> bool:
    """Uses up one of our requests: unused, under 10 minutes old (no replays), and from
    the browser that started it (no login CSRF)."""
    result = await session.execute(
        text(
            "UPDATE hd_sso_request SET used_at = now() WHERE id = :id AND used_at IS NULL "
            "AND created_at > now() - interval '10 minutes' AND browser_hash = :bh "
            "RETURNING id"
        ),
        {"id": request_id, "bh": browser_hash},
    )
    return result.first() is not None


async def active_member(session: AsyncSession, email: str) -> RowMapping | None:
    result = await session.execute(
        text("SELECT id, role FROM member WHERE email = :email AND is_active"),
        {"email": email},
    )
    return result.mappings().first()
