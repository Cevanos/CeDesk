"""Guard: every table is a tenant table with forced RLS, unless it is listed as global."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.db

# Tables that hold no workspace data. Adding one here needs a reason.
GLOBAL_TABLES = {
    "alembic_version",
    # slug -> workspace id, read at sign-in before any workspace is known.
    # No names, members or tickets; the app can add rows but not change or remove them.
    "workspace_directory",
}

TABLES = text(
    """
    SELECT c.relname AS name,
           c.relrowsecurity AS rls,
           c.relforcerowsecurity AS forced,
           EXISTS (SELECT 1 FROM pg_attribute a
                   WHERE a.attrelid = c.oid AND a.attname = 'workspace_id'
                     AND NOT a.attisdropped) AS has_workspace_id,
           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)


async def test_every_table_has_workspace_id_and_forced_rls(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = (await conn.execute(TABLES)).mappings().all()
    problems = []
    for row in rows:
        if row["name"] in GLOBAL_TABLES:
            continue
        tenant_key = row["has_workspace_id"] or row["name"] == "workspace"
        if not tenant_key:
            problems.append(f"{row['name']}: no workspace_id")
        if not (row["rls"] and row["forced"]):
            problems.append(f"{row['name']}: RLS not enabled and forced")
        if row["policies"] == 0:
            problems.append(f"{row['name']}: no RLS policy")
    assert not problems, "\n".join(problems)
