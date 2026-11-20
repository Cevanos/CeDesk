"""Contacts (feature-gap plan 2.1, 2.2, 2.3, 2.7) with RLS on: the list with search,
filters, sort and paging; one contact; notes (author or Admin changes them); contact
fields and attribute values; the activity timeline; and that another workspace sees none
of it."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, sql

pytestmark = pytest.mark.db

C = "/api/v1/contacts"
F = "/api/v1/contact-fields"


async def customer(
    client: AsyncClient, w: dict[str, Any], name: str, email: str, *, subject: str = "Help"
) -> tuple[str, str]:
    """A ticket from a new customer: (contact id, ticket id)."""
    body = {"subject": subject, "contact": {"email": email, "name": name}}
    t = ok(await client.post(T, headers=w["h"], json=body), 201)
    return str(t["contact"]["id"]), str(t["id"])


async def field(client: AsyncClient, h: dict[str, str], key: str, kind: str, **more: Any) -> Any:
    body = {"key": key, "label": key.title(), "kind": kind, **more}
    return await client.post(F, headers=h, json=body)


async def test_the_list_searches_filters_sorts_and_pages(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ana, ana_t = await customer(client, w, "Ana Silva", "ana@shop.test")
    bo, _ = await customer(client, w, "bo Chen", "bo@corp.test")
    await customer(client, w, "Bo Chen", "bo@corp.test", subject="Again")  # 2 tickets
    cy, _ = await customer(client, w, "Cy", "cy@shop.test")
    ok(await client.patch(f"{C}/{cy}/profile", headers=w["h"], json={"vip": True}))
    ok(await client.patch(f"{T}/{ana_t}", headers=w["h"], json={"status": "resolved"}))
    await sql(
        app_sessionmaker,
        w["ws"],
        "UPDATE hd_contact SET phone = '+91 98450 11111' WHERE id = :c",
        c=ana,
    )

    page = ok(await client.get(C, headers=w["h"]))
    assert [c["name"] for c in page["contacts"]] == ["Ana Silva", "bo Chen", "Cy"]
    row = page["contacts"][1]
    assert (row["ticket_count"], row["open_ticket_count"], row["email"]) == (2, 2, "bo@corp.test")
    by = {c["name"]: c for c in page["contacts"]}
    assert by["Ana Silva"]["open_ticket_count"] == 0

    async def names(**params: Any) -> list[str]:
        found = ok(await client.get(C, headers=w["h"], params=params))["contacts"]
        return [c["name"] for c in found]

    assert await names(q="SHOP") == ["Ana Silva", "Cy"]  # email, any case
    assert await names(q="98450") == ["Ana Silva"]  # phone
    assert await names(q="chen") == ["bo Chen"]
    assert await names(q="100%") == []  # LIKE characters are literal
    assert await names(vip=True) == ["Cy"]
    assert await names(has_open=False) == ["Ana Silva"]
    assert (await names(sort="tickets"))[0] == "bo Chen"
    assert (await names(sort="recent"))[0] == "Ana Silva"  # resolved just now
    assert await names(created_from="2000-01-01", created_to="2000-01-02") == []
    # Paging: two at a time, no repeats, the same set.
    seen: list[str] = []
    cursor = None
    for _ in range(3):
        params: dict[str, Any] = {"limit": 2, "sort": "recent"}
        if cursor:
            params["cursor"] = cursor
        got = ok(await client.get(C, headers=w["h"], params=params))
        seen += [c["id"] for c in got["contacts"]]
        cursor = got["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted([ana, bo, cy])
    # A cursor from another sort, or a bad filter, is a 422.
    first = ok(await client.get(C, headers=w["h"], params={"limit": 1}))
    bad = [
        {"cursor": first["next_cursor"], "sort": "tickets"},
        {"cursor": "nope"},
        {"sort": "random"},
        {"created_from": "2025-02-01", "created_to": "2025-01-01"},
        {"attr": "plan:pro"},
        {"attr": "plan"},
        {"q": "x" * 201},
    ]
    for params in bad:
        assert (await client.get(C, headers=w["h"], params=params)).status_code == 422, params
    # Erased customers are left out of the list.
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_contact SET erased_at = now() WHERE id = :c", c=cy
    )
    assert await names() == ["Ana Silva", "bo Chen"]
    # A Light agent reads the list and a contact.
    meena = await as_(client, w, "Meena")
    assert len(ok(await client.get(C, headers=meena))["contacts"]) == 2
    assert ok(await client.get(f"{C}/{ana}", headers=meena))["name"] == "Ana Silva"


async def test_notes_are_changed_by_their_author_or_an_admin(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ana, _ = await customer(client, w, "Ana", "ana@shop.test")
    neha, ravi, meena = [await as_(client, w, n) for n in ("Neha", "Ravi", "Meena")]
    url = f"{C}/{ana}/notes"
    note = ok(await client.post(url, headers=neha, json={"body": "  Prefers phone calls. "}), 201)
    assert (note["body"], note["author_name"], note["can_edit"]) == (
        "Prefers phone calls.",
        "Neha",
        True,
    )
    # A Light agent adds notes too (as on tickets), but changes nobody else's.
    light = ok(await client.post(url, headers=meena, json={"body": "VIP at the shop"}), 201)
    nid = note["id"]
    r = await client.patch(f"{url}/{nid}", headers=ravi, json={"body": "x"})
    assert r.status_code == 403
    assert (await client.delete(f"{url}/{nid}", headers=meena)).status_code == 403
    assert (await client.post(url, headers=neha, json={"body": ""})).status_code == 422
    assert (await client.post(url, headers=neha, json={"body": "x" * 5001})).status_code == 422
    changed = ok(await client.patch(f"{url}/{nid}", headers=neha, json={"body": "Calls only"}))
    assert changed["body"] == "Calls only"
    # The contact carries its newest notes; the list pages them, newest first.
    detail = ok(await client.get(f"{C}/{ana}", headers=ravi))
    assert detail["note_count"] == 2
    assert [(n["body"], n["can_edit"]) for n in detail["notes"]] == [
        ("VIP at the shop", False),
        ("Calls only", False),
    ]
    first = ok(await client.get(url, headers=w["h"], params={"limit": 1}))
    assert [n["id"] for n in first["notes"]] == [light["id"]]
    rest = ok(await client.get(url, headers=w["h"], params={"cursor": first["next_cursor"]}))
    assert [n["id"] for n in rest["notes"]] == [nid] and rest["next_cursor"] is None
    # An Admin deletes anyone's note.
    assert (await client.delete(f"{url}/{light['id']}", headers=w["h"])).status_code == 204
    assert (await client.delete(f"{url}/{light['id']}", headers=w["h"])).status_code == 404
    entries = await audit(client, w, "contacts")
    assert [e["action"] for e in entries] == [
        "note_deleted",
        "note_changed",
        "note_added",
        "note_added",
    ]
    assert all(e["target"] == f"Customer {ana}" for e in entries)
    assert "phone" not in str(entries).lower() and "shop" not in str(entries).lower()
    # A note on another customer's id path is not found.
    bo, _ = await customer(client, w, "Bo", "bo@shop.test")
    r = await client.patch(f"{C}/{bo}/notes/{nid}", headers=neha, json={"body": "x"})
    assert r.status_code == 404


async def test_contact_fields_and_attribute_values(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ana, _ = await customer(client, w, "Ana", "ana@shop.test")
    bo, _ = await customer(client, w, "Bo", "bo@shop.test")
    sam, neha, meena = [await as_(client, w, n) for n in ("Sam", "Neha", "Meena")]
    # Only Admins define fields.
    assert (await field(client, sam, "plan", "select", options=["free", "pro"])).status_code == 403
    plan = ok(await field(client, w["h"], "plan", "select", options=["free", "pro"]), 201)
    ok(await field(client, w["h"], "seats", "number", position=1), 201)
    ok(await field(client, w["h"], "renews", "date", position=2), 201)
    ok(await field(client, w["h"], "trial", "checkbox", position=3), 201)
    ok(await field(client, w["h"], "notes_url", "text", position=4), 201)
    assert (await field(client, w["h"], "plan", "text")).status_code == 409
    for bad in (
        {"key": "Bad Key", "kind": "text"},
        {"key": "x", "kind": "colour"},
        {"key": "y", "kind": "select"},
        {"key": "z", "kind": "text", "options": ["a"]},
    ):
        r = await client.post(F, headers=w["h"], json={"label": "L", **bad})
        assert r.status_code == 422, bad
    listed = ok(await client.get(F, headers=meena))
    assert [f["key"] for f in listed] == ["plan", "seats", "renews", "trial", "notes_url"]

    url = f"{C}/{ana}/attributes"
    values = {"plan": "pro", "seats": 12, "renews": "2027-01-31", "trial": False}
    got = ok(await client.put(url, headers=neha, json={"attributes": values}))
    assert got["attributes"] == values
    assert [f["key"] for f in got["fields"]] == ["plan", "seats", "renews", "trial", "notes_url"]
    bad_values = [
        {"plan": "gold"},
        {"seats": "twelve"},
        {"seats": True},
        {"renews": "31/01/2027"},
        {"renews": "2027-02-30"},
        {"trial": "yes"},
        {"colour": "red"},
    ]
    for bad_value in bad_values:
        r = await client.put(url, headers=neha, json={"attributes": bad_value})
        assert r.status_code == 422, bad_value
    # A Light agent can't set values.
    assert (await client.put(url, headers=meena, json={"attributes": {}})).status_code == 403
    # Clearing one keeps the others.
    got = ok(await client.put(url, headers=neha, json={"attributes": {"seats": None}}))
    assert got["attributes"] == {"plan": "pro", "renews": "2027-01-31", "trial": False}
    ok(
        await client.put(
            f"{C}/{bo}/attributes",
            headers=neha,
            json={"attributes": {"plan": "free", "trial": True}},
        )
    )

    # The list filters on values, read by the field's kind.
    async def names(*attrs: str) -> list[str]:
        found = ok(await client.get(C, headers=w["h"], params={"attr": list(attrs)}))
        return [c["name"] for c in found["contacts"]]

    assert await names("plan:pro") == ["Ana"]
    assert await names("trial:true") == ["Bo"]
    assert await names("plan:free", "trial:true") == ["Bo"]
    assert await names("renews:2027-01-31") == ["Ana"]
    assert (await client.get(C, headers=w["h"], params={"attr": "seats:many"})).status_code == 422
    # The audit names the fields changed, never the values.
    entries = [e for e in await audit(client, w, "contacts") if e["action"] == "attributes_changed"]
    assert entries[-1]["detail"] == {"fields": ["plan", "renews", "seats", "trial"]}
    assert "pro" not in str([e["detail"] for e in entries])
    # Renaming keeps the key and kind; deleting removes every value.
    pid = plan["id"]
    r = await client.patch(
        f"{F}/{pid}", headers=w["h"], json={"label": "Plan tier", "options": ["free", "pro", "max"]}
    )
    assert ok(r)["label"] == "Plan tier" and ok(r)["kind"] == "select"
    assert (
        await client.patch(f"{F}/{pid}", headers=w["h"], json={"kind": "text"})
    ).status_code == 422
    assert (await client.delete(f"{F}/{pid}", headers=w["h"])).status_code == 204
    assert "plan" not in ok(await client.get(f"{C}/{ana}", headers=w["h"]))["attributes"]
    assert "plan" not in ok(await client.get(f"{C}/{bo}", headers=w["h"]))["attributes"]
    actions = [e["action"] for e in await audit(client, w, "contact_fields")]
    assert actions[:2] == ["deleted", "changed"]


async def test_the_timeline_shows_what_happened_in_order(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ana, t = await customer(client, w, "Ana", "ana@shop.test")
    neha = await as_(client, w, "Neha")
    ok(
        await client.post(
            f"{T}/{t}/messages", headers=w["h"], json={"kind": "reply", "body": "On its way"}
        ),
        201,
    )
    ok(
        await client.post(
            f"{T}/{t}/messages", headers=w["h"], json={"kind": "note", "body": "Secret note"}
        ),
        201,
    )
    ok(await client.patch(f"{T}/{t}", headers=w["h"], json={"status": "resolved"}))
    await sql(
        app_sessionmaker,
        w["ws"],
        "INSERT INTO hd_csat (workspace_id, ticket_id, "
        "contact_id, score, comment, source) VALUES (:w, :t, :c, 5, 'Great', 'email')",
        w=w["ws"],
        t=t,
        c=ana,
    )
    ok(await client.post(f"{C}/{ana}/notes", headers=neha, json={"body": "Loyal customer"}), 201)
    got = ok(await client.get(f"{C}/{ana}/timeline", headers=w["h"]))
    kinds = [i["kind"] for i in got["items"]]
    assert kinds[:3] == ["note", "csat", "ticket_resolved"]
    assert kinds[3:] == ["message", "ticket_created"]  # the reply, then the ticket
    texts = [i["text"] for i in got["items"] if i["kind"] == "message"]
    assert "On its way" in texts and "Secret note" not in texts  # never internal notes
    rating = next(i for i in got["items"] if i["kind"] == "csat")
    assert (rating["score"], rating["text"], rating["ticket_id"]) == (5, "Great", t)
    # Paging: one at a time walks the same items.
    seen, cursor = [], None
    while True:
        params: dict[str, Any] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = ok(await client.get(f"{C}/{ana}/timeline", headers=w["h"], params=params))
        seen += [(i["kind"], i["id"]) for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [(i["kind"], i["id"]) for i in got["items"]]
    only = ok(await client.get(f"{C}/{ana}/timeline", headers=w["h"], params={"kind": "note"}))
    assert [i["kind"] for i in only["items"]] == ["note"]
    r = await client.get(f"{C}/{ana}/timeline", headers=w["h"], params={"kind": "bogus"})
    assert r.status_code == 422


async def test_another_workspace_sees_no_contacts(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    ana, _ = await customer(client, w, "Ana", "ana@shop.test")
    note = ok(await client.post(f"{C}/{ana}/notes", headers=w["h"], json={"body": "Ours"}), 201)
    fid = ok(await field(client, w["h"], "plan", "text"), 201)["id"]
    h = them["h"]
    assert ok(await client.get(C, headers=h))["contacts"] == []
    assert ok(await client.get(F, headers=h)) == []
    for method, path, body in (
        ("GET", f"{C}/{ana}", None),
        ("GET", f"{C}/{ana}/notes", None),
        ("POST", f"{C}/{ana}/notes", {"body": "Theirs"}),
        ("PATCH", f"{C}/{ana}/notes/{note['id']}", {"body": "x"}),
        ("DELETE", f"{C}/{ana}/notes/{note['id']}", None),
        ("PUT", f"{C}/{ana}/attributes", {"attributes": {}}),
        ("GET", f"{C}/{ana}/timeline", None),
        ("PATCH", f"{F}/{fid}", {"label": "x"}),
        ("DELETE", f"{F}/{fid}", None),
    ):
        r = await client.request(method, path, headers=h, json=body)
        assert r.status_code == 404, (method, path)
    assert ok(await client.get(f"{C}/{ana}", headers=w["h"]))["note_count"] == 1
