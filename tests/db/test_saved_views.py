"""Saved views (feature-gap plan 1.2) with RLS on: personal views only for their owner,
shared views for everyone and changed only with `share_views`, filters checked with the
ticket list's own rules, audited, and invisible to another workspace."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, ticket

pytestmark = pytest.mark.db

V = "/api/v1/views"


async def make(client: AsyncClient, h: dict[str, str], name: str, filters: dict[str, Any],
               shared: bool = False) -> Any:  # fmt: skip
    return await client.post(V, headers=h, json={"name": name, "filters": filters,
                                                 "shared": shared})  # fmt: skip


async def test_personal_views_are_their_owners_only(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    neha = await as_(client, w, "Neha")
    filters = {"status": "pending", "scope": "mine", "q": "  refund ", "sort": "priority",
               "team": w["teams"]["Billing"], "snoozed": False}  # fmt: skip
    view = ok(await make(client, neha, "My pending", filters), 201)
    # Stored as the list takes them: trimmed, defaults (sort=priority) left out.
    assert view["filters"] == {"status": "pending", "scope": "mine", "q": "refund",
                               "team": w["teams"]["Billing"], "snoozed": False}  # fmt: skip
    assert (view["shared"], view["owner_name"], view["can_edit"]) == (False, "Neha", True)
    assert [v["name"] for v in ok(await client.get(V, headers=neha))] == ["My pending"]
    # Not even an Admin sees, changes or deletes it.
    assert ok(await client.get(V, headers=w["h"])) == []
    vid = view["id"]
    assert (await client.get(f"{V}/{vid}", headers=w["h"])).status_code == 404
    assert (await client.patch(f"{V}/{vid}", headers=w["h"], json={"name": "x"})).status_code == 404
    assert (await client.delete(f"{V}/{vid}", headers=w["h"])).status_code == 404
    # The owner renames it and changes its filters; the same name twice is a 409.
    got = ok(
        await client.patch(
            f"{V}/{vid}", headers=neha, json={"name": "Waiting", "filters": {"status": "open"}}
        )
    )
    assert (got["name"], got["filters"]) == ("Waiting", {"status": "open"})
    assert (await make(client, neha, "waiting", {})).status_code == 409
    # The view's filters work on the ticket list as they are.
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    page = ok(await client.get(T, headers=neha, params=got["filters"]))
    assert [r["id"] for r in page["tickets"]] == [t]
    assert (await client.delete(f"{V}/{vid}", headers=neha)).status_code == 204
    assert ok(await client.get(V, headers=neha)) == []
    actions = [e["action"] for e in await audit(client, w, "views")]
    assert actions == ["deleted", "changed", "created"]


async def test_shared_views_need_the_right(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    neha, sam = await as_(client, w, "Neha"), await as_(client, w, "Sam")
    r = await make(client, neha, "Team queue", {"status": "open"}, shared=True)
    assert r.status_code == 403
    shared = ok(await make(client, sam, "Team queue", {"status": "open"}, shared=True), 201)
    mine = ok(await make(client, neha, "Mine", {"scope": "mine"}), 201)
    seen = ok(await client.get(V, headers=neha))
    assert [(v["name"], v["shared"], v["can_edit"]) for v in seen] == [
        ("Team queue", True, False), ("Mine", False, True)]  # fmt: skip
    sid = shared["id"]
    r = await client.patch(f"{V}/{sid}", headers=neha, json={"name": "Mine now"})
    assert r.status_code == 403
    assert (await client.delete(f"{V}/{sid}", headers=neha)).status_code == 403
    r = await client.patch(f"{V}/{mine['id']}", headers=neha, json={"shared": True})
    assert r.status_code == 403  # an agent can't share
    # The Admin (share_views) changes the Supervisor's shared view; the name is taken
    # among shared views.
    ok(await client.patch(f"{V}/{sid}", headers=w["h"], json={"filters": {"status": "pending"}}))
    assert (await make(client, w["h"], "team QUEUE", {}, shared=True)).status_code == 409
    # Unsharing makes it Sam's personal view again.
    ok(await client.patch(f"{V}/{sid}", headers=sam, json={"shared": False}))
    assert [v["name"] for v in ok(await client.get(V, headers=neha))] == ["Mine"]
    assert [v["name"] for v in ok(await client.get(V, headers=sam))] == ["Team queue"]


async def test_filters_are_checked_like_the_list(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    bad = [
        {"assignee": "x"},  # not a list filter
        {"status": "closed"},
        {"scope": "theirs"},
        {"sort": "random"},
        {"imported": "freshservice"},
        {"snoozed": "yes"},
        {"team": "not-a-uuid"},
        {"team": them["teams"]["Billing"]},  # another workspace's team
        {"q": "x" * 201},
        {"status": 3},
    ]
    for filters in bad:
        r = await make(client, w["h"], "Bad", filters)
        assert r.status_code == 422, filters
    assert (await make(client, w["h"], "", {})).status_code == 422
    r = await client.patch(f"{V}/{ok(await make(client, w['h'], 'Ok', {}), 201)['id']}",
                           headers=w["h"], json={"name": None})  # fmt: skip
    assert r.status_code == 422


async def test_another_workspace_sees_no_views(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    shared = ok(await make(client, w["h"], "Everyone", {"status": "open"}, shared=True), 201)
    assert ok(await client.get(V, headers=them["h"])) == []
    for method in ("get", "delete"):
        r = await getattr(client, method)(f"{V}/{shared['id']}", headers=them["h"])
        assert r.status_code == 404
    r = await client.patch(f"{V}/{shared['id']}", headers=them["h"], json={"name": "Mine"})
    assert r.status_code == 404
    assert ok(await client.get(f"{V}/{shared['id']}", headers=w["h"]))["name"] == "Everyone"
