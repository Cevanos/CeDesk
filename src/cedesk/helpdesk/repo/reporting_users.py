"""Reporting logins (differentiator plan #7). These run on a session of the database
superuser (`CEDESK_ADMIN_DATABASE_URL`): creating a login needs it. Names and the
password verifier are quoted by PostgreSQL itself (`format(%I, %L)`), never by hand."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain import reporting as rules


async def _run(session: AsyncSession, template: str, *args: str) -> None:
    """A utility statement (CREATE ROLE, GRANT, ...) with identifiers or literals in it:
    PostgreSQL builds it with format(), then it is run."""
    params = {f"a{i}": a for i, a in enumerate(args)}
    placeholders = "".join(f", CAST(:a{i} AS text)" for i in range(len(args)))
    built = await session.execute(text(f"SELECT format(CAST(:t AS text){placeholders})"),
                                  {"t": template, **params})  # fmt: skip
    await session.execute(text(str(built.scalar_one()).replace(":", r"\:")))


async def ready(session: AsyncSession) -> bool:
    """Migration 0080 is applied."""
    result = await session.execute(text("SELECT to_regclass('public.hd_reporting_access')"))
    return result.scalar_one() is not None


async def role_exists(session: AsyncSession, name: str) -> bool:
    result = await session.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
    return result.first() is not None


async def workspace_for_slug(session: AsyncSession, slug: str) -> UUID | None:
    result = await session.execute(
        text("SELECT workspace_id FROM workspace_directory WHERE slug = :s"), {"s": slug}
    )
    found = result.scalar_one_or_none()
    return None if found is None else UUID(str(found))


async def create(
    session: AsyncSession, name: str, verifier: str, workspace_id: UUID | None
) -> None:
    """The login, in the reporting group, read-only, limited; then its access row."""
    if not await role_exists(session, rules.GROUP):
        await _run(session, "CREATE ROLE %I NOLOGIN", rules.GROUP)
    owner = await session.execute(
        text("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'reporting'")
    )
    view_owner = str(owner.scalar_one())
    database = str((await session.execute(text("SELECT current_database()"))).scalar_one())
    await _run(session, "GRANT CONNECT ON DATABASE %I TO %I", database, rules.GROUP)
    await _run(session, "GRANT USAGE ON SCHEMA reporting TO %I", rules.GROUP)
    await _run(session, "GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO %I", rules.GROUP)
    await _run(
        session,
        "ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA reporting GRANT SELECT ON TABLES TO %I",
        view_owner, rules.GROUP,
    )  # fmt: skip
    await _run(
        session,
        "CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        f"NOBYPASSRLS INHERIT CONNECTION LIMIT {rules.CONNECTION_LIMIT}",
        name, verifier,
    )  # fmt: skip
    await _run(session, "GRANT %I TO %I", rules.GROUP, name)
    await _run(session, "ALTER ROLE %I SET default_transaction_read_only = on", name)
    await _run(session, f"ALTER ROLE %I SET statement_timeout = '{rules.STATEMENT_TIMEOUT}'",
               name)  # fmt: skip
    await _run(session, "ALTER ROLE %I SET search_path = reporting", name)
    await session.execute(
        text("INSERT INTO hd_reporting_access (role_name, workspace_id) VALUES (:n, :w)"),
        {"n": name, "w": workspace_id},
    )


async def drop(session: AsyncSession, name: str) -> bool:
    """False: no such reporting login (a role not made by `create` is never dropped)."""
    result = await session.execute(
        text("DELETE FROM hd_reporting_access WHERE role_name = :n RETURNING role_name"),
        {"n": name},
    )
    if result.first() is None:
        return False
    if await role_exists(session, name):
        await _run(session, "DROP OWNED BY %I", name)
        await _run(session, "DROP ROLE %I", name)
    return True


async def list_logins(session: AsyncSession) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT a.role_name, a.created_at, d.slug FROM hd_reporting_access a "
            "LEFT JOIN workspace_directory d ON d.workspace_id = a.workspace_id "
            "ORDER BY a.role_name"
        )
    )
    return result.mappings().all()
