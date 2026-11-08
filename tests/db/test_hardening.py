"""Regression tests from the Phase 1 audit (B1-B6), against the real database with RLS on.

The concurrency tests call the services directly with `asyncio.gather`: each call opens
its own pooled connection, so the transactions really overlap in Postgres.
"""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.service import auth, members, tickets
from cedesk.helpdesk.service.errors import ServiceError
from cedesk.helpdesk.service.tickets import NewTicket
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
CONTACT = {"email": "ana@shop.test", "name": "Ana"}


def lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


async def caller(sm: Sm, c: AsyncClient, slug: str, email: str) -> Caller:
    return await auth.resolve(sm, await sign_in(c, slug, email))


async def team(c: AsyncClient, h: dict[str, str], name: str, ids: list[str], **kw: Any) -> str:
    body = {"name": name, "member_ids": ids, **kw}
    return ok(await c.post("/api/v1/teams", headers=h, json=body), 201)["id"]  # type: ignore[no-any-return]


# B1: assignment through a team change


async def test_agent_cannot_move_another_agents_ticket_between_teams(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    a = await sign_in(client, slug, "admin@example.com")
    h = bearer(a)
    ravi = await add(client, a, "ravi@example.com", "agent", "Ravi")
    neha = await add(client, a, "neha@example.com", "agent", "Neha")
    await add(client, a, "sup@example.com", "supervisor", "Sup")
    agent = bearer(await sign_in(client, slug, "neha@example.com"))
    sup = bearer(await sign_in(client, slug, "sup@example.com"))
    billing = await team(client, h, "Billing", [ravi])
    sales = await team(client, h, "Sales", [neha])
    empty = await team(client, h, "Empty", [], auto_assign=False)

    ravis = ok(
        await client.post(
            T, headers=h, json={"subject": "a", "contact": CONTACT, "team_id": billing}
        ),
        201,
    )
    assert ravis["assignee_name"] == "Ravi"
    url = f"{T}/{ravis['id']}"
    for target in (sales, empty):
        r = await client.patch(url, headers=agent, json={"team_id": target})
        assert r.status_code == 403, target
    still = ok(await client.get(url, headers=h))
    assert (still["assignee_name"], still["team_name"]) == ("Ravi", "Billing")

    # Keeping the assignee while moving the team changes no assignment: allowed.
    kept = ok(await client.patch(url, headers=agent, json={"team_id": sales, "assignee_id": ravi}))
    assert (kept["assignee_name"], kept["team_name"]) == ("Ravi", "Sales")

    # An agent may still send their own or an unassigned ticket to another team.
    own = ok(
        await client.post(
            T, headers=agent, json={"subject": "b", "contact": CONTACT, "assignee_id": neha}
        ),
        201,
    )
    moved = ok(await client.patch(f"{T}/{own['id']}", headers=agent, json={"team_id": billing}))
    assert moved["assignee_name"] == "Ravi"
    assert lines(moved)[-1].startswith("Auto-assigned to Ravi (Billing")

    # A Supervisor may move anyone's ticket.
    back = ok(await client.patch(f"{T}/{own['id']}", headers=sup, json={"team_id": sales}))
    assert back["assignee_name"] == "Neha"


# B2 and B3: custom fields after the ticket type changes


async def test_existing_tickets_stay_editable_after_a_field_schema_change(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    h = bearer(await sign_in(client, slug, "admin@example.com"))
    fields = [
        {"key": "order_id", "label": "Order ID", "kind": "text"},
        {"key": "note", "label": "Note", "kind": "text"},
        {"key": "reason", "label": "Reason", "kind": "select", "options": ["Damaged", "Late"]},
    ]
    ty = ok(
        await client.post(
            "/api/v1/ticket-types", headers=h, json={"name": "Refund", "fields": fields}
        ),
        201,
    )["id"]
    t = ok(
        await client.post(
            T,
            headers=h,
            json={
                "subject": "s",
                "contact": CONTACT,
                "type_id": ty,
                "fields": {"order_id": "A-100", "reason": "Late"},
            },
        ),
        201,
    )
    url = f"{T}/{t['id']}"

    # Order ID becomes a number, "Late" is no longer an option, and a required field appears.
    fields[0]["kind"] = "number"
    fields[2]["options"] = ["Damaged"]
    fields.append({"key": "ref", "label": "Ref", "kind": "text", "required": True})
    ok(await client.patch(f"/api/v1/ticket-types/{ty}", headers=h, json={"fields": fields}))

    edited = ok(await client.patch(url, headers=h, json={"fields": {"note": "hi"}}))
    assert edited["fields"] == {"order_id": "A-100", "reason": "Late", "note": "hi"}
    assert lines(edited)[-1] == "Note set to hi by Admin"
    ok(await client.patch(url, headers=h, json={"priority": "high"}))

    # Changed values are still checked against the new schema.
    bad = await client.patch(url, headers=h, json={"fields": {"order_id": "A-100x"}})
    assert bad.status_code == 422
    assert bad.json()["error"]["message"] == "Order ID must be a number."
    fixed = ok(await client.patch(url, headers=h, json={"fields": {"order_id": 100}}))
    assert fixed["fields"]["order_id"] == 100
    ok(await client.patch(url, headers=h, json={"fields": {"ref": "R1"}}))
    cleared = await client.patch(url, headers=h, json={"fields": {"ref": None}})
    assert cleared.status_code == 422
    assert cleared.json()["error"]["message"] == "Ref is required."


async def test_type_change_records_every_cleared_field(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    h = bearer(await sign_in(client, slug, "admin@example.com"))
    refund_fields = [
        {"key": "order_id", "label": "Order ID", "kind": "text"},
        {"key": "amount", "label": "Amount", "kind": "number"},
        {"key": "reason", "label": "Reason", "kind": "select", "options": ["Damaged", "Late"]},
    ]
    exchange_fields = [
        {"key": "order_id", "label": "Order ID", "kind": "text"},
        {"key": "reason", "label": "Reason", "kind": "select", "options": ["Damaged"]},
        {"key": "size", "label": "New size", "kind": "text", "required": True},
    ]
    types = "/api/v1/ticket-types"
    refund = ok(
        await client.post(types, headers=h, json={"name": "Refund", "fields": refund_fields}), 201
    )["id"]
    exchange = ok(
        await client.post(types, headers=h, json={"name": "Exchange", "fields": exchange_fields}),
        201,
    )["id"]
    t = ok(
        await client.post(
            T,
            headers=h,
            json={
                "subject": "s",
                "contact": CONTACT,
                "type_id": refund,
                "fields": {"order_id": "A-1", "amount": 5, "reason": "Late"},
            },
        ),
        201,
    )
    url = f"{T}/{t['id']}"

    missing = await client.patch(url, headers=h, json={"type_id": exchange})
    assert missing.status_code == 422
    assert missing.json()["error"]["message"] == "New size is required."

    changed = ok(
        await client.patch(url, headers=h, json={"type_id": exchange, "fields": {"size": "M"}})
    )
    assert changed["fields"] == {"order_id": "A-1", "size": "M"}
    assert lines(changed)[-4:] == [
        "Type set to Exchange by Admin",
        "Amount cleared by Admin (not in Exchange)",
        "Reason cleared by Admin (not valid for Exchange)",
        "New size set to M by Admin",
    ]

    untyped = ok(await client.patch(url, headers=h, json={"type_id": None}))
    assert untyped["fields"] == {}
    assert lines(untyped)[-3:] == [
        "Type cleared by Admin",
        "Order ID cleared by Admin (type cleared)",
        "New size cleared by Admin (type cleared)",
    ]


# Concurrent contact creation


async def test_parallel_tickets_for_a_new_email_share_one_contact(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await caller(app_sessionmaker, client, slug, "admin@example.com")
    new = NewTicket(subject="Hi", contact_email="New.Person@Shop.test", contact_name="New")
    views = await asyncio.gather(
        *[tickets.create_ticket(app_sessionmaker, admin, new) for _ in range(6)]
    )
    assert len({v.ticket["contact_id"] for v in views}) == 1
    last = await tickets.get_ticket(app_sessionmaker, admin, views[0].ticket["id"])
    assert last.other_tickets == 5
    assert last.ticket["contact_email"] == "new.person@shop.test"


# Cross-workspace routing updates


async def test_routing_of_another_workspaces_agent_is_not_found(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug_a, slug_b = await new_workspace(app_sessionmaker), await new_workspace(app_sessionmaker)
    a = bearer(await sign_in(client, slug_a, "admin@example.com"))
    b_token = await sign_in(client, slug_b, "admin@example.com")
    b_agent = await add(client, b_token, "x@example.com", "agent", "X")

    r = await client.patch(
        f"/api/v1/agents/{b_agent}/routing",
        headers=a,
        json={"status": "offline", "cap_email": 0, "skills": ["evil"]},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NotFound"
    theirs = {x["id"]: x for x in ok(await client.get("/api/v1/agents", headers=bearer(b_token)))}
    assert (theirs[b_agent]["status"], theirs[b_agent]["cap_email"]) == ("available", 5)
    assert theirs[b_agent]["skills"] == []
    assert b_agent not in {x["id"] for x in ok(await client.get("/api/v1/agents", headers=a))}


# B5: Admin role changes at the same moment


async def _admins(sm: Sm, c: Caller) -> int:
    rows = await members.list_members(sm, c, after=None, limit=200)
    return sum(1 for r in rows if r["role"] == "admin" and r["is_active"])


@pytest.mark.parametrize("change", [{"role": Role.agent}, {"is_active": False}])
async def test_two_admins_removing_each_other_leave_one_admin(
    client: AsyncClient, app_sessionmaker: Sm, change: dict[str, Any]
) -> None:
    for _ in range(5):
        slug = await new_workspace(app_sessionmaker)
        a1 = await caller(app_sessionmaker, client, slug, "admin@example.com")
        a2_id = UUID(
            await add(
                client, await sign_in(client, slug, "admin@example.com"), "a2@example.com", "admin"
            )
        )
        a2 = await caller(app_sessionmaker, client, slug, "a2@example.com")
        kw = {"name": None, "role": None, "is_active": None, **change}
        results = await asyncio.gather(
            members.change_member(app_sessionmaker, a1, a2_id, **kw),
            members.change_member(app_sessionmaker, a2, a1.member_id, **kw),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        assert len(errors) == 1, results
        # A clean refusal (403 or 409), never a database error or a 500.
        assert isinstance(errors[0], ServiceError), repr(errors[0])
        assert errors[0].status in (403, 409)
        survivor = a2 if results[0] is errors[0] else a1
        assert await _admins(app_sessionmaker, survivor) == 1


async def test_last_active_admin_cannot_remove_themselves(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, "admin@example.com")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))["member"]["id"]
    for body in ({"role": "agent"}, {"is_active": False}):
        r = await client.patch(f"/api/v1/members/{me}", headers=bearer(token), json=body)
        assert r.status_code == 403
    assert ok(await client.get("/api/v1/me", headers=bearer(token)))["member"]["role"] == "admin"


async def test_many_parallel_member_changes_never_deadlock(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    # Promotions and demotions at once: the set of Admins changes while others lock it.
    roles = (Role.agent, Role.supervisor, Role.admin)
    for _ in range(5):
        slug = await new_workspace(app_sessionmaker)
        token = await sign_in(client, slug, "admin@example.com")
        ids = [UUID(await add(client, token, f"m{i}@example.com", "admin")) for i in range(4)]
        people = [
            await caller(app_sessionmaker, client, slug, f"m{i}@example.com") for i in range(4)
        ]
        jobs = [
            members.change_member(
                app_sessionmaker,
                people[i],
                ids[(i + k) % 4],
                name=None,
                role=roles[(i + k) % 3],
                is_active=None,
            )
            for i in range(4)
            for k in (1, 2, 3)
        ]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                assert isinstance(r, ServiceError) and r.status in (403, 409), repr(r)
        admin = await caller(app_sessionmaker, client, slug, "admin@example.com")
        assert await _admins(app_sessionmaker, admin) >= 1


# B6: one agent in two teams, routed at the same moment


@pytest.mark.parametrize(
    ("routing", "max_open", "reason"),
    [({"cap_email": 1}, 5, "is full on Email (1)"), ({"cap_email": 5}, 1, "has 1 open")],
)
async def test_parallel_routing_across_teams_respects_limits(
    client: AsyncClient,
    app_sessionmaker: Sm,
    routing: dict[str, int],
    max_open: int,
    reason: str,
) -> None:
    for _ in range(5):
        slug = await new_workspace(app_sessionmaker)
        token = await sign_in(client, slug, "admin@example.com")
        h = bearer(token)
        x = await add(client, token, "x@example.com", "agent", "X")
        ok(await client.patch(f"/api/v1/agents/{x}/routing", headers=h, json=routing))
        teams = [await team(client, h, n, [x], max_open_per_agent=max_open) for n in ("A", "B")]
        admin = await caller(app_sessionmaker, client, slug, "admin@example.com")
        new = NewTicket(subject="p", contact_email="c@x.io", contact_name="C")
        made = [await tickets.create_ticket(app_sessionmaker, admin, new) for _ in teams]
        moved = await asyncio.gather(
            *[
                tickets.change_ticket(
                    app_sessionmaker, admin, v.ticket["id"], {"team_id": UUID(tm)}
                )
                for v, tm in zip(made, teams, strict=True)
            ]
        )
        assigned = [v for v in moved if v.ticket["assignee_id"] == UUID(x)]
        assert len(assigned) == 1
        other = next(v for v in moved if v.ticket["assignee_id"] is None)
        assert reason in other.messages[-1]["body"]


# Database hardening: session key, workspace delete, session clean-up


def _workspace_of(token: str) -> UUID:
    return UUID(token.split(".", 1)[0])


async def test_a_session_cannot_point_at_another_workspaces_member(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from cedesk.db import workspace_session

    a = await sign_in(client, await new_workspace(app_sessionmaker), "admin@example.com")
    b = await sign_in(client, await new_workspace(app_sessionmaker), "admin@example.com")
    b_member = ok(await client.get("/api/v1/me", headers=bearer(b)))["member"]["id"]
    with pytest.raises(IntegrityError, match="auth_session_member_id_fkey"):
        async with workspace_session(app_sessionmaker, _workspace_of(a)) as s:
            await s.execute(
                text(
                    "INSERT INTO auth_session (workspace_id, member_id, token_hash, expires_at) "
                    "VALUES (:ws, :m, :h, now() + interval '1 hour')"
                ),
                {"ws": _workspace_of(a), "m": b_member, "h": b"\x00" * 32},
            )


async def test_ended_sessions_are_pruned(client: AsyncClient, app_sessionmaker: Sm) -> None:
    from sqlalchemy import text

    from cedesk.db import workspace_session

    slug = await new_workspace(app_sessionmaker)
    tokens = [await sign_in(client, slug, "admin@example.com") for _ in range(4)]
    ws = _workspace_of(tokens[0])
    for t in tokens[:3]:
        assert (await client.post("/api/v1/auth/sign-out", headers=bearer(t))).status_code == 204

    async def ids() -> list[UUID]:
        async with workspace_session(app_sessionmaker, ws) as s:
            rows = await s.execute(text("SELECT id FROM auth_session ORDER BY created_at"))
            return [UUID(str(r)) for r in rows.scalars().all()]

    old_out, old_expired, recent_out, live = await ids()
    async with workspace_session(app_sessionmaker, ws) as s:
        await s.execute(
            text("UPDATE auth_session SET revoked_at = now() - interval '8 days' WHERE id = :id"),
            {"id": old_out},
        )
        await s.execute(
            text(
                "UPDATE auth_session SET revoked_at = NULL, "
                "expires_at = now() - interval '8 days' WHERE id = :id"
            ),
            {"id": old_expired},
        )

    deleted = await auth.prune_ended_sessions(app_sessionmaker)
    assert deleted >= 2
    assert await ids() == [recent_out, live]
    assert (await client.get("/api/v1/me", headers=bearer(tokens[3]))).status_code == 200

    # A sign-in also tidies that member's long-ended sessions.
    async with workspace_session(app_sessionmaker, ws) as s:
        await s.execute(
            text("UPDATE auth_session SET revoked_at = now() - interval '9 days' WHERE id = :id"),
            {"id": recent_out},
        )
    await sign_in(client, slug, "admin@example.com")
    remaining = await ids()
    assert recent_out not in remaining and live in remaining and len(remaining) == 2
