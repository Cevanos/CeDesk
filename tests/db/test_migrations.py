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


async def test_0053_wont_go_down_while_exports_or_holds_exist(owner_engine: Any) -> None:
    """Going below 0053 would lose retention exports and Governance hold approvals: the
    downgrade refuses and changes nothing; once they are gone it goes down and back up."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    async with owner_engine.begin() as conn:
        for table in ("workspace", "hd_retention_export"):
            await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('O2') RETURNING id"))
        await conn.execute(
            text(
                "INSERT INTO hd_retention_export (workspace_id, object_key, tickets, size, "
                "expires_at) VALUES (:ws, 'k.json', 1, 2, now())"
            ),
            {"ws": ws},
        )
        for table in ("workspace", "hd_retention_export"):
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    with pytest.raises(RuntimeError, match="1 retention exports exist"):
        await asyncio.to_thread(command.downgrade, cfg, "0052")
    async with owner_engine.connect() as conn:  # rolled back: still there
        assert await conn.scalar(text("SELECT to_regclass('hd_retention_export')")) is not None
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0052")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('hd_retention_export')")) is None
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def test_0052_upgrades_an_archive_from_0049s_first_version(owner_engine: Any) -> None:
    """Servers ran 0049 as first written. A ticket archived then: 0049 still refuses to go
    down while it is there, and 0052 fills `source_id` and `file_bytes` from what it kept
    (its `related` and `email_ids` start empty: that version didn't keep them)."""
    import asyncio
    import json

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0051")
    try:
        async with owner_engine.begin() as conn:
            for table in ("workspace", "hd_ticket_archive"):
                await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            ws = await conn.scalar(
                text("INSERT INTO workspace (name) VALUES ('0052') RETURNING id")
            )
            await conn.execute(
                text(
                    "INSERT INTO hd_ticket_archive (workspace_id, id, number, subject, status, "
                    "priority, channel, source, created_at, ticket, messages, files) VALUES "
                    "(:ws, gen_random_uuid(), 7, 'Old', 'resolved', 'low', 'web', 'zendesk', "
                    "now(), CAST(:ticket AS jsonb), '[]', CAST(:files AS jsonb))"
                ),
                {
                    "ws": ws,
                    "ticket": json.dumps({"source_id": "z-9"}),
                    "files": json.dumps([{"name": "a", "size": 100}, {"name": "b", "size": 23}]),
                },
            )
            for table in ("workspace", "hd_ticket_archive"):
                await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        with pytest.raises(RuntimeError, match="downgrading 0049 would"):
            await asyncio.to_thread(command.downgrade, cfg, "0048")

        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with owner_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE hd_ticket_archive NO FORCE ROW LEVEL SECURITY"))
            row = (
                await conn.execute(
                    text(
                        "SELECT source_id, file_bytes, related, email_ids FROM "
                        "hd_ticket_archive WHERE workspace_id = :ws"
                    ),
                    {"ws": ws},
                )
            ).one()
            await conn.execute(text("ALTER TABLE hd_ticket_archive FORCE ROW LEVEL SECURITY"))
        assert tuple(row) == ("z-9", 123, {}, [])
    finally:
        await empty_archive_async()
        await asyncio.to_thread(command.upgrade, cfg, "head")
