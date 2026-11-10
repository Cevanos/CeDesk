"""Email in (md C8) without a database: reading an email, its visible text, the ticket
label, automatic emails, the IMAP mailbox, and the email-in settings."""

import imaplib
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from cedesk import mail
from cedesk.helpdesk.domain import inbound
from cedesk.mail import ImapMailbox, MailboxError
from cedesk.settings import Env, Settings


def raw(
    body: str = "My T-shirt arrived torn.",
    *,
    html: str | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    base = {
        "From": "Priya Sharma <Priya@Example.com>",
        "To": "Acme Support <support@acme.in>",
        "Subject": "Damaged T-shirt",
        "Message-ID": "<m1@mail.gmail.com>",
    }
    for key, value in (base | (headers or {})).items():
        if value:
            msg[key] = value
    if html is not None and body:
        msg.set_content(body)
        msg.add_alternative(html, subtype="html")
    elif html is not None:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


def test_reads_the_people_subject_and_ids() -> None:
    email = inbound.parse(raw(headers={
        "Cc": "Rahul <Rahul@X.com>, support@acme.in",
        "In-Reply-To": "<a@acme.in>",
        "References": "<z@gmail.com> <a@acme.in>",
    }))  # fmt: skip
    assert email.from_address == "priya@example.com"
    assert email.from_name == "Priya Sharma"
    assert email.to == ["support@acme.in"]
    assert email.cc == ["rahul@x.com", "support@acme.in"]
    assert email.subject == "Damaged T-shirt"
    assert email.message_id == "<m1@mail.gmail.com>"
    assert email.in_reply_to == "<a@acme.in>"
    assert email.references == ["<z@gmail.com>", "<a@acme.in>"]
    assert email.thread_ids() == ["<a@acme.in>", "<z@gmail.com>"]
    assert email.text == "My T-shirt arrived torn."
    assert email.auto is False
    assert email.stored()["to"] == ["support@acme.in"]


def test_name_falls_back_to_the_address_and_hindi_survives() -> None:
    email = inbound.parse(raw("मेरा ऑर्डर कहाँ है?", headers={
        "From": "neha.k@example.com", "Subject": "=?utf-8?b?4KSG4KSw4KWN4KSh4KSw?=",
    }))  # fmt: skip
    assert email.from_name == "neha.k"
    assert email.subject == "आर्डर"
    assert email.text == "मेरा ऑर्डर कहाँ है?"


def test_plain_text_is_preferred_and_html_only_is_turned_into_text() -> None:
    both = inbound.parse(raw("Plain wins", html="<p>HTML loses</p>"))
    assert both.text == "Plain wins"
    only_html = inbound.parse(raw("", html=(
        "<html><head><style>p{}</style></head><body><p>Hi&nbsp;team,</p>"
        "<div>Order <b>#1042</b> is late.</div><script>x()</script></body></html>"
    )))  # fmt: skip
    assert only_html.text == "Hi\xa0team,\nOrder #1042 is late."


@pytest.mark.parametrize(
    "body",
    [
        "Still waiting.\n\nOn Mon, 10 Nov 2026 at 10:00, Acme Support <support@acme.in> wrote:\n"
        "> Hi Priya, it ships today.",
        "Still waiting.\n\nOn Mon, 10 Nov 2026 at 10:00, Acme Support\n<support@acme.in> wrote:\n"
        "> Hi Priya",  # Gmail wraps the line
        "Still waiting.\n\n-----Original Message-----\nFrom: Acme\nHi Priya",
        "Still waiting.\n\n________________________________\nFrom: Acme\nHi Priya",
        "Still waiting.\n\n> Hi Priya, it ships today.\n>",
    ],
)
def test_quoted_history_is_cut_off(body: str) -> None:
    assert inbound.parse(raw(body)).text == "Still waiting."


def test_a_forward_or_a_quote_only_email_keeps_its_text() -> None:
    forward = "From: Rahul <r@x.com>\nSubject: order\n\nPlease help my friend."
    assert inbound.parse(raw(forward)).text == forward
    assert inbound.parse(raw("> only a quote")).text == "> only a quote"
    assert inbound.parse(raw("   ")).text == "(no text)"


def test_without_a_message_id_the_same_email_gets_the_same_id() -> None:
    one = inbound.parse(raw(headers={"Message-ID": ""}))
    two = inbound.parse(raw(headers={"Message-ID": ""}))
    assert one.message_id == two.message_id
    assert one.message_id.endswith("@cedesk.no-message-id>")


@pytest.mark.parametrize(
    ("headers", "auto"),
    [
        ({"Auto-Submitted": "auto-replied"}, True),
        ({"Auto-Submitted": "no"}, False),
        ({"Precedence": "bulk"}, True),
        ({"X-Autoreply": "yes"}, True),
        ({"List-Id": "<news.shop.com>"}, True),
        ({"From": "MAILER-DAEMON@mx.google.com"}, True),
        ({"From": "noreply@shop.com"}, True),
        ({}, False),
    ],
)
def test_automatic_emails_are_spotted(headers: dict[str, str], auto: bool) -> None:
    assert inbound.parse(raw(headers=headers)).auto is auto


def test_unreadable_emails_are_refused() -> None:
    with pytest.raises(inbound.InboundError):
        inbound.parse(raw(headers={"From": "not an address"}))
    with pytest.raises(inbound.InboundError):
        inbound.parse(b"\x00\x01 just bytes")


def test_ticket_label_in_the_subject() -> None:
    assert inbound.ticket_number("Re: Damaged T-shirt [T-1001]") == 1001
    assert inbound.ticket_number("Order T-1001") is None  # only our [T-n] label
    assert inbound.ticket_number("Hello") is None


def test_long_parts_are_cut() -> None:
    email = inbound.parse(raw("x" * 60_000, headers={"Subject": "s" * 300}))
    assert len(email.text) == inbound.TEXT_MAX
    assert len(email.subject) == inbound.SUBJECT_MAX


class FakeImap:
    """Stands in for imaplib.IMAP4_SSL."""

    log: ClassVar[list[str]] = []
    unseen: ClassVar[dict[bytes, bytes]] = {}
    refuse: ClassVar[bool] = False

    def __init__(self, host: str, port: int, **_: Any) -> None:
        self.log.append(f"connect {host}:{port}")

    def login(self, user: str, password: str) -> None:
        if self.refuse:
            raise imaplib.IMAP4.error("bad")
        self.log.append(f"login {user}")

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def uid(self, command: str, *args: str | bytes) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            return "OK", [b" ".join(self.unseen)]
        if command == "FETCH":
            uid = args[0]
            assert args[1] == "(BODY.PEEK[])"  # peek: fetching alone never marks it read
            return "OK", [(b"1 (UID " + bytes(uid) + b")", self.unseen[bytes(uid)]), b")"]
        self.log.append(f"{command} {' '.join(str(a) for a in args)}")
        return "OK", []

    def logout(self) -> None:
        self.log.append("logout")


def mailbox(monkeypatch: pytest.MonkeyPatch, refuse: bool = False) -> ImapMailbox:
    FakeImap.log, FakeImap.unseen, FakeImap.refuse = [], {b"7": b"one", b"9": b"two"}, refuse
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", FakeImap)
    return ImapMailbox(Settings(imap_host="imap.example.com", imap_username="me",
                                imap_password="pw"))  # type: ignore[arg-type]  # fmt: skip


async def test_imap_fetches_unread_then_marks_only_handled_ones_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = mailbox(monkeypatch)
    assert await box.fetch() == [("7", b"one"), ("9", b"two")]
    await box.done(["7"])
    assert "STORE 7 +FLAGS (\\Seen)" in FakeImap.log  # only the handled one
    assert FakeImap.log.count("logout") == 2


async def test_imap_sign_in_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MailboxError, match="refused the sign-in"):
        await mailbox(monkeypatch, refuse=True).fetch()


