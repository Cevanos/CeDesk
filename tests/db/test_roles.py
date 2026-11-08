"""Guard: the app role cannot bypass RLS, and one workspace never sees another's rows."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cedesk.db import make_sessionmaker, workspace_session

pytestmark = pytest.mark.db


async def test_app_role_is_plain(app_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with app_sessionmaker() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT current_user AS me, r.rolsuper, r.rolbypassrls, "
                        "(SELECT count(*) FROM pg_class c WHERE c.relowner = r.oid "
                        " AND c.relnamespace = 'public'::regnamespace) AS owned "
                        "FROM pg_roles r WHERE r.rolname = current_user"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["me"] == "cedesk_app"
    assert row["rolsuper"] is False
    assert row["rolbypassrls"] is False
    assert row["owned"] == 0


async def test_app_role_cannot_change_the_migration_marker(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with app_sessionmaker() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT has_table_privilege('alembic_version', 'SELECT') AS can_read, "
                        "has_table_privilege('alembic_version', 'INSERT') "
                        " OR has_table_privilege('alembic_version', 'UPDATE') "
                        " OR has_table_privilege('alembic_version', 'DELETE') "
                        " OR has_table_privilege('alembic_version', 'TRUNCATE') AS can_write"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["can_read"] is True
    assert row["can_write"] is False


async def test_a_workspace_sees_only_itself(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    a, b = uuid4(), uuid4()
    for ws in (a, b):
        async with workspace_session(app_sessionmaker, ws) as s:
            await s.execute(
                text("INSERT INTO workspace (id, name) VALUES (:id, :name)"),
                {"id": ws, "name": f"test {ws}"},
            )

    async with workspace_session(app_sessionmaker, a) as s:
        seen = (await s.execute(text("SELECT id FROM workspace"))).scalars().all()
    assert seen == [a]

    # No workspace set: no rows at all, not every row.
    async with app_sessionmaker() as s:
        assert (await s.execute(text("SELECT count(*) FROM workspace"))).scalar_one() == 0

    # Only the owner may delete a workspace (migration 0008).
    for ws in (a, b):
        async with workspace_session(make_sessionmaker(owner_engine), ws) as s:
            await s.execute(text("DELETE FROM workspace WHERE id = :id"), {"id": ws})


async def test_app_role_cannot_delete_a_workspace(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    ws = uuid4()
    async with workspace_session(app_sessionmaker, ws) as s:
        await s.execute(text("INSERT INTO workspace (id, name) VALUES (:id, 'x')"), {"id": ws})
    async with app_sessionmaker() as s:
        can = await s.execute(
            text(
                "SELECT has_table_privilege('workspace', 'DELETE') "
                "OR has_table_privilege('workspace', 'TRUNCATE')"
            )
        )
        assert can.scalar_one() is False
    with pytest.raises(DBAPIError, match="permission denied"):
        async with workspace_session(app_sessionmaker, ws) as s:
            await s.execute(text("DELETE FROM workspace WHERE id = :id"), {"id": ws})
    async with workspace_session(make_sessionmaker(owner_engine), ws) as s:
        await s.execute(text("DELETE FROM workspace WHERE id = :id"), {"id": ws})


async def test_writing_into_another_workspace_is_refused(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with workspace_session(app_sessionmaker, uuid4()) as s:
            await s.execute(
                text("INSERT INTO workspace (id, name) VALUES (:id, 'other')"),
                {"id": uuid4()},
            )
