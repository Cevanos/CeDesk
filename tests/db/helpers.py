"""Shared steps for API tests: make a workspace, sign in, add members."""

from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.helpdesk.service.auth import create_workspace

PASSWORD = "correct-horse-battery"
Sm = async_sessionmaker[AsyncSession]


async def new_workspace(sm: Sm, *, seed: bool = False) -> str:
    """An empty workspace by default, so each test sets up exactly what it needs."""
    slug = f"ws-{uuid4().hex[:10]}"
    await create_workspace(
        sm,
        name="Test",
        slug=slug,
        admin_email="Admin@Example.com",
        admin_name="Admin",
        admin_password=PASSWORD,
        seed=seed,
    )
    return slug


async def sign_in(c: AsyncClient, slug: str, email: str, password: str = PASSWORD) -> str:
    r = await c.post(
        "/api/v1/auth/sign-in", json={"workspace": slug, "email": email, "password": password}
    )
    assert r.status_code == 200, r.text
    token: str = r.json()["token"]
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def add(c: AsyncClient, admin: str, email: str, role: str, name: str | None = None) -> str:
    r = await c.post(
        "/api/v1/members",
        headers=bearer(admin),
        json={"email": email, "name": name or email, "role": role, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    member_id: str = r.json()["id"]
    return member_id


def ok(r: Any, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json()