def test_make_mailbox_only_with_a_host() -> None:
    assert mail.make_mailbox(Settings()) is None
    assert isinstance(mail.make_mailbox(Settings(imap_host="h")), ImapMailbox)


def test_email_in_settings_problems() -> None:
    no_login = Settings(imap_host="imap.gmail.com").mail_problems()
    assert len(no_login) == 1 and "CEDESK_IMAP_USERNAME" in no_login[0]
    short = Settings(
        env=Env.staging,
        secret_key="x" * 40,  # type: ignore[arg-type]
        database_url="postgresql+asyncpg://cedesk_app:real@db/cedesk",
        inbound_email_token="short",  # type: ignore[arg-type]
    ).unsafe()
    assert len(short) == 1 and "CEDESK_INBOUND_EMAIL_TOKEN" in short[0]


def _with_files() -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "priya@example.com"
    msg["To"] = "support@acme.test"
    msg["Subject"] = "Photos"
    msg.set_content("See attached.")
    msg.add_alternative("<p>See attached.</p>", subtype="html")
    return msg


def test_attached_files_are_read_in_order() -> None:
    msg = _with_files()
    msg.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="tear.png")
    msg.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="receipt.pdf")
    files = inbound.parse(msg.as_bytes()).files
    assert [(f.name, f.mime, f.data) for f in files] == [
        ("tear.png", "image/png", b"\x89PNG"),
        ("receipt.pdf", "application/pdf", b"%PDF"),
    ]
    assert inbound.parse(msg.as_bytes()).text == "See attached."  # the body is not a file


def test_signature_pictures_are_not_files() -> None:
    msg = _with_files()
    html = msg.get_body(preferencelist=("html",))
    assert html is not None
    html.add_related(b"GIF89a", maintype="image", subtype="gif", cid="<logo@acme>")
    assert inbound.parse(msg.as_bytes()).files == ()


def test_a_forwarded_email_is_kept_whole() -> None:
    inner = EmailMessage()
    inner["From"] = "courier@example.com"
    inner["Subject"] = "Delivery failed"
    inner.set_content("We could not deliver.")
    msg = _with_files()
    msg.add_attachment(inner)
    [file] = inbound.parse(msg.as_bytes()).files
    assert (file.name, file.mime) == ("forwarded-email.eml", "message/rfc822")
    assert b"We could not deliver." in file.data


def test_no_files_in_a_plain_email() -> None:
    assert inbound.parse(_with_files().as_bytes()).files == ()
