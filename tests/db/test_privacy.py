"""Privacy export and erase for one customer (md S11) through the real API with RLS on:
the export holds everything, the erase leaves nothing that names the customer but keeps
every count, only Admins can do either, and both are audited without the address."""

import json
import os
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service import privacy
from cedesk.storage import ObjectMissing, ObjectStore
from tests.db.helpers import Sm, ok
from tests.db.test_message_files import app_parts, client, desk, send, store, upload

pytestmark = pytest.mark.db
__all__ = ["app_parts", "client", "store"]  # fixtures

P = "/api/v1/privacy"
ANA = "ana@shop.test"


async def sql(sm: Sm, w: dict[str, Any], query: str, **params: Any) -> list[Any]:
    async with workspace_session(sm, w["ws"]) as s:
        result = await s.execute(text(query), params)
        return list(result.mappings().all()) if result.returns_rows else []


async def customer_desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Ana's emailed ticket with a reply and a file, a note, a rating, a portal sign-in."""
    w = await desk(client, sm, channel="email")
    photo = await upload(client, w, "ana-passport.jpg")
    ok(await send(client, w, [photo], body="Hi Ana, refund on its way to 12 Hill Rd"), 201)
    ok(await send(client, w, [], kind="note", body="Ana called, angry"), 201)
    contact = w["ticket"]["contact"]["id"]
    await sql(sm, w, "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, comment, "
              "source, rated_at) VALUES (:ws, :t, :c, 2, 'Ana here: slow', 'email', now())",
              ws=w["ws"], t=w["ticket"]["id"], c=contact)  # fmt: skip
    await sql(sm, w, "INSERT INTO hd_portal_session (workspace_id, contact_id, token_hash, "
              "expires_at) VALUES (:ws, :c, :h, now() + interval '1 day')",
              ws=w["ws"], c=contact, h=os.urandom(32))  # fmt: skip
    await sql(sm, w, "INSERT INTO hd_portal_code (workspace_id, email, code_hash, expires_at) "
              "VALUES (:ws, :e, :h, now() + interval '1 hour')",
              ws=w["ws"], e=ANA, h=os.urandom(32))  # fmt: skip
    # A CSV import keys her by her address.
    await sql(sm, w, "INSERT INTO hd_import_map (workspace_id, source, kind, external_id, "
              "local_id) VALUES (:ws, 'csv', 'contact', :e, :c)",
              ws=w["ws"], e=ANA, c=contact)  # fmt: skip
    return {**w, "contact": contact, "photo": photo}


