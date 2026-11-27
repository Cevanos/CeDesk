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
    # support email address -> workspace id, read when an email arrives (migration 0017).
    # Only addresses; the app can only read it (triggers keep it in step).
    "email_address_directory",
    # verified help center domain -> workspace id, read before the workspace is known so the
    # frontend can serve a customer's own domain (0085). Only domains; the app can only read
    # it (a trigger on hd_help_domain keeps it in step).
    "help_domain_directory",
    # Self-service sign-ups waiting for their email code, and those code emails (0060):
    # read before any workspace exists. The password is only a hash, the code and the
    # client IP only HMACs; rows are deleted after two days.
    "workspace_signup",
    "auth_email_out",
    # AI apps that asked to connect (0087): the same Claude for every workspace, known
    # before any workspace grants it anything. Name, redirect URIs, the hash of a secret;
    # what a member granted lives in the tenant table oauth_grant.
    "oauth_client",
    # Request counts shared by every API process (0087): hashed keys and counts only.
    "rate_limit_hit",
    # reporting login -> workspace (or every workspace), read by the reporting policies of
    # migration 0080 to decide which rows a BI login's views show. Only role names and
    # workspace ids; the app has no rights on it; `cedesk reporting-user` (the database
    # superuser) fills it. RLS here would hide the rows those policies need to read.
    "hd_reporting_access",
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
