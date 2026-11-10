"""Fixtures for API tests against the test database."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.main import create_app
from cedesk.settings import Settings


@pytest.fixture
async def client(app_sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    # Every test request comes from one address; the attempt limit has its own test
    # (test_server_ready.py), so here it is out of the way.
    app = create_app(Settings(sign_in_limit=100_000))
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
