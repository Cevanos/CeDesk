"""Email out (md C9) without a database: the From choice, CC, subject, threading headers,
the retry plan, the message we build, SMTP errors, and the mail settings."""

import smtplib
from typing import Any, ClassVar

import pytest

from cedesk import mail
from cedesk.helpdesk.domain import email as rules
from cedesk.mail import OutgoingEmail, SendError, SmtpMailer, build
from cedesk.settings import Env, MailBackend, Settings

SUPPORT = rules.Address("support@acme.in", "Acme Support", "", is_default=True)
BILLING = rules.Address("billing@acme.in", "Acme Billing", "", is_default=False)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Damaged T-shirt", "Re: Damaged T-shirt [T-1001]"),
        ("Re: Damaged T-shirt", "Re: Damaged T-shirt [T-1001]"),
        ("RE:  AW: Damaged", "Re: AW: Damaged [T-1001]"),  # one prefix comes off
        ("Re: Damaged T-shirt [T-1001]", "Re: Damaged T-shirt [T-1001]"),  # label once
        ("", "Re: Your request [T-1001]"),
    ],
)
def test_reply_subject(subject: str, expected: str) -> None:
    assert rules.reply_subject(subject, "T-1001") == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Support@Acme.IN ", "support@acme.in"),
        ("a.b+c@mail.acme.co.in", "a.b+c@mail.acme.co.in"),
        ("support", None),
        ("support@acme", None),
        ("two@at@acme.in", None),
        ("has space@acme.in", None),
        ("x@acme.in\r\nBcc: evil@x.com", None),  # no header injection
        ("<x@acme.in>", None),
        ("x@acme.in.", None),
    ],
)
def test_normalise_address(raw: str, expected: str | None) -> None:
    assert rules.normalise_address(raw) == expected


def test_reply_comes_from_the_address_the_customer_wrote_to() -> None:
    assert rules.pick_from([SUPPORT, BILLING], ["Billing@Acme.in"]) == BILLING
    assert rules.pick_from([SUPPORT, BILLING], ["someone@else.com"]) == SUPPORT  # default
    assert rules.pick_from([SUPPORT, BILLING], []) == SUPPORT
    assert rules.pick_from([BILLING], []) is None  # no default: nothing to send from
    assert rules.pick_from([], ["billing@acme.in"]) is None


def test_cc_keeps_others_but_not_us_or_the_customer() -> None:
    first = {"cc": ["Rahul@x.com", "support@acme.in", "priya@example.com", "rahul@x.com"]}
    assert rules.cc_for_reply(first, {"support@acme.in"}, "Priya@example.com") == ["rahul@x.com"]
    assert rules.cc_for_reply(None, set(), "p@x.com") == []
    assert rules.cc_for_reply({"cc": "one@x.com"}, set(), "p@x.com") == ["one@x.com"]  # old shape
    many = {"cc": [f"p{i}@x.com" for i in range(30)]}
    assert len(rules.cc_for_reply(many, set(), "c@x.com")) == rules.MAX_ADDRESSES


def test_threading_headers_follow_the_customers_latest_email() -> None:
    assert rules.thread(None) == (None, [])
    assert rules.thread({"subject": "no id"}) == (None, [])
    last = {"message_id": "<3@x>", "references": ["<1@x>", "<2@x>"]}
    assert rules.thread(last) == ("<3@x>", ["<1@x>", "<2@x>", "<3@x>"])
    long = {"message_id": "<new@x>", "references": [f"<{i}@x>" for i in range(40)]}
    in_reply_to, refs = rules.thread(long)
    assert in_reply_to == "<new@x>"
    assert len(refs) == rules.MAX_REFERENCES and refs[-1] == "<new@x>"


def test_message_id_uses_our_domain() -> None:
    mid = rules.new_message_id("support@acme.in")
    assert mid.startswith("<") and mid.endswith("@acme.in>")
    assert mid != rules.new_message_id("support@acme.in")


def test_signature_goes_under_a_separator() -> None:
    assert rules.with_signature("Hi", "  ") == "Hi"
    assert rules.with_signature("Hi", "Neha\nAcme") == "Hi\n\n-- \nNeha\nAcme"


def test_retries_wait_longer_each_time_then_give_up() -> None:
    waits = [rules.retry_after(n) for n in range(1, rules.MAX_ATTEMPTS + 1)]
    assert waits == [60, 300, 900, 3600, 14400, None]