async def test_the_export_holds_everything(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    w = await customer_desk(client, app_sessionmaker)
    for body in ({"email": "nobody@shop.test"}, {"contact_id": w["contact"], "email": ANA}, {}):
        assert (await client.post(f"{P}/export", headers=w["admin"], json=body)).status_code in (
            404,
            422,
        )
    made = ok(
        await client.post(f"{P}/export", headers=w["admin"], json={"email": "ANA@Shop.test"}), 201
    )
    assert (made["kind"], made["contact_id"], made["file_ready"]) == ("export", w["contact"], True)
    assert made["counts"] == {"tickets": 1, "messages": 4}

    r = await client.get(f"{P}/requests/{made['id']}/file", headers=w["admin"])
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = json.loads(r.content)
    assert data["customer"]["email"] == ANA and data["customer"]["name"] == "Ana"
    [ticket] = data["tickets"]
    assert ticket["subject"] == "Broken parcel"
    assert [(m["kind"], m["text"]) for m in ticket["messages"]] == [
        ("customer", "It arrived broken."),
        ("system", "Ticket created by Admin"),
        ("agent", "Hi Ana, refund on its way to 12 Hill Rd"),
        ("note", "Ana called, angry"),
    ]
    assert ticket["messages"][2]["files"] == [
        {"name": "ana-passport.jpg", "size": 5, "type": "image/jpeg"}
    ]
    assert ticket["rating"]["score"] == 2 and ticket["rating"]["comment"] == "Ana here: slow"

    # Agents can't export, list or download.
    for method, path in (("POST", f"{P}/export"), ("GET", f"{P}/requests"),
                         ("GET", f"{P}/requests/{made['id']}/file")):  # fmt: skip
        r = await client.request(method, path, headers=w["neha"], json={"email": ANA})
        assert r.status_code == 403
    listed = ok(await client.get(f"{P}/requests", headers=w["admin"]))
    assert [x["id"] for x in listed] == [made["id"]]
    [entry] = ok(await client.get("/api/v1/audit", headers=w["admin"], params={"area": "privacy"}))[
        "entries"
    ]
    assert (entry["action"], entry["target"]) == ("exported", f"Customer {w['contact']}")
    assert ANA not in json.dumps(entry)

    # After 7 days the worker removes the file.
    await sql(app_sessionmaker, w, "UPDATE hd_privacy_request SET expires_at = now() - "
              "interval '1 second' WHERE id = :id", id=made["id"])  # fmt: skip
    assert await privacy.remove_expired_exports(app_sessionmaker, store) >= 1
    with pytest.raises(ObjectMissing):
        await store.get(f"{w['ws']}/privacy/{made['id']}.json")
    assert (
        await client.get(f"{P}/requests/{made['id']}/file", headers=w["admin"])
    ).status_code == 409


async def test_erase_leaves_nothing_that_names_the_customer(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    sm = app_sessionmaker
    w = await customer_desk(client, sm)
    export = ok(await client.post(f"{P}/export", headers=w["admin"], json={"email": ANA}), 201)
    keys = [r["object_key"] for r in await sql(sm, w, "SELECT object_key FROM hd_attachment")]
    before = await sql(
        sm, w, "SELECT kind, count(*) AS n FROM hd_message GROUP BY kind ORDER BY kind"
    )
    pending = await sql(sm, w, "SELECT count(*) AS n FROM hd_email_out WHERE status = 'pending' "
                        "AND ticket_id = :t", t=w["ticket"]["id"])  # fmt: skip
    assert pending[0]["n"] >= 1

    erase = {"contact_id": w["contact"], "confirm_email": "someone@else.test"}
    assert (await client.post(f"{P}/erase", headers=w["admin"], json=erase)).status_code == 422
    assert (await client.post(f"{P}/erase", headers=w["neha"], json=erase)).status_code == 403
    erase["confirm_email"] = " ANA@shop.test "
    done = ok(await client.post(f"{P}/erase", headers=w["admin"], json=erase), 201)
    counts = done["counts"]
    assert [counts[k] for k in ("tickets", "messages", "files", "exports")] == [1, 4, 1, 1]
    assert counts["rating_comments"] == 1 and counts["portal_sessions"] == 1
    assert counts["portal_codes"] == 1 and counts["emails"] >= 1 and counts["events"] >= 1
    assert counts["import_links"] == 1
    assert (await client.post(f"{P}/erase", headers=w["admin"], json=erase)).status_code == 409

    # Counts stay; the words are gone.
    after = await sql(
        sm, w, "SELECT kind, count(*) AS n FROM hd_message GROUP BY kind ORDER BY kind"
    )
    assert after == before
    assert {r["body"] for r in await sql(sm, w, "SELECT body FROM hd_message")} == {"(erased)"}
    [ticket] = await sql(sm, w, "SELECT subject, fields, search_text FROM hd_ticket")
    assert ticket["subject"] == "(erased)" and ticket["fields"] == {}
    [contact] = await sql(sm, w, "SELECT email, name, erased_at FROM hd_contact")
    assert contact["email"] == f"erased-{w['contact']}@erased.invalid"
    assert contact["name"] == "Erased customer" and contact["erased_at"] is not None
    [rating] = await sql(sm, w, "SELECT score, comment FROM hd_csat")
    assert (rating["score"], rating["comment"]) == (2, None)
    emails = await sql(sm, w, "SELECT status, last_error, to_addresses FROM hd_email_out "
                       "WHERE ticket_id = :t", t=w["ticket"]["id"])  # fmt: skip
    assert {e["status"] for e in emails} <= {"failed", "sent"}
    assert all(e["to_addresses"] == ["erased@erased.invalid"] for e in emails)
    events = await sql(sm, w, "SELECT payload, published_at, failed_at FROM hd_outbox "
                       "WHERE ticket_id = :t", t=w["ticket"]["id"])  # fmt: skip
    assert events and all(e["payload"] == {"erased": True} for e in events)
    assert all(e["published_at"] or e["failed_at"] for e in events)
    assert not await sql(sm, w, "SELECT 1 FROM hd_portal_code")
    assert not await sql(sm, w, "SELECT 1 FROM hd_portal_session WHERE revoked_at IS NULL")

    # The files are gone from storage and can't be downloaded.
    for key in keys:
        with pytest.raises(ObjectMissing):
            await store.get(key)
    r = await client.get(f"/api/v1/attachments/{w['photo']}", headers=w["admin"])
    assert r.status_code == 409 and "erased" in r.json()["error"]["message"]
    assert (
        await client.get(f"{P}/requests/{export['id']}/file", headers=w["admin"])
    ).status_code == 409

    # Nothing anywhere still holds her address or words.
    for table, column in (("hd_ticket", "search_text"), ("hd_message", "body"),
                          ("hd_message", "email::text"), ("hd_outbox", "payload::text"),
                          ("hd_audit", "target || detail::text"),
                          ("hd_privacy_request", "counts::text"),
                          ("hd_import_map", "external_id")):  # fmt: skip
        found = await sql(sm, w, f"SELECT 1 FROM {table} WHERE {column} ILIKE '%ana%'")  # noqa: S608
        assert not found, table
    tickets = ok(await client.get("/api/v1/tickets", headers=w["admin"], params={"q": "broken"}))
    assert tickets["tickets"] == []
    assert ok(await client.get("/api/v1/tickets", headers=w["admin"]))["tickets"][0]["subject"] == (
        "(erased)"
    )

    # She writes again: a new, separate contact.
    new = ok(await client.post("/api/v1/tickets", headers=w["admin"], json={
        "subject": "Me again", "channel": "email", "description": "Hello",
        "contact": {"email": ANA, "name": "Ana"}}), 201)  # fmt: skip
    assert new["contact"]["id"] != w["contact"]
