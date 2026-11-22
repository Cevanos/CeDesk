"""Shared steps for the reporting tests: a desk with people and teams, tickets placed at a
given time, and a member held to one team."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

T = "/api/v1/tickets"


async def sql(sm: Sm, workspace: UUID, query: str, **params: Any) -> list[Any]:
    async with workspace_session(sm, workspace) as s:
        result = await s.execute(text(query), params)
        return list(result.mappings().all()) if getattr(result, "returns_rows", False) else []


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """A seeded workspace (teams Billing, Shipping, ...): Admin, Sam (supervisor), Neha and
    Ravi (agents), Lisa (light agent)."""
    slug = await new_workspace(sm, seed=True)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    ids = {
        "sam": await add(client, token, "sam@example.com", "supervisor", "Sam"),
        "neha": await add(client, token, "neha@example.com", "agent", "Neha"),
        "ravi": await add(client, token, "ravi@example.com", "agent", "Ravi"),
        "lisa": await add(client, token, "lisa@example.com", "light_agent", "Lisa"),
    }
    ids["admin"] = ok(await client.get("/api/v1/me", headers=h))["member"]["id"]
    teams = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/teams", headers=h))}
    return {
        "slug": slug,
        "ws": UUID(token.split(".")[0]),
        "admin": h,
        "sam": bearer(await sign_in(client, slug, "sam@example.com")),
        "neha": bearer(await sign_in(client, slug, "neha@example.com")),
        "lisa": bearer(await sign_in(client, slug, "lisa@example.com")),
        "ids": ids,
        "teams": teams,
    }


async def ticket(
    client: AsyncClient,
    w: dict[str, Any],
    subject: str = "Hello",
    *,
    email: str = "ana@shop.test",
    team: str | None = None,
    priority: str = "medium",
    tags: list[str] | None = None,
    channel: str = "web",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "subject": subject,
        "contact": {"email": email, "name": "Ana"},
        "priority": priority,
        "channel": channel,
        "tags": tags or [],
    }
    if team:
        body["team_id"] = w["teams"][team]
    return ok(await client.post(T, headers=w["admin"], json=body), 201)  # type: ignore[no-any-return]


async def place(
    sm: Sm,
    w: dict[str, Any],
    ticket_id: str,
    *,
    created: datetime | None = None,
    resolved: datetime | None = None,
    first_reply: datetime | None = None,
    source: str | None = None,
    reopen_count: int | None = None,
) -> None:
    """Puts a ticket at the times a test needs (a resolved time makes it resolved)."""
    sets, params = [], {"id": UUID(ticket_id)}
    if created is not None:
        sets.append("created_at = :created")
        params["created"] = created
    if resolved is not None:
        sets += ["status = 'resolved'", "resolved_at = :resolved"]
        params["resolved"] = resolved
    if first_reply is not None:
        sets.append("first_reply_at = :reply")
        params["reply"] = first_reply
    if source is not None:
        sets.append("source = :source")
        params["source"] = source
    if reopen_count is not None:
        sets.append("reopen_count = :reopen")
        params["reopen"] = reopen_count
    await sql(sm, w["ws"], f"UPDATE hd_ticket SET {', '.join(sets)} WHERE id = :id", **params)  # noqa: S608


async def team_only_member(
    client: AsyncClient, w: dict[str, Any], team: str, email: str = "ravi@example.com"
) -> dict[str, str]:
    """Gives a member a custom role with the reports right, held to one team; signs them in."""
    role = ok(
        await client.post(
            "/api/v1/roles",
            headers=w["admin"],
            json={"name": f"{team} reports", "scope": "team_only", "team_id": w["teams"][team],
                  "reports": True},
        ),
        201,
    )  # fmt: skip
    ok(
        await client.patch(
            f"/api/v1/members/{w['ids'][email.split('@')[0]]}",
            headers=w["admin"],
            json={"role": "custom", "role_id": role["id"]},
        )
    )
    return bearer(await sign_in(client, w["slug"], email))


def now() -> datetime:
    return datetime.now(UTC)
