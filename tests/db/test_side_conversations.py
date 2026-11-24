"""Side conversations (md X5), with RLS on: an agent emails another team from a ticket; the
email is queued from the ticket's support address; the reply comes back to the side
conversation (never the customer's thread), the assignee is told, and the customer sees
none of it in the portal or the ticket's messages."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.email_in import Outcome, receive
from tests.db.helpers import Sm, add, bearer, ok, sign_in
from tests.db.test_email_in import TOKEN, desk, email, hook  # noqa: F401 (hook: a fixture)

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def outbox(sm: Sm, ws: UUID) -> list[Any]:
    async with workspace_session(sm, ws) as s:
        rows = await s.execute(
            text(
                "SELECT to_addresses, from_address, subject, body_text, header_message_id, "
                "in_reply_to, references_ids, message_id FROM hd_email_out ORDER BY created_at"
            )
        )
        return list(rows.mappings().all())


async def customer_ticket(client: AsyncClient, sm: Sm, w: dict[str, Any]) -> str:
    """A ticket from Priya's email to billing@, assigned to Neha."""
    got = await receive(sm, email(to=w["billing"], subject="Order 1234 missing"))
    ticket_id = str(got.ticket_id)
    ok(await client.patch(f"{T}/{ticket_id}", headers=w["h"], json={"assignee_id": w["neha"]}))
    return ticket_id


async def test_a_side_conversation_goes_out_and_its_reply_comes_back(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    tid = await customer_ticket(client, sm, w)
    url = f"{T}/{tid}/side-conversations"
    body = {"to": ["Warehouse@Acme.com"], "body": "Was order 1234 shipped?"}
    side = ok(await client.post(url, headers=w["agent"], json=body), 201)
    assert side["to"] == ["warehouse@acme.com"] and side["from_address"] == w["billing"]
    assert side["subject"].startswith("About Order 1234 missing [T-")
    first = side["messages"][0]
    assert (first["direction"], first["author_name"], first["body"]) == (
        "out", "Neha", "Was order 1234 shipped?")  # fmt: skip

    queued = (await outbox(sm, w["ws"]))[-1]
    assert (queued["to_addresses"], queued["from_address"], queued["message_id"]) == (
        ["warehouse@acme.com"], w["billing"], None)  # fmt: skip
    assert queued["subject"] == side["subject"]

    reply = email(
        to=w["billing"],
        sender="Ravi <warehouse@acme.com>",
        subject=f"Re: {side['subject']}",
        body="Shipped yesterday, AWB 998877.",
        in_reply_to=queued["header_message_id"],
    )
    got = await receive(sm, reply)
    assert (got.outcome, str(got.ticket_id)) == (Outcome.side_reply, tid)
    assert (await receive(sm, reply)).outcome is Outcome.side_reply  # again: saved once

    [after] = ok(await client.get(url, headers=w["agent"]))
    assert [(m["direction"], m["body"]) for m in after["messages"]] == [
        ("out", "Was order 1234 shipped?"), ("in", "Shipped yesterday, AWB 998877.")]  # fmt: skip
    t = ok(await client.get(f"{T}/{tid}", headers=w["h"]))
    assert "AWB 998877" not in str([m["body"] for m in t["messages"] if m["kind"] != "system"])
    lines = [m["body"] for m in t["messages"] if m["kind"] == "system"]
    assert "Side conversation with warehouse@acme.com started by Neha" in lines
    assert lines.count("warehouse@acme.com replied in a side conversation") == 1  # not twice
    notes = ok(await client.get("/api/v1/notifications", headers=w["agent"]))
    assert [n["kind"] for n in notes["notifications"]].count("side_reply") == 1

    again = ok(
        await client.post(
            f"/api/v1/side-conversations/{side['id']}/messages",
            headers=w["agent"],
            json={"body": "Thanks, which courier?"},
        ),
        201,
    )
    assert [m["direction"] for m in again["messages"]] == ["out", "in", "out"]
    follow = (await outbox(sm, w["ws"]))[-1]
    assert follow["subject"] == f"Re: {side['subject']}"
    # Threaded under their reply (the latest), with the whole chain in References.
    assert follow["in_reply_to"] not in (None, queued["header_message_id"])
    assert follow["references_ids"][0] == queued["header_message_id"]
    assert follow["in_reply_to"] == follow["references_ids"][-1]


async def test_a_side_reply_through_the_email_webhook_answers_200(
    client: AsyncClient,
    hook: AsyncClient,  # noqa: F811 (the fixture imported above)
    app_sessionmaker: Sm,
) -> None:
    """The webhook's answer must allow side_reply: it was missing, so the reply was saved but
    the route answered 500 and the mail service kept sending it again."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    tid = await customer_ticket(client, sm, w)
    url = f"{T}/{tid}/side-conversations"
    body = {"to": ["warehouse@acme.com"], "body": "Shipped?"}
    side = ok(await client.post(url, headers=w["agent"], json=body), 201)
    queued = (await outbox(sm, w["ws"]))[-1]
    reply = email(to=w["billing"], sender="Warehouse <warehouse@acme.com>",
                  subject=f"Re: {side['subject']}", body="Yes, today.",
                  in_reply_to=queued["header_message_id"])  # fmt: skip
    headers = {"Content-Type": "message/rfc822", "Authorization": f"Bearer {TOKEN}"}
    for _ in range(2):  # a retry by the mail service gets the same answer, saved once
        got = ok(await hook.post("/api/v1/inbound/email", content=reply, headers=headers))
        assert got == {"outcome": "side_reply", "ticket_id": tid}
    [after] = ok(await client.get(url, headers=w["agent"]))
    assert [m["direction"] for m in after["messages"]] == ["out", "in"]


async def test_who_and_what_is_refused(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    tid = await customer_ticket(client, sm, w)
    url = f"{T}/{tid}/side-conversations"
    for to in (["priya@example.com"], [w["support"]], ["not an address"], []):
        r = await client.post(url, headers=w["agent"], json={"to": to, "body": "Hi"})
        assert r.status_code == 422, to
    me = ok(await client.get("/api/v1/me", headers=w["h"]))
    token = w["h"]["Authorization"].split()[1]
    await add(client, token, "meena@example.com", "light_agent", "Meena")
    light = bearer(await sign_in(client, me["workspace"]["slug"], "meena@example.com"))
    r = await client.post(url, headers=light, json={"to": ["x@acme.com"], "body": "Hi"})
    assert r.status_code == 403  # a light agent can't reply, so can't email out
    r = await client.post(f"{T}/{UUID(int=3)}/side-conversations", headers=w["agent"],
                          json={"to": ["x@acme.com"], "body": "Hi"})  # fmt: skip
    assert r.status_code == 404


async def test_a_customer_email_is_still_the_customers(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Email in still threads the customer's own reply onto the ticket, not the side."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    got = await receive(sm, email(to=w["billing"], message_id="<first@gmail.com>"))
    tid = str(got.ticket_id)
    ok(await client.post(f"{T}/{tid}/side-conversations", headers=w["h"],
                         json={"to": ["warehouse@acme.com"], "body": "Status?"}), 201)  # fmt: skip
    back = await receive(sm, email(to=w["billing"], in_reply_to="<first@gmail.com>",
                                   body="Any update?"))  # fmt: skip
    assert back.outcome is not Outcome.side_reply and str(back.ticket_id) == tid
    [side] = ok(await client.get(f"{T}/{tid}/side-conversations", headers=w["h"]))
    assert [m["direction"] for m in side["messages"]] == ["out"]
