"""Test fixtures.

DB tests run against the `cedesk_test` database from docker-compose (port 5447),
never the development database. Migrations run once per session as the owner;
tests connect as `cedesk_app`, so row-level security is really exercised.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cedesk.db import make_engine, make_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
OWNER_URL = os.environ.get(
    "CEDESK_TEST_OWNER_URL",
    "postgresql+asyncpg://cedesk_owner:cedesk_owner_dev@localhost:5447/cedesk_test",
)
APP_URL = os.environ.get(
    "CEDESK_TEST_APP_URL",
    "postgresql+asyncpg://cedesk_app:cedesk_app_dev@localhost:5447/cedesk_test",
)


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", OWNER_URL)
    return cfg


def _reachable() -> bool:
    async def ping() -> None:
        engine = make_engine(OWNER_URL)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except (OSError, OperationalError):
        return False
    return True


async def empty_archive_async() -> None:
    """Migration 0049 refuses to go down while tickets are archived, and 0053 while
    Governance hold approvals or retention exports exist (that would lose them); test runs
    leave some: removed before a test goes below them. TRUNCATE is not subject to
    row-level security; the DELETE lifts it."""
    engine = make_engine(OWNER_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DO $$ BEGIN "
                    "IF to_regclass('hd_ticket_archive') IS NOT NULL THEN "
                    "TRUNCATE hd_ticket_archive; END IF; "
                    "IF to_regclass('hd_retention_export') IS NOT NULL THEN "
                    "TRUNCATE hd_retention_export; END IF; "
                    "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE "
                    "table_name = 'hd_approval' AND column_name = 'cevanos_hold_id') THEN "
                    "ALTER TABLE hd_approval NO FORCE ROW LEVEL SECURITY; "
                    "DELETE FROM hd_approval WHERE cevanos_hold_id IS NOT NULL; "
                    "ALTER TABLE hd_approval FORCE ROW LEVEL SECURITY; END IF; "
                    "END $$"
                )
            )
    finally:
        await engine.dispose()


def empty_archive() -> None:
    asyncio.run(empty_archive_async())


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    if not _reachable():
        if os.environ.get("CEDESK_REQUIRE_DB") == "1":
            pytest.fail("Test Postgres is not reachable on CEDESK_TEST_OWNER_URL")
        pytest.skip("Test Postgres not running: `docker compose up -d db`")
    cfg = alembic_config()
    empty_archive()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
async def owner_engine(migrated_db: None) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(OWNER_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_sessionmaker(migrated_db: None) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(APP_URL)
    yield make_sessionmaker(engine)
    await engine.dispose()
