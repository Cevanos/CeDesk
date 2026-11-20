"""Shared steps for the inbox tools' tests (feature-gap plan phase 1)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from tests.db.helpers import Sm, bearer, ok, sign_in
from tests.db.test_reports import T, desk, ticket

__all__ = ["T", "desk", "ticket"]


async def as_(client: AsyncClient, w: dict[str, Any], who: str) -> dict[str, str]:
    """Headers for a member of `desk` (Neha, Ravi, Sam, Meena) or the Admin."""
    email = "admin@example.com" if who == "Admin" else f"{who.lower()}@example.com"
    return bearer(await sign_in(client, w["slug"], email))


async def bell(client: AsyncClient, w: dict[str, Any], who: str, kind: str) -> list[str]:
    h = await as_(client, w, who)
    found = ok(await client.get("/api/v1/notifications", headers=h))["notifications"]
    return [n["text"] for n in found if n["kind"] == kind]


async def lines(client: AsyncClient, w: dict[str, Any], ticket_id: str) -> list[str]:
    detail = ok(await client.get(f"{T}/{ticket_id}", headers=w["h"]))
    return [m["body"] for m in detail["messages"] if m["kind"] == "system"]


async def audit(client: AsyncClient, w: dict[str, Any], area: str) -> list[dict[str, Any]]:
    page = ok(await client.get(f"/api/v1/audit?area={area}&limit=200", headers=w["h"]))
    entries: list[dict[str, Any]] = page["entries"]
    return entries


async def events(sm: Sm, ws: UUID, ticket_id: str) -> list[dict[str, Any]]:
    async with workspace_session(sm, ws) as s:
        rows = await s.execute(
            text("SELECT event, payload FROM hd_outbox WHERE ticket_id = :t ORDER BY seq"),
            {"t": ticket_id},
        )
        return [dict(r) for r in rows.mappings().all()]


async def sql(sm: Sm, ws: UUID, statement: str, **params: Any) -> Any:
    async with workspace_session(sm, ws) as s:
        result = await s.execute(text(statement), params)
        return result.all() if result.returns_rows else None


def soon(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()
