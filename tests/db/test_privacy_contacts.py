"""Privacy export and erase (md S11) cover the contact data of feature-gap plan phase 2:
notes, attribute values, addresses merged in and the merge history. The export holds
them; the erase deletes them (and scrubs each merged-in address as the customer's own),
and the erased customer drops out of the contact list, segments and the CSV export."""

import json
import os
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.storage import ObjectStore
from tests.db.helpers import Sm, ok
from tests.db.test_privacy import ANA, P, app_parts, client, customer_desk, sql, store

pytestmark = pytest.mark.db
__all__ = ["app_parts", "client", "store"]  # fixtures

C = "/api/v1/contacts"
OLD = "ana.old@mail.test"


async def with_contact_data(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Ana with a note, a plan, and a duplicate (her old address) merged into her."""
    w = await customer_desk(client, sm)
    h = w["admin"]
    ok(
        await client.post(
            "/api/v1/contact-fields",
            headers=h,
            json={"key": "plan", "label": "Plan", "kind": "text"},
        ),
        201,
    )
    ok(
        await client.put(
            f"{C}/{w['contact']}/attributes",
            headers=h,
            json={"attributes": {"plan": "Gold, card ending 4242"}},
        )
    )
    ok(
        await client.post(
            f"{C}/{w['contact']}/notes", headers=h, json={"body": "Lives at 12 Hill Rd"}
        ),
        201,
    )
    old = ok(
        await client.post(
            "/api/v1/tickets",
            headers=h,
            json={"subject": "Old account", "contact": {"email": OLD, "name": "Ana Old"}},
        ),
        201,
    )
    ok(
        await client.post(
            f"{C}/{w['contact']}/merge",
            headers=h,
            json={"duplicate_id": old["contact"]["id"], "confirm": True},
        )
    )
    await sql(
        sm,
        w,
        "INSERT INTO hd_portal_code (workspace_id, email, code_hash, expires_at) "
        "VALUES (:ws, :e, :h, now() + interval '1 hour')",
        ws=w["ws"],
        e=OLD,
        h=os.urandom(32),
    )
    return w


async def test_the_export_holds_the_contact_data(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    w = await with_contact_data(client, app_sessionmaker)
    # Found by the merged-in address too.
    made = ok(await client.post(f"{P}/export", headers=w["admin"], json={"email": OLD}), 201)
    assert made["contact_id"] == w["contact"]
    data = json.loads(
        (await client.get(f"{P}/requests/{made['id']}/file", headers=w["admin"])).content
    )
    customer = data["customer"]
    assert customer["other_emails"] == [OLD]
    assert customer["attributes"] == [
        {"key": "plan", "label": "Plan", "value": "Gold, card ending 4242"}
    ]
    assert [n["body"] for n in data["contact_notes"]] == ["Lives at 12 Hill Rd"]
    assert data["contact_notes"][0]["author"] == "Admin"
    assert [(m["name"], m["email"]) for m in data["merged_customers"]] == [("Ana Old", OLD)]
    assert {t["subject"] for t in data["tickets"]} == {"Broken parcel", "Old account"}


async def test_erasing_removes_the_contact_data(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    sm = app_sessionmaker
    w = await with_contact_data(client, sm)
    erase = {"contact_id": w["contact"], "confirm_email": ANA}
    done = ok(await client.post(f"{P}/erase", headers=w["admin"], json=erase), 201)
    counts = done["counts"]
    assert (counts["contact_notes"], counts["other_emails"], counts["attributes"]) == (1, 1, 1)
    assert counts["merge_history"] == 1
    assert counts["portal_codes"] == 2  # her own address's code and the old one's
    c = w["contact"]
    assert (await sql(sm, w, "SELECT count(*) AS n FROM hd_contact_note"))[0]["n"] == 0
    assert (await sql(sm, w, "SELECT count(*) AS n FROM hd_contact_alias"))[0]["n"] == 0
    [row] = await sql(sm, w, "SELECT attributes FROM hd_contact WHERE id = :c", c=c)
    assert row["attributes"] == {}
    [merge] = await sql(sm, w, "SELECT merged_name, merged_email, moved FROM hd_contact_merge")
    assert (merge["merged_name"], merge["merged_email"]) == (None, None)
    assert merge["moved"]["tickets"] == 1  # the merge itself stays, as counts
    # Nothing in the database names either address or the note's words any more.
    for table in (
        "hd_contact",
        "hd_contact_alias",
        "hd_contact_merge",
        "hd_portal_code",
        "hd_contact_note",
    ):
        rows = await sql(sm, w, f"SELECT * FROM {table}")  # noqa: S608 (test constants)
        text = json.dumps([dict(r) for r in rows], default=str).lower()
        assert ANA not in text and OLD not in text and "hill rd" not in text, table
    # A new email from the old address no longer finds her: it is a new customer.
    new = ok(
        await client.post(
            "/api/v1/tickets",
            headers=w["admin"],
            json={"subject": "Hello", "contact": {"email": OLD, "name": "Someone"}},
        ),
        201,
    )
    assert new["contact"]["id"] != c
    # She is out of the contact list and the CSV export.
    listed = ok(await client.get(C, headers=w["admin"]))["contacts"]
    assert c not in [x["id"] for x in listed]
    exported = (await client.get(f"{C}/export", headers=w["admin"])).content.decode()
    assert c not in exported
    # Her notes can't be added to any more.
    r = await client.post(f"{C}/{c}/notes", headers=w["admin"], json={"body": "x"})
    assert r.status_code == 422
    # Nor her card renamed or made VIP: it stays as the erase left it (both were allowed).
    [before] = await sql(sm, w, "SELECT name, vip FROM hd_contact WHERE id = :c", c=c)
    for change in ({"name": "Ana Hill"}, {"vip": True}):
        r = await client.patch(f"{C}/{c}/profile", headers=w["admin"], json=change)
        assert (r.status_code, r.json()["error"]["message"]) == (422, "This customer was erased.")
    assert (await sql(sm, w, "SELECT name, vip FROM hd_contact WHERE id = :c", c=c)) == [before]
