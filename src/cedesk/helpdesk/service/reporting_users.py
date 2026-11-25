"""Reporting logins for BI tools (differentiator plan #7), for the server's operator:
`cedesk reporting-user create|drop|list`. They run with the database superuser
(`CEDESK_ADMIN_DATABASE_URL`), never the app's connection. Each is one transaction: a
login is made with its rights and its access row, or not at all.

A login reads the `reporting` views only (migration 0080), of one workspace or (with
`--all`) of every workspace, read-only, at most 5 connections, 60 s a query."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.helpdesk.domain import reporting as rules
from cedesk.helpdesk.repo import reporting_users as repo
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound

NOT_READY = "The reporting views aren't there yet: run the migrations first (0080)."

AdminSessions = async_sessionmaker[AsyncSession]


def _checked(name: str, password: str | None = None) -> None:
    try:
        rules.check_name(name)
        if password is not None:
            rules.check_password(password)
    except rules.ReportingError as exc:
        raise Invalid(str(exc)) from exc


async def _ready(s: AsyncSession) -> None:
    if not await repo.ready(s):
        raise Conflict(NOT_READY)


async def create(admin: AdminSessions, name: str, password: str, slug: str | None) -> UUID | None:
    """`slug` None: every workspace. Returns the workspace id (None: every one)."""
    _checked(name, password)
    verifier = rules.scram_verifier(password)
    async with admin() as s, s.begin():
        await _ready(s)
        workspace_id = None
        if slug is not None:
            workspace_id = await repo.workspace_for_slug(s, slug.strip().lower())
            if workspace_id is None:
                raise NotFound(f"No workspace with the address {slug!r}.")
        if await repo.role_exists(s, name):
            raise Conflict(f"A database role named {name} exists already.")
        await repo.create(s, name, verifier, workspace_id)
    return workspace_id


async def drop(admin: AdminSessions, name: str) -> None:
    _checked(name)
    async with admin() as s, s.begin():
        await _ready(s)
        if not await repo.drop(s, name):
            raise NotFound(f"No reporting login named {name}.")


async def list_logins(admin: AdminSessions) -> Sequence[RowMapping]:
    async with admin() as s, s.begin():
        await _ready(s)
        return await repo.list_logins(s)
