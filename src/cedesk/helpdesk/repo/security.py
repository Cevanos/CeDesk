"""SQL for workspace security settings (md S3-S6, S8). RLS applies."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.security import Security, from_row
from cedesk.helpdesk.repo import outbox as outbox_repo

COLUMNS = (
    "s.password_min, s.password_expiry_days, s.session_length, "
    "CAST(s.ip_allowlist AS text[]) AS ip_allowlist, s.attachments_need_sign_in, "
    "s.admin_alerts, s.require_2fa"
)


async def settings_row(session: AsyncSession) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT " + COLUMNS + ", s.updated_at, s.updated_by, m.name AS updated_by_name "
            "FROM hd_security_settings s LEFT JOIN member m ON m.id = s.updated_by"
        )
    )
    return result.mappings().first()


async def lock_settings(session: AsyncSession, workspace_id: UUID) -> RowMapping | None:
    """Makes sure the row exists, then locks it, so two saves don't both send the "IP list
    changed" alert from the same old list."""
    await session.execute(
        text(
            "INSERT INTO hd_security_settings (workspace_id) VALUES (:ws) "
            "ON CONFLICT (workspace_id) DO NOTHING"
        ),
        {"ws": workspace_id},
    )
    result = await session.execute(
        text("SELECT " + COLUMNS + " FROM hd_security_settings s FOR UPDATE")  # noqa: S608
    )
    return result.mappings().first()


async def save(session: AsyncSession, values: dict[str, Any], by: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_security_settings SET password_min = :password_min, "
            "password_expiry_days = :password_expiry_days, session_length = :session_length, "
            "ip_allowlist = CAST(:ip_allowlist AS cidr[]), "
            "attachments_need_sign_in = :attachments_need_sign_in, "
            "admin_alerts = :admin_alerts, require_2fa = :require_2fa, updated_by = :by, "
            "updated_at = now()"
        ),
        values | {"by": by},
    )


async def shorten_sessions(session: AsyncSession, ends_sql: str) -> None:
    """Live sessions end by the new rule if that is sooner (`ends_sql` is one of the
    constants in `SHORTEN`). A longer setting doesn't lengthen sessions already made."""
    await session.execute(
        text(
            "UPDATE auth_session SET expires_at = LEAST(expires_at, "  # noqa: S608
            + ends_sql
            + ") WHERE revoked_at IS NULL AND expires_at > now()"
        )
    )


SHORTEN = {
    "1h_idle": "last_used_at + interval '1 hour', created_at + interval '7 days'",
    "8h": "created_at + interval '8 hours'",
    "12h": "created_at + interval '12 hours'",
    "7d": "created_at + interval '7 days'",
}


async def touch_session(session: AsyncSession, session_id: UUID, expires_at: Any) -> None:
    await session.execute(
        text("UPDATE auth_session SET last_used_at = now(), expires_at = :exp WHERE id = :id"),
        {"id": session_id, "exp": expires_at},
    )


async def admins(session: AsyncSession) -> Sequence[RowMapping]:
    """Active Admins, who get the security alerts."""
    result = await session.execute(
        text("SELECT email, name FROM member WHERE role = 'admin' AND is_active ORDER BY email")
    )
    return result.mappings().all()


async def score_facts(session: AsyncSession) -> RowMapping:
    """Settings kept elsewhere that count toward the score: card redaction (on unless
    turned off, I5) and the portal's email domain list (S7)."""
    result = await session.execute(
        text(
            "SELECT COALESCE((SELECT redact_cards FROM hd_triage_settings), true) AS redaction, "
            "COALESCE((SELECT cardinality(allowed_domains) > 0 FROM hd_portal_settings), false) "
            "AS portal_domains, "
            "COALESCE((SELECT enabled AND enforced FROM hd_sso_settings), false) AS sso"
        )
    )
    return result.mappings().one()


async def current(session: AsyncSession) -> Security:
    return from_row(await settings_row(session))


async def clear_ip_allowlist(session: AsyncSession) -> bool:
    """Empties the list; False when it was already empty."""
    result = await session.execute(
        text(
            "UPDATE hd_security_settings SET ip_allowlist = '{}', updated_at = now(), "
            "updated_by = NULL WHERE cardinality(ip_allowlist) > 0 RETURNING workspace_id"
        )
    )
    return result.first() is not None


async def queue_alert(
    session: AsyncSession, workspace_id: UUID, what: str, by: UUID | None
) -> None:
    """A `security.alert` outbox event; the worker emails every active Admin (S6). Only
    when the workspace has the alerts on."""
    if (await current(session)).admin_alerts:
        await outbox_repo.add(
            session,
            workspace_id,
            "security.alert",
            None,
            {"what": what, "by": str(by) if by else None},
        )


async def has_two_factor(session: AsyncSession, member_id: UUID) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM hd_member_2fa WHERE member_id = :id AND confirmed_at IS NOT NULL"),
        {"id": member_id},
    )
    return result.first() is not None
