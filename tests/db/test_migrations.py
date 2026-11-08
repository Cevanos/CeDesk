"""Guard: migrations have one head and survive a full down-and-up cycle."""

from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from tests.conftest import alembic_config

pytestmark = pytest.mark.db


def test_there_is_exactly_one_head() -> None:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"Two people added migrations in parallel: {heads}"


def test_downgrade_to_base_and_back_up(migrated_db: None) -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_members_from_before_0011_stay_available(
    app_sessionmaker: Any, owner_engine: Any
) -> None:
    """0011 makes new agents start Offline; members who existed before keep routing as
    Available (a forced-RLS table once hid them from this copy)."""
    import asyncio

    from sqlalchemy import text

    from tests.db.helpers import new_workspace

    cfg = alembic_config()
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0010")
        await new_workspace(app_sessionmaker)  # its Admin has no routing row yet
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
