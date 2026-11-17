"""No mail path puts an address, a subject, a body or a sign-in code in the logs."""

import logging
import smtplib
import stat
from pathlib import Path
from typing import Any

import pytest

from cedesk import logs, mail
from cedesk.mail import LogMailer, NoMailer, OutgoingEmail, SmtpMailer
from cedesk.settings import Env, MailBackend, Settings

CODE = "483920"
EMAIL = OutgoingEmail(
    "support@acme.in", "Acme", ["jane.customer@example.com"],
    f"Your sign-in code {CODE}", f"Use {CODE} or https://x.test/reset?token=abc123",
    cc=["boss@example.com"],
)  # fmt: skip
PRIVATE = ("jane.customer", "boss@example.com", CODE, "abc123", "sign-in code")


def rendered(caplog: pytest.LogCaptureFixture) -> str:
    """Every captured record as the JSON line production would write."""
    fmt = logs.JsonFormatter()
    return "\n".join(fmt.format(r) for r in caplog.records)


def assert_private(text: str) -> None:
    for bit in PRIVATE:
        assert bit not in text, bit


async def test_the_dev_outbox_keeps_the_email_out_of_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="cedesk")
    outbox = tmp_path / "outbox"
    await LogMailer(str(outbox)).send(EMAIL)
    (written,) = outbox.iterdir()
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o700
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert CODE in written.read_text()  # the developer can still read it, in the file
    line = rendered(caplog)
    assert written.name in line
    assert_private(line)


async def test_no_mail_server_logs_a_count_not_the_addresses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="cedesk")
    await NoMailer().send(EMAIL)
    line = rendered(caplog)
    assert '"count": 2' in line
    assert_private(line)


class RefusingSmtp:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "RefusingSmtp":
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def starttls(self, context: Any) -> None:
        pass

    def send_message(self, msg: Any) -> dict[str, Any]:
        return {"boss@example.com": (550, b"5.1.1 <boss@example.com> no such user")}


async def test_a_refused_recipient_is_counted_not_named(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="cedesk")
    monkeypatch.setattr(mail.smtplib, "SMTP", RefusingSmtp)
    await SmtpMailer(Settings(mail_backend=MailBackend.smtp, smtp_host="h")).send(EMAIL)
    line = rendered(caplog)
    assert '"count": 1' in line
    assert_private(line)


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPRecipientsRefused({"jane.customer@example.com": (550, b"no")}),
        smtplib.SMTPResponseException(550, b"5.1.1 <jane.customer@example.com> unknown"),
        smtplib.SMTPServerDisconnected("lost jane.customer@example.com"),
    ],
)
async def test_a_failed_send_never_carries_the_server_text(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    class Failing(RefusingSmtp):
        def send_message(self, msg: Any) -> dict[str, Any]:
            raise error

    monkeypatch.setattr(mail.smtplib, "SMTP", Failing)
    with pytest.raises(mail.SendError) as caught:
        await SmtpMailer(Settings(mail_backend=MailBackend.smtp, smtp_host="h")).send(EMAIL)
    assert "jane" not in str(caught.value)


def test_the_log_drops_mail_fields_even_if_code_passes_them() -> None:
    record = logging.LogRecord("cedesk.x", logging.INFO, __file__, 1, "email", None, None)
    record.mail_to = ["jane.customer@example.com"]
    record.mail_text = CODE
    assert_private(logs.JsonFormatter().format(record))


def test_only_a_developer_machine_may_keep_emails() -> None:
    assert Settings(env=Env.dev).mail_problems() == []
    assert Settings(env=Env.staging).mail_problems()
    assert Settings(env=Env.production).mail_problems()
