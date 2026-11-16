"""Handing a ticket to someone by hand rings their bell (plan 15.3, bug B-24): the timeline line
was written but the new assignee was never told, because only routing sent the 'assigned'
notification. Nobody is told about their own choice, and unassigning tells no one."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """An empty workspace (no teams, so routing never runs) with an Admin, a Supervisor and
    two agents."""
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    return {
        "slug": slug,
        "h": bearer(admin),
        "sup": await add(client, admin, "sup@example.com", "supervisor", "Sup"),
        "neha": await add(client, admin, "neha@example.com", "agent", "Neha"),
        "chetan": await add(client, admin, "chetan@example.com", "agent", "Chetan"),
    }


async def ticket(client: AsyncClient, h: dict[str, str], **extra: Any) -> dict[str, Any]:
    body = {"subject": "Refund", "contact": {"email": "priya@example.com", "name": "Priya"}}
    created: dict[str, Any] = ok(await client.post(T, headers=h, json=body | extra), 201)
    return created


async def assigned_bells(client: AsyncClient, w: dict[str, Any], email: str) -> list[str]:
    """The texts of this person's 'assigned' notifications (other kinds are ignored: shared
    steps also write alerts and replies)."""
    token = await sign_in(client, w["slug"], email)
    notes = ok(await client.get("/api/v1/notifications", headers=bearer(token)))["notifications"]
    return [n["text"] for n in notes if n["kind"] == "assigned"]


async def test_an_admin_handing_a_ticket_over_rings_the_new_assignees_bell(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w["h"])
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"assignee_id": w["neha"]}))
    assert await assigned_bells(client, w, "neha@example.com") == [
        f"Admin assigned you {t['label']}: Refund"
    ]


async def test_a_supervisor_moving_it_tells_only_the_new_person(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w["h"], assignee_id=w["neha"])
    assert len(await assigned_bells(client, w, "neha@example.com")) == 1  # told when it was made
    sup = bearer(await sign_in(client, w["slug"], "sup@example.com"))
    ok(await client.patch(f"{T}/{t['id']}", headers=sup, json={"assignee_id": w["chetan"]}))
    assert await assigned_bells(client, w, "chetan@example.com") == [
        f"Sup assigned you {t['label']}: Refund"
    ]
    assert len(await assigned_bells(client, w, "neha@example.com")) == 1  # nothing new for her


async def test_taking_a_ticket_yourself_rings_no_bell(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w["h"])
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    ok(await client.patch(f"{T}/{t['id']}", headers=neha, json={"assignee_id": w["neha"]}))
    assert await assigned_bells(client, w, "neha@example.com") == []


async def test_unassigning_or_changing_nothing_rings_no_bell(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w["h"], assignee_id=w["neha"])
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"assignee_id": w["neha"]}))
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"assignee_id": None}))
    assert len(await assigned_bells(client, w, "neha@example.com")) == 1  # only the first one
    assert await assigned_bells(client, w, "chetan@example.com") == []


async def test_creating_a_ticket_for_someone_rings_their_bell(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w["h"], assignee_id=w["chetan"])
    assert await assigned_bells(client, w, "chetan@example.com") == [
        f"Admin assigned you {t['label']}: Refund"
    ]
