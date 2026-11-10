"""Email out (md C9) through the real API with RLS on: support addresses (Admin only, one
default), a reply on an email ticket is queued with the right From, To, CC, subject,
signature and threading headers, and the worker sends it, retries, or gives up with a
line on the ticket."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import email as rules
from cedesk.helpdesk.service.customer_replies import customer_reply
from cedesk.helpdesk.service.email import NO_ADDRESS_LINE, send_due
from cedesk.mail import OutgoingEmail, SendError
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/email-addresses"
T = "/api/v1/tickets"


class FakeMailer:
    """Records what it sends. Emails from other tests' workspaces (still queued in the
    shared test database) are accepted and ignored; `fail` applies only to ours."""

    def __init__(self, domain: str, fail: SendError | None = None) -> None:
        self.domain, self.fail = domain, fail
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        if not email.from_address.endswith("@" + self.domain):
            return
        if self.fail is not None:
            raise self.fail
        self.sent.append(email)


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    agent = bearer(await sign_in(client, slug, "neha@example.com"))
    return {"slug": slug, "domain": f"{slug}.test", "h": bearer(token), "agent": agent,
            "neha": neha, "ws": UUID(token.split(".")[0])}  # fmt: skip


async def address(
    client: AsyncClient, w: dict[str, Any], local: str, **extra: Any
) -> dict[str, Any]:
    body = {"address": f"{local}@{w['domain']}", "name": f"Acme {local}", **extra}
    result: dict[str, Any] = ok(await client.post(A, headers=w["h"], json=body), 201)
    return result


async def email_ticket(client: AsyncClient, w: dict[str, Any], channel: str = "email") -> Any:
    body = {
        "subject": "Damaged T-shirt",
        "contact": {"email": "priya@example.com", "name": "Priya Sharma"},
        "description": "My T-shirt arrived torn.",
        "channel": channel,
        "assignee_id": w["neha"],
    }
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def reply(client: AsyncClient, h: dict[str, str], ticket_id: str, body: str) -> Any:
    r = await client.post(f"{T}/{ticket_id}/messages", headers=h,
                          json={"kind": "reply", "body": body})  # fmt: skip
    return ok(r, 201)


async def queued(sm: Sm, ws: UUID) -> list[Any]:
    async with workspace_session(sm, ws) as s:
        result = await s.execute(text("SELECT * FROM hd_email_out ORDER BY created_at, id"))
        return list(result.mappings().all())


async def make_due(sm: Sm, ws: UUID) -> None:
    async with workspace_session(sm, ws) as s:
        await s.execute(
            text("UPDATE hd_email_out SET next_attempt_at = now() - interval '1 second'")
        )


async def ticket(client: AsyncClient, h: dict[str, str], ticket_id: str) -> Any:
    return ok(await client.get(f"{T}/{ticket_id}", headers=h))


def lines(t: dict[str, Any]) -> list[str]:
    """System lines about email (not 'Ticket created' or 'Assigned to')."""
    return [m["body"] for m in t["messages"]
            if m["kind"] == "system" and "mail" in m["body"]]  # fmt: skip


# Support addresses


async def test_the_first_address_is_the_default_and_the_default_can_move(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    support = await address(client, w, "Support", signature="Thanks,\n{{agent.name}}")
    assert support["address"] == f"support@{w['domain']}"  # stored lower-case
    assert support["is_default"] is True
    billing = await address(client, w, "billing")
    assert billing["is_default"] is False

    moved = ok(await client.patch(f"{A}/{billing['id']}", headers=w["h"],
                                  json={"is_default": True, "name": "Billing Desk"}))  # fmt: skip
    assert moved["is_default"] is True and moved["name"] == "Billing Desk"
    rows = ok(await client.get(A, headers=w["h"]))
    assert [(r["address"].split("@")[0], r["is_default"]) for r in rows] == [
        ("billing", True), ("support", False),
    ]  # fmt: skip

    r = await client.patch(f"{A}/{billing['id']}", headers=w["h"], json={"is_default": False})
    assert r.status_code == 422  # make another one the default instead

    # Deleting the default hands it to the oldest remaining address.
    assert (await client.delete(f"{A}/{billing['id']}", headers=w["h"])).status_code == 204
    rows = ok(await client.get(A, headers=w["h"]))
    assert [(r["id"], r["is_default"]) for r in rows] == [(support["id"], True)]


async def test_addresses_are_admin_only_checked_and_unique_everywhere(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    support = await address(client, w, "support")
    body = {"address": f"x@{w['domain']}", "name": "X"}
    assert (await client.get(A, headers=w["agent"])).status_code == 403
    assert (await client.post(A, headers=w["agent"], json=body)).status_code == 403

    for bad in ["support", "a b@acme.in", f"x@{w['domain']}\r\nBcc: e@x.com"]:
        r = await client.post(A, headers=w["h"], json={"address": bad, "name": "X"})
        assert r.status_code == 422, bad
    r = await client.post(A, headers=w["h"], json={"address": support["address"], "name": "X"})
    assert r.status_code == 409

    # Another workspace can't take the same address, see this one's, or change it.
    other = await desk(client, app_sessionmaker)
    r = await client.post(A, headers=other["h"],
                          json={"address": support["address"].upper(), "name": "X"})  # fmt: skip
    assert r.status_code == 409
    assert ok(await client.get(A, headers=other["h"])) == []
    r = await client.patch(f"{A}/{support['id']}", headers=other["h"], json={"name": "Y"})
    assert r.status_code == 404
    assert (await client.delete(f"{A}/{support['id']}", headers=other["h"])).status_code == 404


# Replies


async def test_a_reply_on_an_email_ticket_is_queued_with_signature_and_label(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support", signature="Thanks,\n{{agent.name}}, Acme")
    t = await email_ticket(client, w)
    sent = await reply(client, w["agent"], t["id"], "Hi {{customer.first_name}}, sorry!")
    assert sent["delivery"] == "pending"
    assert sent["email"]["from"] == f"support@{w['domain']}"
    assert sent["email"]["to"] == ["priya@example.com"]

    [row] = await queued(app_sessionmaker, w["ws"])
    assert row["from_address"] == f"support@{w['domain']}"
    assert row["from_name"] == "Acme support"
    assert row["to_addresses"] == ["priya@example.com"]
    assert row["cc_addresses"] == []
    assert row["subject"] == f"Re: Damaged T-shirt [{t['label']}]"
    assert row["body_text"] == "Hi Priya, sorry!\n\n-- \nThanks,\nNeha, Acme"
    assert row["header_message_id"] == sent["email"]["message_id"]
    assert row["in_reply_to"] is None  # the first message came in without email headers
    assert row["message_id"] == UUID(sent["id"])

    # The ticket shows it too, and a note is never emailed.
    note = {"kind": "note", "body": "internal"}
    ok(await client.post(f"{T}/{t['id']}/messages", headers=w["agent"], json=note), 201)
    view = await ticket(client, w["h"], t["id"])
    by_kind = {m["kind"]: m["delivery"] for m in view["messages"]}
    assert by_kind == {"customer": None, "agent": "pending", "note": None, "system": None}
    assert len(await queued(app_sessionmaker, w["ws"])) == 1


async def test_only_email_tickets_are_emailed(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w, channel="web")
    sent = await reply(client, w["agent"], t["id"], "Hello")
    assert sent["delivery"] is None and sent["email"] is None
    assert await queued(app_sessionmaker, w["ws"]) == []


async def test_without_a_support_address_the_ticket_says_it_was_not_emailed(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await email_ticket(client, w)
    sent = await reply(client, w["agent"], t["id"], "Hello")
    assert sent["delivery"] is None
    assert lines(await ticket(client, w["h"], t["id"])) == [NO_ADDRESS_LINE]
    assert await queued(app_sessionmaker, w["ws"]) == []


async def test_reply_comes_from_the_address_written_to_keeps_cc_and_threads(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    await address(client, w, "billing")
    t = await email_ticket(client, w)
    ticket_id = UUID(t["id"])
    billing = f"billing@{w['domain']}"
    await customer_reply(
        app_sessionmaker, w["ws"], ticket_id, "Any update?",
        email={"message_id": "<m1@gmail.com>", "from": "priya@example.com",
               "to": [billing.upper()], "cc": ["Rahul@x.com", f"support@{w['domain']}"],
               "subject": "Re: Damaged T-shirt", "references": ["<m0@gmail.com>"]},
    )  # fmt: skip
    await customer_reply(
        app_sessionmaker, w["ws"], ticket_id, "Hello??",
        email={"message_id": "<m2@gmail.com>", "from": "priya@example.com",
               "to": [billing], "cc": [], "subject": "Re: Re: Damaged T-shirt",
               "references": ["<m0@gmail.com>", "<m1@gmail.com>"]},
    )  # fmt: skip
    await reply(client, w["agent"], t["id"], "On it.")

    [row] = await queued(app_sessionmaker, w["ws"])
    assert row["from_address"] == billing  # where Priya wrote, not the default
    assert row["cc_addresses"] == ["rahul@x.com"]  # from the first email with a CC
    assert row["in_reply_to"] == "<m2@gmail.com>"  # her latest email
    assert row["references_ids"] == ["<m0@gmail.com>", "<m1@gmail.com>", "<m2@gmail.com>"]
    assert row["subject"] == f"Re: Re: Damaged T-shirt [{t['label']}]"


async def test_a_macro_reply_is_emailed_too(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    macro = ok(await client.post("/api/v1/macros", headers=w["h"], json={
        "name": "Thanks", "steps": [{"kind": "reply", "text": "Thanks {{customer.first_name}}"}],
    }), 201)  # fmt: skip
    r = await client.post(f"{T}/{t['id']}/macros/{macro['id']}", headers=w["agent"])
    assert r.status_code == 200, r.text
    [row] = await queued(app_sessionmaker, w["ws"])
    assert row["body_text"] == "Thanks Priya"


# The worker


async def test_the_worker_sends_once_and_marks_it_sent(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    sent = await reply(client, w["agent"], t["id"], "Hello")
    mailer = FakeMailer(w["domain"])

    await send_due(app_sessionmaker, mailer)
    [email] = mailer.sent
    assert email.to == ["priya@example.com"]
    assert email.message_id == sent["email"]["message_id"]
    assert email.subject == f"Re: Damaged T-shirt [{t['label']}]"

    await send_due(app_sessionmaker, mailer)  # nothing left
    assert len(mailer.sent) == 1
    [row] = await queued(app_sessionmaker, w["ws"])
    assert (row["status"], row["attempts"], row["last_error"]) == ("sent", 1, None)
    assert row["sent_at"] is not None
    view = await ticket(client, w["h"], t["id"])
    assert [m["delivery"] for m in view["messages"] if m["kind"] == "agent"] == ["sent"]


async def test_a_temporary_error_retries_later_then_sends(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    await reply(client, w["agent"], t["id"], "Hello")

    down = FakeMailer(w["domain"], SendError("Could not reach the mail server."))
    await send_due(app_sessionmaker, down)
    [row] = await queued(app_sessionmaker, w["ws"])
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert row["last_error"] == "Could not reach the mail server."

    await send_due(app_sessionmaker, down)  # not due yet: no second try right away
    [row] = await queued(app_sessionmaker, w["ws"])
    assert row["attempts"] == 1

    await make_due(app_sessionmaker, w["ws"])
    up = FakeMailer(w["domain"])
    await send_due(app_sessionmaker, up)
    [row] = await queued(app_sessionmaker, w["ws"])
    assert (row["status"], row["attempts"], len(up.sent)) == ("sent", 2, 1)
    assert lines(await ticket(client, w["h"], t["id"])) == []


async def test_a_permanent_error_gives_up_with_a_line_on_the_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    await reply(client, w["agent"], t["id"], "Hello")

    refused = SendError("The mail server refused every recipient.", permanent=True)
    await send_due(app_sessionmaker, FakeMailer(w["domain"], refused))
    [row] = await queued(app_sessionmaker, w["ws"])
    assert row["status"] == "failed"
    view = await ticket(client, w["h"], t["id"])
    assert lines(view) == [
        "Email to priya@example.com could not be sent: The mail server refused every recipient."
    ]
    assert [m["delivery"] for m in view["messages"] if m["kind"] == "agent"] == ["failed"]


async def test_the_worker_gives_up_after_the_last_attempt(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    await reply(client, w["agent"], t["id"], "Hello")
    down = FakeMailer(w["domain"], SendError("Could not reach the mail server."))
    for _ in range(rules.MAX_ATTEMPTS):
        await make_due(app_sessionmaker, w["ws"])
        await send_due(app_sessionmaker, down)
    [row] = await queued(app_sessionmaker, w["ws"])
    assert (row["status"], row["attempts"]) == ("failed", rules.MAX_ATTEMPTS)
    assert len(lines(await ticket(client, w["h"], t["id"]))) == 1


async def test_a_test_email_goes_to_the_admin(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    support = await address(client, w, "support", signature="{{agent.name}}, Acme")
    assert (await client.post(f"{A}/{support['id']}/test", headers=w["agent"])).status_code == 403
    out = ok(await client.post(f"{A}/{support['id']}/test", headers=w["h"]), 202)
    assert out == {"sent_to": "admin@example.com"}
    mailer = FakeMailer(w["domain"])
    await send_due(app_sessionmaker, mailer)
    [email] = mailer.sent
    assert (email.to, email.subject) == (["admin@example.com"], "CeDesk test email")
    assert email.text.endswith("-- \nAdmin, Acme")  # placeholders filled


async def test_the_worker_sends_for_every_workspace(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    one, two = await desk(client, app_sessionmaker), await desk(client, app_sessionmaker)
    for w in (one, two):
        await address(client, w, "support")
        t = await email_ticket(client, w)
        await reply(client, w["agent"], t["id"], "Hello")
    first, second = FakeMailer(one["domain"]), FakeMailer(two["domain"])

    class Both:
        async def send(self, email: OutgoingEmail) -> None:
            await first.send(email)
            await second.send(email)

    await send_due(app_sessionmaker, Both())
    assert (len(first.sent), len(second.sent)) == (1, 1)


# Failed emails: the Admin list (md 2B "Reliability")


async def test_failed_emails_are_listed_for_admins_and_can_be_sent_again(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    sent = await reply(client, w["agent"], t["id"], "Hello")
    refused = SendError("Mailbox full.", permanent=True)
    await send_due(app_sessionmaker, FakeMailer(w["domain"], refused))

    assert (await client.get("/api/v1/email/failed", headers=w["agent"])).status_code == 403
    [failed] = ok(await client.get("/api/v1/email/failed", headers=w["h"]))["emails"]
    assert (failed["ticket_label"], failed["message_id"], failed["to"]) == (
        t["label"], sent["id"], ["priya@example.com"],
    )  # fmt: skip
    assert failed["last_error"] == "Mailbox full." and failed["can_retry"] is True

    r = await client.post(f"/api/v1/email/{failed['id']}/retry", headers=w["h"])
    assert r.status_code == 204
    view = await ticket(client, w["h"], t["id"])
    assert [m["delivery"] for m in view["messages"] if m["kind"] == "agent"] == ["pending"]
    assert any(m["body"].endswith("sent the failed email again.")
               for m in view["messages"] if m["kind"] == "system")  # fmt: skip
    mailer = FakeMailer(w["domain"])
    await send_due(app_sessionmaker, mailer)
    assert len(mailer.sent) == 1
    assert ok(await client.get("/api/v1/email/failed", headers=w["h"]))["emails"] == []
    again = await client.post(f"/api/v1/email/{failed['id']}/retry", headers=w["h"])
    assert again.status_code == 404  # sent now, not failed


async def test_an_account_email_that_failed_cannot_be_resent(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    async with workspace_session(app_sessionmaker, w["ws"]) as s:  # a failed reset email
        email_id = await s.scalar(text(
            "INSERT INTO hd_email_out (workspace_id, from_address, from_name, to_addresses, "
            "subject, body_text, status, sensitive, last_error) VALUES (:ws, :f, 'Acme', "
            "ARRAY['neha@example.com'], 'Reset your password', '(removed after sending)', "
            "'failed', true, 'refused') RETURNING id"),
            {"ws": w["ws"], "f": f"support@{w['domain']}"})  # fmt: skip
    [failed] = ok(await client.get("/api/v1/email/failed", headers=w["h"]))["emails"]
    assert failed["ticket_id"] is None and failed["can_retry"] is False
    r = await client.post(f"/api/v1/email/{email_id}/retry", headers=w["h"])
    assert r.status_code == 404


async def test_a_repeated_macro_run_emails_once(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await address(client, w, "support")
    t = await email_ticket(client, w)
    macro = ok(await client.post("/api/v1/macros", headers=w["h"], json={
        "name": "Thanks", "steps": [{"kind": "reply", "text": "Thanks!"}],
    }), 201)  # fmt: skip
    url = f"{T}/{t['id']}/macros/{macro['id']}"
    keyed = {**w["agent"], "Idempotency-Key": "run-1"}
    answers = await asyncio.gather(*[client.post(url, headers=keyed) for _ in range(3)])
    assert {r.status_code for r in answers} == {200}
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in answers) == 2
    assert len(await queued(app_sessionmaker, w["ws"])) == 1
    other = await client.post(f"{T}/{t['id']}/macros/{macro['id']}", headers=w["agent"])
    assert other.status_code == 200  # no key: runs again
    assert len(await queued(app_sessionmaker, w["ws"])) == 2
