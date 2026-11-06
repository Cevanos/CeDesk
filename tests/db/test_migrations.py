"""Guard: migrations have one head and survive a full down-and-up cycle."""

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
