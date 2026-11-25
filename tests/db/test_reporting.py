"""Open analytics (differentiator plan #7) on real PostgreSQL: reporting logins and the
`reporting` views.

- the views hold no text a person typed and no names or email addresses of people;
- a login made for one workspace reads that workspace's rows only, through the views
  only: no `hd_` table, no writing; a login for every workspace reads them all;
- the app's own connection is unchanged and can't read who has a reporting login;
- names, passwords, unknown workspaces and duplicates are refused; dropping removes the
  login.

Logins are database roles (shared by every database on the server), so each test makes
its own names and drops them at the end.
"""

import os
import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.db import make_engine
from cedesk.helpdesk.service import reporting_users as service
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from tests.db.helpers import Sm
from tests.db.inbox_helpers import desk, sql
from tests.db.test_automation_rules import new_ticket

pytestmark = pytest.mark.db

ADMIN_URL = os.environ.get(
    "CEDESK_TEST_ADMIN_URL",
    "postgresql+asyncpg://postgres:postgres_dev@127.0.0.1:5447/cedesk_test",
)
LOGIN_URL = "postgresql+asyncpg://{name}:{password}@127.0.0.1:5447/cedesk_test"
PASSWORD = "a-long-test-password-1234"

# Columns no view may have: text people typed, and who people are.
PERSONAL = {
    "subject", "body", "comment", "email", "tags", "fields", "search_text", "author_name",
    "contact_id", "password_hash", "handover", "recipients", "conversation_id", "source_id",
}  # fmt: skip
NAMED_VIEWS = {"teams", "ticket_types", "workspaces"}  # names of setup, not of people


@pytest.fixture
async def admin() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(ADMIN_URL)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def cleanup(admin: async_sessionmaker[AsyncSession], *names: str) -> None:
    for name in names:
        try:
            await service.drop(admin, name)
        except NotFound:
            pass


def login_name() -> str:
    return f"report_t{secrets.token_hex(4)}"


async def as_login(name: str, query: str) -> list[Any]:
    engine = make_engine(LOGIN_URL.format(name=name, password=PASSWORD))
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(query))).all())
    finally:
        await engine.dispose()


async def views(admin: async_sessionmaker[AsyncSession]) -> dict[str, set[str]]:
    async with admin() as s:
        rows = (await s.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'reporting'"
        ))).all()  # fmt: skip
    found: dict[str, set[str]] = {}
    for view, column in rows:
        found.setdefault(view, set()).add(column)
    return found


async def test_the_views_hold_no_personal_data(
    app_sessionmaker: Sm, admin: async_sessionmaker[AsyncSession]
) -> None:
    found = await views(admin)
    assert set(found) == {
        "tickets", "messages", "csat", "members", "teams", "team_members", "ticket_types",
        "workspaces",
    }  # fmt: skip
    for view, columns in found.items():
        assert not columns & PERSONAL, (view, columns & PERSONAL)
        if view not in NAMED_VIEWS:
            assert "name" not in columns, view


async def test_a_login_reads_its_workspace_only_through_the_views(
    client: AsyncClient, app_sessionmaker: Sm, admin: async_sessionmaker[AsyncSession]
) -> None:
    a = await desk(client, app_sessionmaker)
    b = await desk(client, app_sessionmaker)
    for subject in ("One", "Two", "Three"):
        await new_ticket(client, a, subject)
    await new_ticket(client, b, "Other")
    one, every = login_name(), login_name()
    try:
        assert await service.create(admin, one, PASSWORD, a["slug"]) == a["ws"]
        assert await service.create(admin, every, PASSWORD, None) is None

        mine = await as_login(one, "SELECT DISTINCT workspace_id FROM reporting.tickets")
        assert mine == [(a["ws"],)]
        assert len(await as_login(one, "SELECT 1 FROM reporting.tickets")) == 3
        in_a = await sql(app_sessionmaker, a["ws"], "SELECT count(*) FROM hd_message")
        assert len(await as_login(one, "SELECT 1 FROM reporting.messages")) == in_a[0][0] > 0
        assert await as_login(one, "SELECT workspace_id FROM workspaces") == [(a["ws"],)]
        # search_path is the reporting schema; team names come through, people don't.
        assert {r[0] for r in await as_login(one, "SELECT name FROM teams")} == {
            "Billing", "Shipping",
        }  # fmt: skip
        seen = {r[0] for r in await as_login(every, "SELECT workspace_id FROM tickets")}
        assert {a["ws"], b["ws"]} <= seen

        # No hd_ table, no writing.
        for query in ("SELECT 1 FROM public.hd_ticket", "SELECT 1 FROM public.member",
                      "SELECT 1 FROM public.hd_reporting_access",
                      "DELETE FROM reporting.tickets"):  # fmt: skip
            with pytest.raises(DBAPIError):
                await as_login(one, query)
    finally:
        await cleanup(admin, one, every)


async def test_the_apps_connection_is_unchanged(
    client: AsyncClient, app_sessionmaker: Sm, admin: async_sessionmaker[AsyncSession]
) -> None:
    w = await desk(client, app_sessionmaker)
    name = login_name()
    await service.create(admin, name, PASSWORD, None)
    try:
        async with app_sessionmaker() as s:  # no workspace set: RLS shows nothing
            assert (await s.execute(text("SELECT count(*) FROM hd_ticket"))).scalar_one() == 0
        async with app_sessionmaker() as s:
            with pytest.raises(DBAPIError):
                await s.execute(text("SELECT 1 FROM hd_reporting_access"))
        assert w["ws"]
    finally:
        await cleanup(admin, name)


async def test_bad_input_is_refused_and_drop_removes_the_login(
    client: AsyncClient, app_sessionmaker: Sm, admin: async_sessionmaker[AsyncSession]
) -> None:
    w = await desk(client, app_sessionmaker)
    for bad in ("bi", "report_X", "report_a;drop", "cedesk_owner"):
        with pytest.raises(Invalid):
            await service.create(admin, bad, PASSWORD, w["slug"])
    with pytest.raises(Invalid, match="16 characters"):
        await service.create(admin, login_name(), "short", w["slug"])
    with pytest.raises(NotFound):
        await service.create(admin, login_name(), PASSWORD, "no-such-desk")

    name = login_name()
    await service.create(admin, name, PASSWORD, w["slug"])
    with pytest.raises(Conflict):
        await service.create(admin, name, PASSWORD, w["slug"])
    listed = {r["role_name"]: r["slug"] for r in await service.list_logins(admin)}
    assert listed[name] == w["slug"]

    await service.drop(admin, name)
    with pytest.raises(NotFound):
        await service.drop(admin, name)
    with pytest.raises(DBAPIError):  # the login is gone
        await as_login(name, "SELECT 1")
    with pytest.raises(NotFound):  # a role it didn't make is never dropped
        await service.drop(admin, "report_not_ours")
