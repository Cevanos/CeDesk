"""SQL for two-step sign-in (md S2). RLS applies."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def get(session: AsyncSession, member_id: UUID, *, lock: bool = False) -> RowMapping | None:
    sql = "SELECT secret_sealed, confirmed_at, last_step FROM hd_member_2fa WHERE member_id = :id"
    result = await session.execute(text(sql + (" FOR UPDATE" if lock else "")), {"id": member_id})
    return result.mappings().first()


async def start(session: AsyncSession, workspace_id: UUID, member_id: UUID, sealed: bytes) -> bool:
    """A new secret waiting to be confirmed. False when 2FA is already on."""
    result = await session.execute(
        text(
            "INSERT INTO hd_member_2fa (workspace_id, member_id, secret_sealed) "
            "VALUES (:ws, :id, :sealed) ON CONFLICT (member_id) DO UPDATE SET "
            "secret_sealed = EXCLUDED.secret_sealed, last_step = 0, created_at = now() "
            "WHERE hd_member_2fa.confirmed_at IS NULL RETURNING member_id"
        ),
        {"ws": workspace_id, "id": member_id, "sealed": sealed},
    )
    return result.first() is not None


async def confirm(session: AsyncSession, member_id: UUID, step: int) -> None:
    await session.execute(
        text(
            "UPDATE hd_member_2fa SET confirmed_at = now(), last_step = :step WHERE member_id = :id"
        ),
        {"id": member_id, "step": step},
    )


async def use_step(session: AsyncSession, member_id: UUID, step: int) -> None:
    await session.execute(
        text("UPDATE hd_member_2fa SET last_step = :step WHERE member_id = :id"),
        {"id": member_id, "step": step},
    )


async def remove(session: AsyncSession, member_id: UUID) -> bool:
    """Turns 2FA off: the secret and the recovery codes go. False if it wasn't set up."""
    await session.execute(
        text("DELETE FROM hd_recovery_code WHERE member_id = :id"), {"id": member_id}
    )
    result = await session.execute(
        text("DELETE FROM hd_member_2fa WHERE member_id = :id RETURNING confirmed_at"),
        {"id": member_id},
    )
    row = result.first()
    return row is not None and row[0] is not None


async def replace_codes(
    session: AsyncSession, workspace_id: UUID, member_id: UUID, hashes: Sequence[bytes]
) -> None:
    await session.execute(
        text("DELETE FROM hd_recovery_code WHERE member_id = :id"), {"id": member_id}
    )
    await session.execute(
        text(
            "INSERT INTO hd_recovery_code (workspace_id, member_id, code_hash) "
            "VALUES (:ws, :id, :hash)"
        ),
        [{"ws": workspace_id, "id": member_id, "hash": h} for h in hashes],
    )


async def use_recovery_code(session: AsyncSession, member_id: UUID, code_hash: bytes) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_recovery_code SET used_at = now() WHERE member_id = :id "
            "AND code_hash = :hash AND used_at IS NULL RETURNING id"
        ),
        {"id": member_id, "hash": code_hash},
    )
    return result.first() is not None


async def codes_left(session: AsyncSession, member_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_recovery_code WHERE member_id = :id AND used_at IS NULL"),
        {"id": member_id},
    )
    return int(result.scalar_one())


async def finish_sign_in(session: AsyncSession, session_id: UUID, expires_at: datetime) -> None:
    """The code was right: the session becomes a normal one, timed from now."""
    await session.execute(
        text(
            "UPDATE auth_session SET second_factor_pending = false, expires_at = :exp, "
            "last_used_at = now() WHERE id = :id"
        ),
        {"id": session_id, "exp": expires_at},
    )
