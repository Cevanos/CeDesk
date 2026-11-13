"""Guard: migrations have one head and survive a full down-and-up cycle."""

from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from tests.conftest import alembic_config, empty_archive, empty_archive_async

pytestmark = pytest.mark.db


def test_there_is_exactly_one_head() -> None:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"Two people added migrations in parallel: {heads}"


def test_downgrade_to_base_and_back_up(migrated_db: None) -> None:
    cfg = alembic_config()
    empty_archive()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_members_from_before_0012_stay_available(
    app_sessionmaker: Any, owner_engine: Any
) -> None:
    """0012 makes new agents start Offline; members who existed before keep routing as
    Available (a forced-RLS table once hid them from this copy)."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0011")
        # A member as 0011 stored them (plain SQL: today's code expects today's columns).
        # It has no routing row yet.
        async with owner_engine.begin() as conn:
            for table in ("workspace", "member"):
                await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            ws = await conn.scalar(
                text("INSERT INTO workspace (name) VALUES ('Before 0012') RETURNING id")
            )
            await conn.execute(
                text(
                    "INSERT INTO member (workspace_id, email, name, role, password_hash) "
                    "VALUES (:ws, 'old@example.com', 'Old', 'agent', 'x')"
                ),
                {"ws": ws},
            )
            for table in ("workspace", "member"):
                await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        await conn.execute(text("ALTER TABLE member NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE hd_agent_state NO FORCE ROW LEVEL SECURITY"))
        try:
            missing = await conn.scalar(
                text(
                    "SELECT count(*) FROM member m LEFT JOIN hd_agent_state s "
                    "ON s.member_id = m.id WHERE m.role <> 'light_agent' "
                    "AND s.status IS DISTINCT FROM 'available'"
                )
            )
        finally:
            await conn.rollback()
    assert missing == 0


async def test_0049_wont_go_down_while_tickets_are_archived(owner_engine: Any) -> None:
    """Dropping the archive would lose its tickets: the downgrade refuses and changes
    nothing; once the archive is empty it goes down and back up."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    async with owner_engine.begin() as conn:
        for table in ("workspace", "hd_ticket_archive"):
            await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('O3') RETURNING id"))
        await conn.execute(
            text(
                "INSERT INTO hd_ticket_archive (workspace_id, id, number, subject, status, "
                "priority, channel, created_at, ticket, messages) VALUES (:ws, gen_random_uuid(), "
                "1, 'Old', 'resolved', 'low', 'web', now(), '{}', '[]')"
            ),
            {"ws": ws},
        )
        for table in ("workspace", "hd_ticket_archive"):
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    with pytest.raises(RuntimeError, match="holds 1 archived tickets"):
        await asyncio.to_thread(command.downgrade, cfg, "0048")
    async with owner_engine.connect() as conn:  # rolled back: still there, still forced
        assert await conn.scalar(text("SELECT to_regclass('hd_ticket_archive')")) is not None
        forced = "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'hd_ticket_archive'"
        assert await conn.scalar(text(forced)) is True
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0048")
    await asyncio.to_thread(command.upgrade, cfg, "head")
