"""Files on replies, notes and customer emails (md T18) with RLS on: an agent sends files
they uploaded with a reply or note, every message lists its files, an emailed reply carries
them as attachments, and the files in a customer's email are kept on its message."""

from collections.abc import AsyncIterator
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.email import send_due
from cedesk.helpdesk.service.email_in import Outcome, receive
from cedesk.mail import OutgoingEmail, build
from cedesk.main import create_app
from cedesk.settings import Settings
from cedesk.storage import ObjectStore
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
MAX = 4096  # bytes per file, so the limit is cheap to test


@pytest.fixture
async def app_parts(app_sessionmaker: Sm, tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Any]]:
    settings = Settings(sign_in_limit=100_000, storage_local_path=str(tmp_path / "files"),
                        attachment_max_bytes=MAX)  # fmt: skip
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c, app.state.store


@pytest.fixture
def client(app_parts: tuple[AsyncClient, Any]) -> AsyncClient:
    return app_parts[0]


@pytest.fixture
def store(app_parts: tuple[AsyncClient, Any]) -> ObjectStore:
    found: ObjectStore = app_parts[1]
    return found


class Catch:
    """Records our workspace's emails; other tests' queued emails are ignored."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        if email.from_address.endswith("@" + self.domain):
            self.sent.append(email)


async def desk(client: AsyncClient, sm: Sm, *, channel: str = "web") -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    await add(client, token, "neha@example.com", "agent", "Neha")
    admin, ws = bearer(token), UUID(token.split(".")[0])
    domain = f"{slug}.test"
    body = {"address": f"support@{domain}", "name": "Acme Support"}
    ok(await client.post("/api/v1/email-addresses", headers=admin, json=body), 201)
    ticket = ok(await client.post(T, headers=admin, json={
        "subject": "Broken parcel", "channel": channel, "description": "It arrived broken.",
        "contact": {"email": "ana@shop.test", "name": "Ana"}}), 201)  # fmt: skip
    return {
        "admin": admin,
        "neha": bearer(await sign_in(client, slug, "neha@example.com")),
        "ws": ws,
        "domain": domain,
        "ticket": ticket,
        "url": f"{T}/{ticket['id']}",
    }


async def upload(
    client: AsyncClient, w: dict[str, Any], name: str = "photo.jpg", data: bytes = b"\xff\xd8jpg",
    *, who: str = "admin", ticket_url: str | None = None,
) -> str:  # fmt: skip
    headers = {**w[who], "Content-Type": "image/jpeg"}
    r = await client.post(f"{ticket_url or w['url']}/attachments", params={"name": name},
                          content=data, headers=headers)  # fmt: skip
    return str(ok(r, 201)["id"])


async def send(
    client: AsyncClient, w: dict[str, Any], ids: list[str], *,
    kind: str = "reply", body: str = "Here you go", who: str = "admin",
) -> Any:  # fmt: skip
    return await client.post(f"{w['url']}/messages", headers=w[who],
                             json={"kind": kind, "body": body, "attachment_ids": ids})  # fmt: skip


async def test_a_reply_sends_the_uploaded_files(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    a = await upload(client, w, "label.jpg")
    b = await upload(client, w, "invoice.pdf", b"%PDF-1.7")
    message = ok(await send(client, w, [a, b]), 201)
    assert [(f["id"], f["name"]) for f in message["attachments"]] == [
        (a, "label.jpg"), (b, "invoice.pdf"),
    ]  # fmt: skip
    assert message["attachments"][1]["size"] == 8

    # The ticket shows them on the message, and the ticket's file list knows the message.
    ticket = ok(await client.get(w["url"], headers=w["neha"]))
    [shown] = [m for m in ticket["messages"] if m["id"] == message["id"]]
    assert [f["name"] for f in shown["attachments"]] == ["label.jpg", "invoice.pdf"]
    assert all(m["attachments"] == [] for m in ticket["messages"] if m["id"] != message["id"])
    listed = ok(await client.get(f"{w['url']}/attachments", headers=w["admin"]))["attachments"]
    assert {f["message_id"] for f in listed} == {message["id"]}
    older = ok(await client.get(f"{w['url']}/messages", headers=w["admin"]))["messages"]
    assert [f["id"] for f in older[0]["attachments"]] == [a, b]
    link = ok(await client.get(f"/api/v1/attachments/{a}", headers=w["neha"]))
    assert (await client.get(link["url"])).content == b"\xff\xd8jpg"


async def test_a_note_can_be_just_a_file(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    f = await upload(client, w, who="neha")
    note = ok(await send(client, w, [f], kind="note", body="", who="neha"), 201)
    assert note["body"] == "" and note["attachments"][0]["id"] == f
    r = await send(client, w, [], kind="note", body="  ")
    assert r.status_code == 422 and "add a file" in r.text


async def test_only_your_own_unsent_files_from_this_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    other = ok(await client.post(T, headers=w["admin"], json={
        "subject": "Other", "contact": {"email": "bo@shop.test", "name": "Bo"}}), 201)  # fmt: skip
    elsewhere = await upload(client, w, ticket_url=f"{T}/{other['id']}")
    nehas = await upload(client, w, who="neha")
    mine = await upload(client, w)
    infected = await upload(client, w)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_attachment SET scan_status = 'infected' WHERE id = :id"),
                        {"id": infected})  # fmt: skip

    for ids, words in [
        ([elsewhere], "isn't on this ticket"),
        (["00000000-0000-0000-0000-000000000000"], "isn't on this ticket"),
        ([nehas], "files you uploaded"),
        ([infected], "virus scan"),
    ]:
        r = await send(client, w, ids)
        assert r.status_code == 422 and words in r.text, (ids, r.text)

    ok(await send(client, w, [mine, mine]), 201)  # the same id twice counts once
    r = await send(client, w, [mine])
    assert r.status_code == 422 and "already sent" in r.text
    # A refused message leaves nothing behind.
    ticket = ok(await client.get(w["url"], headers=w["admin"]))
    assert [m["body"] for m in ticket["messages"] if m["kind"] == "agent"] == ["Here you go"]
    too_many = [await upload(client, w) for _ in range(11)]
    assert (await send(client, w, too_many)).status_code == 422


async def test_an_emailed_reply_carries_its_files(
    client: AsyncClient, store: ObjectStore, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker, channel="email")
    f = await upload(client, w, "label.jpg")
    ok(await send(client, w, [f]), 201)
    mailer = Catch(w["domain"])
    await send_due(app_sessionmaker, mailer, store=store)
    [email] = mailer.sent
    assert [(a.name, a.mime, a.data) for a in email.attachments] == [
        ("label.jpg", "image/jpeg", b"\xff\xd8jpg"),
    ]  # fmt: skip
    parsed = BytesParser(policy=policy.default).parsebytes(build(email).as_bytes())
    assert isinstance(parsed, EmailMessage)
    [part] = list(parsed.iter_attachments())
    assert part.get_filename() == "label.jpg" and part.get_content() == b"\xff\xd8jpg"
    assert parsed.get_body(preferencelist=("plain",)) is not None

    # Too much for one email is refused when the reply is written.
    big = await upload(client, w)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_attachment SET size = 19 * 1024 * 1024 WHERE id = :id"),
                        {"id": big})  # fmt: skip
    r = await send(client, w, [big])
    assert r.status_code == 422 and "18 MB" in r.text
    ok(await send(client, w, [big], kind="note"), 201)  # a note isn't emailed


async def test_a_missing_file_fails_the_email_with_a_line(
    client: AsyncClient, store: ObjectStore, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker, channel="email")
    f = await upload(client, w, "label.jpg")
    reply = ok(await send(client, w, [f]), 201)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        key = await s.scalar(text("SELECT object_key FROM hd_attachment WHERE id = :id"),
                             {"id": f})  # fmt: skip
    await store.delete(str(key))
    mailer = Catch(w["domain"])
    await send_due(app_sessionmaker, mailer, store=store)
    assert mailer.sent == []
    ticket = ok(await client.get(w["url"], headers=w["admin"]))
    assert any("label.jpg is missing from storage" in m["body"] for m in ticket["messages"])
    [shown] = [m for m in ticket["messages"] if m["id"] == reply["id"]]
    assert shown["delivery"] == "failed"


def customer_email(domain: str, **extra: Any) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Priya <priya@example.com>"
    msg["To"] = f"support@{domain}"
    msg["Subject"] = "Torn T-shirt"
    msg["Message-ID"] = f"<{extra.get('mid', 'first')}@mail.example.com>"
    if "reply_to" in extra:
        msg["In-Reply-To"] = extra["reply_to"]
    msg.set_content("It arrived torn, photos attached.")
    return msg


async def test_files_in_a_customer_email_are_kept(
    client: AsyncClient, store: ObjectStore, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    msg = customer_email(w["domain"])
    msg.add_attachment(b"\x89PNG photo", maintype="image", subtype="png", filename="tear.png")
    msg.add_attachment(b"x" * (MAX + 1), maintype="application", subtype="zip",
                       filename="videos.zip")  # fmt: skip
    msg.add_attachment(b"logo", maintype="image", subtype="gif", disposition="inline",
                       cid="<logo@mail>")  # fmt: skip
    got = await receive(app_sessionmaker, msg.as_bytes(), store=store, file_max_bytes=MAX)
    assert got.outcome is Outcome.created
    ticket = ok(await client.get(f"{T}/{got.ticket_id}", headers=w["admin"]))
    [first] = [m for m in ticket["messages"] if m["kind"] == "customer"]
    assert [(f["name"], f["mime"], f["size"]) for f in first["attachments"]] == [
        ("tear.png", "image/png", 10),
    ]  # fmt: skip
    assert any("videos.zip" in m["body"] for m in ticket["messages"] if m["kind"] == "system")
    link = ok(await client.get(f"/api/v1/attachments/{first['attachments'][0]['id']}",
                               headers=w["admin"]))  # fmt: skip
    assert (await client.get(link["url"])).content == b"\x89PNG photo"

    # Their answer, with a file, joins the same ticket with it.
    again = customer_email(w["domain"], mid="second", reply_to="<first@mail.example.com>")
    again.add_attachment(b"%PDF receipt", maintype="application", subtype="pdf",
                         filename="receipt.pdf")  # fmt: skip
    got2 = await receive(app_sessionmaker, again.as_bytes(), store=store, file_max_bytes=MAX)
    assert got2.ticket_id == got.ticket_id
    ticket = ok(await client.get(f"{T}/{got.ticket_id}", headers=w["admin"]))
    customer = [m for m in ticket["messages"] if m["kind"] == "customer"]
    assert [f["name"] for f in customer[-1]["attachments"]] == ["receipt.pdf"]
    # A second delivery of the same email adds no second copy.
    dup = await receive(app_sessionmaker, again.as_bytes(), store=store, file_max_bytes=MAX)
    assert dup.outcome is Outcome.duplicate
    files = ok(await client.get(f"{T}/{got.ticket_id}/attachments", headers=w["admin"]))
    assert sorted(f["name"] for f in files["attachments"]) == ["receipt.pdf", "tear.png"]
