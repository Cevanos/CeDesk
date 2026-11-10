"""Email in (md C8) with RLS on: an email to a support address becomes a ticket in that
workspace (rules and auto-assignment run, billing@ goes to Billing), a reply lands on the
ticket it answers (reopen, thank-you), duplicates and automatic emails are ignored, and the
webhook and the mailbox poller deliver emails."""

import base64
from collections.abc import AsyncIterator, Sequence
from email.message import EmailMessage
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.email_in import Outcome, poll, receive
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/email-addresses"
T = "/api/v1/tickets"
TOKEN = "t" * 40


def email(
    *,
    to: str,
    sender: str = "Priya Sharma <priya@example.com>",
    subject: str = "Damaged T-shirt",
    body: str = "My T-shirt arrived torn.",
    message_id: str | None = None,
    in_reply_to: str | None = None,
    cc: str | None = None,
    extra: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or f"<{uuid4()}@mail.gmail.com>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    for key, value in (extra or {}).items():
        msg[key] = value
    msg.set_content(body)
    return msg.as_bytes()


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """A seeded workspace (default teams, types and rules) with support@ and billing@,
    and Neha (agent, Billing)."""
    slug = await new_workspace(sm, seed=True)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    teams = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/teams", headers=h))}
    ok(await client.patch(f"/api/v1/teams/{teams['Billing']}", headers=h,
                          json={"member_ids": [neha]}))  # fmt: skip
    domain = f"{slug}.test"
    for local in ("support", "billing"):
        body = {"address": f"{local}@{domain}", "name": f"Acme {local}"}
        ok(await client.post(A, headers=h, json=body), 201)
    return {"h": h, "agent": bearer(await sign_in(client, slug, "neha@example.com")),
            "ws": UUID(token.split(".")[0]), "support": f"support@{domain}",
            "billing": f"billing@{domain}", "teams": teams, "neha": neha}  # fmt: skip


async def ticket(client: AsyncClient, h: dict[str, str], ticket_id: Any) -> Any:
    return ok(await client.get(f"{T}/{ticket_id}", headers=h))


def customer_messages(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "customer"]


async def test_a_new_email_becomes_a_ticket(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    got = await receive(app_sessionmaker, email(to=f"Acme <{w['support']}>",
                                                message_id="<new1@gmail.com>"))  # fmt: skip
    assert got.outcome is Outcome.created and got.workspace_id == w["ws"]
    t = await ticket(client, w["h"], got.ticket_id)
    assert (t["subject"], t["channel"], t["status"]) == ("Damaged T-shirt", "email", "open")
    assert (t["contact"]["email"], t["contact"]["name"]) == ("priya@example.com", "Priya Sharma")
    assert (t["source"], t["source_id"]) == ("email", "<new1@gmail.com>")
    first = next(m for m in t["messages"] if m["kind"] == "customer")
    assert first["body"] == "My T-shirt arrived torn."
    assert first["email"]["message_id"] == "<new1@gmail.com>"
    assert first["email"]["to"] == [w["support"]]
    lines = [m["body"] for m in t["messages"] if m["kind"] == "system"]
    assert lines[0] == "Ticket created by Priya Sharma (email)"


async def test_billing_at_goes_to_billing_and_is_auto_assigned(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    got = await receive(app_sessionmaker, email(to=w["billing"], subject="Invoice copy"))
    t = await ticket(client, w["h"], got.ticket_id)
    assert t["team_name"] == "Billing"
    assert t["type_name"] == "Billing question"
    assert t["assignee_id"] == w["neha"]
    lines = [m["body"] for m in t["messages"] if m["kind"] == "system"]
    assert any(line.startswith("Rule applied: billing@ emails to Billing") for line in lines)


async def test_the_reply_threads_both_ways(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    got = await receive(app_sessionmaker, email(to=w["billing"], message_id="<c1@gmail.com>"))
    sent = ok(await client.post(f"{T}/{got.ticket_id}/messages", headers=w["agent"],
                                json={"kind": "reply", "body": "Which order?"}), 201)  # fmt: skip
    assert sent["email"]["in_reply_to"] == "<c1@gmail.com>"
    assert sent["email"]["from"] == w["billing"]  # where Priya wrote

    answer = email(
        to=w["billing"],
        subject=f"Re: {sent['email']['subject']}",
        body="#1042\n\nOn Mon, Neha wrote:\n> Which order?",
        message_id="<c2@gmail.com>",
        in_reply_to=sent["email"]["message_id"],
    )
    again = await receive(app_sessionmaker, answer)
    assert (again.outcome, again.ticket_id) == (Outcome.added, got.ticket_id)
    t = await ticket(client, w["h"], got.ticket_id)
    assert customer_messages(t) == ["My T-shirt arrived torn.", "#1042"]

    # The same email delivered twice is saved once.
    assert (await receive(app_sessionmaker, answer)).outcome is Outcome.duplicate
    assert len(customer_messages(await ticket(client, w["h"], got.ticket_id))) == 2


async def test_resolved_ticket_reopens_but_a_thank_you_does_not(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    got = await receive(app_sessionmaker, email(to=w["support"], message_id="<r1@gmail.com>"))
    ok(await client.post(f"{T}/{got.ticket_id}/resolve", headers=w["h"]))
    thanks = await receive(app_sessionmaker, email(
        to=w["support"], body="Thanks a lot!", in_reply_to="<r1@gmail.com>"))  # fmt: skip
    assert thanks.outcome is Outcome.thanked
    assert (await ticket(client, w["h"], got.ticket_id))["status"] == "resolved"
    more = await receive(app_sessionmaker, email(
        to=w["support"], body="It broke again", in_reply_to="<r1@gmail.com>"))  # fmt: skip
    assert more.outcome is Outcome.reopened
    t = await ticket(client, w["h"], got.ticket_id)
    assert (t["status"], t["reopen_count"]) == ("open", 1)


async def test_the_subject_label_works_only_for_the_tickets_customer(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    got = await receive(app_sessionmaker, email(to=w["support"]))
    label = (await ticket(client, w["h"], got.ticket_id))["label"]
    mine = await receive(app_sessionmaker, email(to=w["support"], subject=f"Re: x [{label}]"))
    assert (mine.outcome, mine.ticket_id) == (Outcome.added, got.ticket_id)
    stranger = await receive(app_sessionmaker, email(
        to=w["support"], sender="eve@evil.com", subject=f"Re: x [{label}]"))  # fmt: skip
    assert stranger.outcome is Outcome.created and stranger.ticket_id != got.ticket_id


async def test_someone_in_cc_replying_is_named(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    got = await receive(app_sessionmaker, email(to=w["support"], message_id="<cc1@gmail.com>",
                                                cc="Rahul <rahul@x.com>"))  # fmt: skip
    await receive(
        app_sessionmaker,
        email(
            to=w["support"],
            sender="Rahul <rahul@x.com>",
            body="Me too",
            in_reply_to="<cc1@gmail.com>",
        ),
    )
    t = await ticket(client, w["h"], got.ticket_id)
    last = [m for m in t["messages"] if m["kind"] == "customer"][-1]
    assert (last["body"], last["author_name"]) == ("Me too", "Rahul")


async def test_a_reply_to_a_merged_ticket_goes_to_the_ticket_it_was_merged_into(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    old = await receive(app_sessionmaker, email(to=w["support"], message_id="<old@gmail.com>"))
    new = await receive(app_sessionmaker, email(to=w["support"]))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET merged_into_id = :n WHERE id = :o"),
                        {"n": new.ticket_id, "o": old.ticket_id})  # fmt: skip
    got = await receive(app_sessionmaker, email(to=w["support"], in_reply_to="<old@gmail.com>"))
    assert got.ticket_id == new.ticket_id


async def test_ignored_emails(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    out_of_office = email(to=w["support"], extra={"Auto-Submitted": "auto-replied"})
    assert (await receive(app_sessionmaker, out_of_office)).outcome is Outcome.automatic
    ours = email(to=w["support"], sender=w["billing"])  # e.g. our own reply, BCC'd back
    assert (await receive(app_sessionmaker, ours)).outcome is Outcome.from_us
    nobody = email(to=f"nobody-{uuid4().hex}@nowhere.test")
    assert (await receive(app_sessionmaker, nobody)).outcome is Outcome.no_workspace
    assert (await receive(app_sessionmaker, b"\x00junk")).outcome is Outcome.unreadable
    rows = ok(await client.get(T, headers=w["h"]))
    assert rows["tickets"] == []


async def test_a_forwarded_email_uses_the_delivery_address(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    forwarded = email(to="help@acme-old-domain.com")  # forwarded on to our address
    assert (await receive(app_sessionmaker, forwarded)).outcome is Outcome.no_workspace
    got = await receive(app_sessionmaker, forwarded, recipients=[w["billing"].upper()])
    assert got.outcome is Outcome.created
    t = await ticket(client, w["h"], got.ticket_id)
    assert t["team_name"] == "Billing"  # the address it reached us on counts
    first = next(m for m in t["messages"] if m["kind"] == "customer")
    assert first["email"]["to"][0] == w["billing"]


async def test_workspaces_never_mix(client: AsyncClient, app_sessionmaker: Sm) -> None:
    one, two = await desk(client, app_sessionmaker), await desk(client, app_sessionmaker)
    first = await receive(app_sessionmaker, email(to=one["support"], message_id="<x1@gmail.com>"))
    # Answers one's email, but is sent to two's address: a new ticket in two, nothing in one.
    other = await receive(app_sessionmaker, email(to=two["support"], in_reply_to="<x1@gmail.com>"))
    assert (other.outcome, other.workspace_id) == (Outcome.created, two["ws"])
    assert other.ticket_id != first.ticket_id
    assert len(customer_messages(await ticket(client, one["h"], first.ticket_id))) == 1
    assert (await client.get(f"{T}/{other.ticket_id}", headers=one["h"])).status_code == 404


async def test_a_deleted_address_stops_receiving(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    rows = ok(await client.get(A, headers=w["h"]))
    billing = next(r for r in rows if r["address"] == w["billing"])
    assert (await client.delete(f"{A}/{billing['id']}", headers=w["h"])).status_code == 204
    got = await receive(app_sessionmaker, email(to=w["billing"]))
    assert got.outcome is Outcome.no_workspace


async def test_the_app_cannot_write_the_address_directory(app_sessionmaker: Sm) -> None:
    async with app_sessionmaker() as s:
        with pytest.raises(Exception, match="permission denied"):
            await s.execute(
                text("INSERT INTO email_address_directory VALUES ('x@y.test', gen_random_uuid())")
            )


# Delivery: the webhook and the mailbox


@pytest.fixture
async def hook(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(sign_in_limit=100_000, inbound_email_token=TOKEN,  # type: ignore[arg-type]
                              inbound_email_max_bytes=20_000))  # fmt: skip
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


H = "/api/v1/inbound/email"
RAW = {"Content-Type": "message/rfc822"}


async def test_webhook_takes_a_raw_email(
    client: AsyncClient, hook: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    body = email(to=w["support"])
    assert (await hook.post(H, content=body, headers=RAW)).status_code == 401
    wrong = RAW | {"Authorization": "Bearer " + "x" * 40}
    assert (await hook.post(H, content=body, headers=wrong)).status_code == 401
    r = await hook.post(H, content=body, headers=RAW | {"Authorization": f"Bearer {TOKEN}"})
    got = ok(r)
    assert got["outcome"] == "created" and got["ticket_id"]
    basic = base64.b64encode(f"api:{TOKEN}".encode()).decode()
    again = ok(await hook.post(H, content=body, headers=RAW | {"Authorization": f"Basic {basic}"}))
    assert again == {"outcome": "duplicate", "ticket_id": None}


async def test_webhook_takes_mailgun_raw_mime(
    client: AsyncClient, hook: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    form = {"body-mime": email(to="old@elsewhere.test").decode(), "recipient": w["support"]}
    r = await hook.post(H, data=form, headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok(r)["outcome"] == "created"


async def test_webhook_takes_sendgrid_raw_email(
    client: AsyncClient, hook: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    files = {"email": (None, email(to=w["billing"]).decode()), "to": (None, w["billing"])}
    r = await hook.post(H, files=files, headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok(r)["outcome"] == "created"


async def test_webhook_refuses_big_emails_and_is_off_without_a_token(
    client: AsyncClient, hook: AsyncClient
) -> None:
    auth = RAW | {"Authorization": f"Bearer {TOKEN}"}
    big = email(to="a@b.test", body="x" * 30_000)
    assert (await hook.post(H, content=big, headers=auth)).status_code == 413
    # `client` runs without CEDESK_INBOUND_EMAIL_TOKEN: the route isn't there.
    assert (await client.post(H, content=b"x", headers=auth)).status_code == 404


class FakeMailbox:
    def __init__(self, emails: list[tuple[str, bytes]]) -> None:
        self.emails, self.marked = emails, []  # type: list[tuple[str, bytes]], list[str]

    async def fetch(self) -> list[tuple[str, bytes]]:
        return self.emails

    async def done(self, ids: Sequence[str]) -> None:
        self.marked += list(ids)


async def test_the_mailbox_poller_marks_only_handled_emails_read(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await desk(client, app_sessionmaker)
    box = FakeMailbox([("1", email(to=w["support"])), ("2", b"\x00junk"),
                       ("3", email(to=w["support"], subject="boom"))])  # fmt: skip
    from cedesk.helpdesk.service import email_in

    real = email_in.receive

    async def flaky(sm: Sm, raw: bytes, **kw: Any) -> Any:
        if b"boom" in raw:
            raise RuntimeError("database went away")
        return await real(sm, raw, **kw)

    monkeypatch.setattr(email_in, "receive", flaky)
    results = await poll(app_sessionmaker, box)
    assert [r.outcome for r in results] == [Outcome.created, Outcome.unreadable]
    assert box.marked == ["1", "2"]  # 3 failed: it stays unread for the next pass