def test_built_email_has_the_threading_headers() -> None:
    msg = build(
        OutgoingEmail(
            from_address="support@acme.in",
            from_name="Acme Support",
            to=["priya@example.com"],
            cc=["rahul@x.com"],
            subject="Re: Damaged [T-1001]",
            text="Hello Priya",
            message_id="<a@acme.in>",
            in_reply_to="<c@gmail.com>",
            references=["<b@gmail.com>", "<c@gmail.com>"],
        )
    )
    assert msg["From"] == "Acme Support <support@acme.in>"
    assert msg["To"] == "priya@example.com"
    assert msg["Cc"] == "rahul@x.com"
    assert msg["Message-ID"] == "<a@acme.in>"
    assert msg["In-Reply-To"] == "<c@gmail.com>"
    assert msg["References"] == "<b@gmail.com> <c@gmail.com>"
    assert msg["Date"]
    assert msg.get_content().strip() == "Hello Priya"


class FakeSmtp:
    """Stands in for smtplib.SMTP: records what happens, or raises `fail`."""

    fail: ClassVar[Exception | None] = None
    seen: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.seen.append(f"connect {host}:{port}")
        if isinstance(self.fail, OSError):
            raise self.fail

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def starttls(self, context: Any) -> None:
        self.seen.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.seen.append(f"login {user}")

    def send_message(self, msg: Any) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        self.seen.append(f"send {msg['To']}")
        return {}


def smtp_mailer(monkeypatch: pytest.MonkeyPatch, fail: Exception | None) -> SmtpMailer:
    FakeSmtp.fail, FakeSmtp.seen = fail, []
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSmtp)
    return SmtpMailer(
        Settings(
            mail_backend=MailBackend.smtp,
            smtp_host="smtp.example.com",
            smtp_username="apikey",
            smtp_password="secret",  # type: ignore[arg-type]
        )
    )


EMAIL = OutgoingEmail("support@acme.in", "Acme", ["p@example.com"], "Hi", "Hello")


async def test_smtp_signs_in_over_starttls_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    await smtp_mailer(monkeypatch, None).send(EMAIL)
    assert FakeSmtp.seen == [
        "connect smtp.example.com:587", "starttls", "login apikey", "send p@example.com",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (ConnectionRefusedError(), False),  # server down: try again later
        (smtplib.SMTPServerDisconnected("bye"), False),
        (smtplib.SMTPResponseException(421, b"busy"), False),
        (smtplib.SMTPResponseException(554, b"rejected"), True),
        (smtplib.SMTPAuthenticationError(535, b"bad"), True),
        (smtplib.SMTPRecipientsRefused({"p@example.com": (550, b"no")}), True),
    ],
)
async def test_smtp_errors_say_whether_to_retry(
    monkeypatch: pytest.MonkeyPatch, error: Exception, permanent: bool
) -> None:
    with pytest.raises(SendError) as caught:
        await smtp_mailer(monkeypatch, error).send(EMAIL)
    assert caught.value.permanent is permanent
    assert "secret" not in str(caught.value)


def test_make_mailer_picks_the_backend() -> None:
    assert isinstance(mail.make_mailer(Settings()), mail.LogMailer)  # the default
    assert isinstance(mail.make_mailer(Settings(mail_backend=MailBackend.none)), mail.NoMailer)
    smtp = Settings(mail_backend=MailBackend.smtp, smtp_host="h")
    assert isinstance(mail.make_mailer(smtp), mail.SmtpMailer)


def test_mail_settings_problems() -> None:
    assert Settings().mail_problems() == []  # dev: logging emails is fine
    assert Settings(env=Env.staging).mail_problems() == []  # the test server too
    prod = Settings(env=Env.production).mail_problems()
    assert len(prod) == 1 and "CEDESK_MAIL_BACKEND" in prod[0]
    assert Settings(env=Env.production, mail_backend=MailBackend.none).mail_problems() == []
    no_host = Settings(mail_backend=MailBackend.smtp).mail_problems()
    assert len(no_host) == 1 and "CEDESK_SMTP_HOST" in no_host[0]


def test_the_api_starts_in_production_without_mail_settings() -> None:
    """Mail settings stop only the worker, never the API."""
    s = Settings(
        env=Env.production,
        secret_key="x" * 40,  # type: ignore[arg-type]
        database_url="postgresql+asyncpg://cedesk_app:real@db/cedesk",
    )
    assert s.unsafe() == []
