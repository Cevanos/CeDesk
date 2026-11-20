"""Segments (feature-gap plan 2.6) with RLS on: saved contact filters checked with the
list's own rules, made by Admins and Supervisors, read by everyone, worked out when read
(erased customers never in one), audited, a field a segment uses can't be deleted, and
another workspace sees none."""

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import as_, audit, desk, sql
from tests.db.test_contacts import C, F, customer, field

pytestmark = pytest.mark.db

S = "/api/v1/segments"


async def test_segments_list_their_customers(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    plan = ok(await field(client, w["h"], "plan", "select", options=["free", "pro"]), 201)
    ana, _ = await customer(client, w, "Ana", "ana@shop.test")
    bo, _ = await customer(client, w, "Bo", "bo@shop.test")
    cy, _ = await customer(client, w, "Cy", "cy@shop.test")
    for who in (ana, bo):
        ok(
            await client.put(
                f"{C}/{who}/attributes", headers=w["h"], json={"attributes": {"plan": "pro"}}
            )
        )
    ok(await client.patch(f"{C}/{bo}/profile", headers=w["h"], json={"vip": True}))
    sam, neha = await as_(client, w, "Sam"), await as_(client, w, "Neha")
    body = {"name": "Pro customers", "filters": {"attributes": {"plan": "pro"}, "q": " shop "}}
    assert (await client.post(S, headers=neha, json=body)).status_code == 403
    seg = ok(await client.post(S, headers=sam, json=body), 201)
    assert seg["filters"] == {"q": "shop", "attributes": {"plan": "pro"}}
    assert (
        await client.post(S, headers=w["h"], json={**body, "name": "PRO customers"})
    ).status_code == 409
    for bad in (
        {"plan": "pro"},
        {"vip": "yes"},
        {"attributes": {"colour": "red"}},
        {"attributes": {"plan": "gold"}},
        {"created_from": "soon"},
        {"sort": "random"},
    ):
        r = await client.post(S, headers=sam, json={"name": "Bad", "filters": bad})
        assert r.status_code == 422, bad
    # Everyone reads it; who is in it is worked out now.
    detail = ok(await client.get(f"{S}/{seg['id']}", headers=neha))
    assert detail["contact_count"] == 2
    got = ok(await client.get(f"{S}/{seg['id']}/contacts", headers=neha))
    assert [c["name"] for c in got["contacts"]] == ["Ana", "Bo"]
    assert got["segment"]["name"] == "Pro customers"
    first = ok(await client.get(f"{S}/{seg['id']}/contacts", headers=neha, params={"limit": 1}))
    rest = ok(
        await client.get(
            f"{S}/{seg['id']}/contacts", headers=neha, params={"cursor": first["next_cursor"]}
        )
    )
    assert [c["id"] for c in first["contacts"] + rest["contacts"]] == [ana, bo]
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_contact SET erased_at = now() WHERE id = :c", c=ana
    )
    got = ok(await client.get(f"{S}/{seg['id']}/contacts", headers=neha))
    assert [c["id"] for c in got["contacts"]] == [bo]
    # Change it; the field it uses can't be deleted while it does.
    assert (await client.delete(f"{F}/{plan['id']}", headers=w["h"])).status_code == 409
    changed = ok(
        await client.patch(f"{S}/{seg['id']}", headers=sam, json={"filters": {"vip": False}})
    )
    assert changed["filters"] == {"vip": False}
    names = [
        c["id"] for c in ok(await client.get(f"{S}/{seg['id']}/contacts", headers=neha))["contacts"]
    ]
    assert names == [cy]
    assert (await client.delete(f"{F}/{plan['id']}", headers=w["h"])).status_code == 204
    assert (
        await client.patch(f"{S}/{seg['id']}", headers=sam, json={"name": None})
    ).status_code == 422
    assert [s["name"] for s in ok(await client.get(S, headers=neha))] == ["Pro customers"]
    assert (await client.delete(f"{S}/{seg['id']}", headers=neha)).status_code == 403
    assert (await client.delete(f"{S}/{seg['id']}", headers=sam)).status_code == 204
    entries = await audit(client, w, "segments")
    assert [e["action"] for e in entries] == ["deleted", "changed", "created"]
    assert entries[-1]["detail"]["filters"] == {"search": True, "attributes": {"plan": "pro"}}


async def test_another_workspace_sees_no_segments(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    await customer(client, w, "Ana", "ana@shop.test")
    seg = ok(await client.post(S, headers=w["h"], json={"name": "All", "filters": {}}), 201)
    assert ok(await client.get(S, headers=them["h"])) == []
    for method, path, body in (
        ("GET", f"{S}/{seg['id']}", None),
        ("GET", f"{S}/{seg['id']}/contacts", None),
        ("PATCH", f"{S}/{seg['id']}", {"name": "Mine"}),
        ("DELETE", f"{S}/{seg['id']}", None),
    ):
        r = await client.request(method, path, headers=them["h"], json=body)
        assert r.status_code == 404, (method, path)
    # The same filters in their workspace list only their customers.
    theirs = ok(await client.post(S, headers=them["h"], json={"name": "All", "filters": {}}), 201)
    got = ok(await client.get(f"{S}/{theirs['id']}/contacts", headers=them["h"]))
    assert got["contacts"] == []
