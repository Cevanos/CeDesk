"""SQL for marketplace apps (md PL7). RLS applies."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = (
    "a.app, a.settings, a.secret_hint, a.installed_at, a.updated_at, a.installed_by, "
    "m.name AS installed_by_name"
)
_FROM = "FROM hd_app_install a LEFT JOIN member m ON m.id = a.installed_by "


async def installed(session: AsyncSession) -> Sequence[RowMapping]:
    result = await session.execute(text(f"SELECT {_COLUMNS} {_FROM} ORDER BY a.app"))
    return result.mappings().all()


async def get(session: AsyncSession, app: str) -> RowMapping | None:
    """With the sealed credential, for reading cards."""
    result = await session.execute(
        text(f"SELECT {_COLUMNS}, a.secret_sealed {_FROM} WHERE a.app = :app"),
        {"app": app},
    )
    return result.mappings().first()


async def save(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    app: str,
    settings: dict[str, Any],
    secret_sealed: bytes,
    secret_hint: str,
    by: UUID,
) -> bool:
    """Installs, or replaces the settings and credential. True when it was new."""
    result = await session.execute(
        text(
            "INSERT INTO hd_app_install (workspace_id, app, settings, secret_sealed, "
            "secret_hint, installed_by) VALUES (:ws, :app, CAST(:settings AS jsonb), :sealed, "
            ":hint, :by) ON CONFLICT (workspace_id, app) DO UPDATE SET "
            "settings = EXCLUDED.settings, secret_sealed = EXCLUDED.secret_sealed, "
            "secret_hint = EXCLUDED.secret_hint, updated_at = now() "
            "RETURNING (xmax = 0) AS inserted"
        ),
        {
            "ws": workspace_id,
            "app": app,
            "settings": json.dumps(settings),
            "sealed": secret_sealed,
            "hint": secret_hint,
            "by": by,
        },
    )
    return bool(result.scalar_one())


async def remove(session: AsyncSession, app: str) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_app_install WHERE app = :app RETURNING app"), {"app": app}
    )
    return result.first() is not None
