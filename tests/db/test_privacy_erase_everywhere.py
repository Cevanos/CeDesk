"""Erase reaches every place the customer's address or words end up, not only their own
tickets: side conversations about their ticket, the copy a merge made on someone else's
ticket, emails they were copied on, their portal sign-in emails, an import's failure
record and snapshot, and a contact.updated event. Other customers keep what is theirs."""

import json
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.storage import ObjectStore
from tests.db.helpers import Sm, ok
from tests.db.test_message_files import app_parts, client, store
from tests.db.test_privacy import ANA, P, customer_desk, sql

pytestmark = pytest.mark.db
__all__ = ["app_parts", "client", "store"]  # fixtures

BOB = "bob@shop.test"


async def ana_everywhere(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    w = await customer_desk(client, sm)
    ws, ana_ticket, contact = w["ws"], w["ticket"]["id"], w["contact"]

    # A side conversation with the courier about Ana's parcel.
    [side] = await sql(sm, w, "INSERT INTO hd_side_conversation (workspace_id, ticket_id, "
                       "subject, to_addresses, from_address) VALUES (:ws, :t, "
                       "'About Ana''s broken parcel', ARRAY['ops@courier.test'], :f) RETURNING id",
                       ws=ws, t=ana_ticket, f=f"support@{w['domain']}")  # fmt: skip
    for direction, body, who, addr in (
        ("out", "Ana at 12 Hill Rd says it broke", "Admin", f"support@{w['domain']}"),
        ("in", "We will refund Ana", "Courier Ops", "ops@courier.test"),
    ):
        await sql(sm, w, "INSERT INTO hd_side_message (workspace_id, side_id, direction, body, "
                  "author_name, from_address, header_message_id) VALUES (:ws, :s, :d, :b, :n, "
                  ":a, :h)", ws=ws, s=side["id"], d=direction, b=body, n=who, a=addr,
                  h=f"<{direction}-{side['id']}@x.test>")  # fmt: skip

    # Bob's ticket: Ana was copied on his email, and on an email CeDesk sent him.
    bob = ok(await client.post("/api/v1/tickets", headers=w["admin"], json={
        "subject": "Bob's order", "channel": "email", "description": "Where is it?",
        "contact": {"email": BOB, "name": "Bob"}}), 201)  # fmt: skip
    headers = {"from": BOB, "to": [f"support@{w['domain']}"], "cc": [f"Ana <{ANA}>"]}
    await sql(sm, w, "UPDATE hd_message SET email = CAST(:e AS jsonb) WHERE ticket_id = :t "
              "AND kind = 'customer'", e=json.dumps(headers), t=bob["id"])  # fmt: skip
    await sql(sm, w, "INSERT INTO hd_email_out (workspace_id, ticket_id, from_address, "
              "from_name, to_addresses, cc_addresses, subject, body_text, status, last_error) "
              "VALUES (:ws, :t, :f, 'Acme', ARRAY[:bob], ARRAY[:ana], 'Re: order', 'Hi Bob', "
              "'failed', :err)", ws=ws, t=bob["id"], f=f"support@{w['domain']}", bob=BOB,
              ana=ANA, err=f"550 <{ANA}> mailbox full")  # fmt: skip

    # Ana's second ticket, merged into Bob's: its messages are copied there.
    second = ok(await client.post("/api/v1/tickets", headers=w["admin"], json={
        "subject": "Also mine", "channel": "email", "description": "Ana again: still broken",
        "contact": {"email": ANA, "name": "Ana"}}), 201)  # fmt: skip
    ok(await client.post(f"/api/v1/tickets/{second['id']}/merge", headers=w["admin"],
                         json={"into": bob["id"]}))  # fmt: skip

    # Her portal sign-in code email (no ticket), still queued.
    await sql(sm, w, "INSERT INTO hd_email_out (workspace_id, ticket_id, from_address, "
              "from_name, to_addresses, subject, body_text, status, sensitive) VALUES (:ws, NULL, "
              ":f, 'Acme', ARRAY[:ana], 'Your code', 'Ana, your code is 123456', 'pending', "
              "true)", ws=ws, f=f"support@{w['domain']}", ana=ANA)  # fmt: skip

    # An import that failed on her row, and its snapshot of her ticket.
    [conn] = await sql(sm, w, "INSERT INTO hd_import_connection (workspace_id, source, label) "
                       "VALUES (:ws, 'csv', 'contacts.csv') RETURNING id", ws=ws)  # fmt: skip
    [run] = await sql(sm, w, "INSERT INTO hd_import_run (workspace_id, connection_id, source) "
                      "VALUES (:ws, :c, 'csv') RETURNING id", ws=ws, c=conn["id"])  # fmt: skip
    await sql(sm, w, "INSERT INTO hd_import_failure (workspace_id, run_id, source, kind, "
              "external_id, category, last_error) VALUES (:ws, :r, 'csv', 'contact', :ana, "
              "'incomplete', :err)", ws=ws, r=run["id"], ana=ANA,
              err=f"row for {ANA}: no name")  # fmt: skip
    await sql(sm, w, "INSERT INTO hd_import_seen (workspace_id, ticket_id, seen) VALUES "
              "(:ws, :t, CAST(:s AS jsonb))", ws=ws, t=ana_ticket,
              s=json.dumps({"subject": "Ana's parcel", "fields": {"source_cc": ANA}}))  # fmt: skip

    # A contact.updated event, which names her and has no ticket.
    named = json.dumps({"contact": {"id": str(contact), "email": ANA, "name": "Ana"}})
    await sql(sm, w, "INSERT INTO hd_outbox (workspace_id, event, ticket_id, payload) VALUES "
              "(:ws, 'contact.updated', NULL, CAST(:p AS jsonb))", ws=ws, p=named)  # fmt: skip
    return {**w, "bob": bob, "second": second, "side": side["id"]}


PLACES = (
    ("hd_side_conversation", "subject || array_to_string(to_addresses, ',')"),
    ("hd_side_message", "body || author_name || from_address"),
    ("hd_message", "body || coalesce(email::text, '') || coalesce(recipients::text, '')"),
    ("hd_email_out", "subject || body_text || array_to_string(to_addresses, ',') || "
                     "array_to_string(cc_addresses, ',') || coalesce(last_error, '')"),
    ("hd_import_failure", "external_id || last_error"),
    ("hd_import_seen", "seen::text"),
    ("hd_outbox", "payload::text"),
    ("hd_ticket", "subject || fields::text || coalesce(search_text::text, '')"),
    ("hd_contact", "email || coalesce(name, '')"),
)  # fmt: skip


async def test_erase_reaches_every_copy_and_leaves_others_alone(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    sm = app_sessionmaker
    w = await ana_everywhere(client, sm)
    for table, column in PLACES:  # she is really there before
        if table in ("hd_ticket", "hd_contact"):
            continue
        assert await sql(sm, w, f"SELECT 1 FROM {table} WHERE {column} ILIKE '%ana%'"), table  # noqa: S608

    erase = {"contact_id": w["contact"], "confirm_email": ANA}
    counts = ok(await client.post(f"{P}/erase", headers=w["admin"], json=erase), 201)["counts"]
    assert counts["side_conversations"] == 1 and counts["side_messages"] == 2
    assert counts["merged_messages"] >= 1 and counts["sign_in_emails"] == 1
    assert counts["sent_elsewhere"] == 1 and counts["email_headers"] >= 1
    assert counts["import_failures"] == 1 and counts["import_snapshots"] == 1

    for table, column in PLACES:
        left = await sql(sm, w, f"SELECT {column} AS v FROM {table} WHERE {column} ILIKE '%ana%'")  # noqa: S608
        assert not left, (table, left)

    # Bob keeps his ticket, his address and his own words.
    [bob] = await sql(sm, w, "SELECT subject FROM hd_ticket WHERE id = :t", t=w["bob"]["id"])
    assert bob["subject"] == "Bob's order"
    [sent] = await sql(sm, w, "SELECT to_addresses, cc_addresses FROM hd_email_out "
                       "WHERE ticket_id = :t", t=w["bob"]["id"])  # fmt: skip
    assert sent["to_addresses"] == [BOB] and sent["cc_addresses"] == []
    bodies = {r["body"] for r in await sql(sm, w, "SELECT body FROM hd_message WHERE "
                                           "ticket_id = :t", t=w["bob"]["id"])}  # fmt: skip
    assert "Where is it?" in bodies
    [headers] = await sql(sm, w, "SELECT email FROM hd_message WHERE ticket_id = :t AND "
                          "email IS NOT NULL", t=w["bob"]["id"])  # fmt: skip
    assert headers["email"]["from"] == BOB
    # The queued sign-in email is stopped, not sent later.
    [code] = await sql(sm, w, "SELECT status FROM hd_email_out WHERE ticket_id IS NULL")
    assert code["status"] == "failed"
    # The audit chain still verifies.
    assert ok(await client.post("/api/v1/audit/verify", headers=w["admin"]))["ok"] is True


async def test_erasing_twice_is_refused_and_changes_nothing(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    w = await ana_everywhere(client, app_sessionmaker)
    erase = {"contact_id": w["contact"], "confirm_email": ANA}
    ok(await client.post(f"{P}/erase", headers=w["admin"], json=erase), 201)
    again = await client.post(f"{P}/erase", headers=w["admin"], json=erase)
    assert again.status_code == 409
