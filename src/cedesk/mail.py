"""Sending and fetching email (md 7.4). One small interface each, so the rest of the code
never knows which service is behind it.

Sending:

- `log`: writes the email to the log and sends nothing. For a developer's machine and the
  test server; refused in production, because emails will carry sign-in codes.
- `smtp`: any SMTP server (Amazon SES, Gmail, Zoho, Mailgun, Postmark, ...).
- `none`: drops every email (production before a mail server is set up).

Fetching: `ImapMailbox` reads unread emails from one IMAP folder (Gmail, Zoho, ...).
"""

import asyncio
import imaplib
import logging
import os
import smtplib
import ssl
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from pathlib import Path
from typing import Protocol

from cedesk.settings import MailBackend, Settings, SmtpSecurity

logger = logging.getLogger("cedesk.mail")


@dataclass(frozen=True)
class MailFile:
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class OutgoingEmail:
    from_address: str
    from_name: str
    to: list[str]
    subject: str
    text: str
    cc: list[str] = field(default_factory=list)
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[MailFile] = field(default_factory=list)


class SendError(Exception):
    """`permanent`: trying again will not help (address refused, bad sign-in)."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Mailer(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...


def build(email: OutgoingEmail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((email.from_name, email.from_address))
    msg["To"] = ", ".join(email.to)
    if email.cc:
        msg["Cc"] = ", ".join(email.cc)
    msg["Subject"] = email.subject
    msg["Date"] = format_datetime(datetime.now(UTC))
    if email.message_id:
        msg["Message-ID"] = email.message_id
    if email.in_reply_to:
        msg["In-Reply-To"] = email.in_reply_to
    if email.references:
        msg["References"] = " ".join(email.references)
    msg.set_content(email.text)
    for file in email.attachments:
        maintype, _, subtype = file.mime.partition("/")
        msg.add_attachment(
            file.data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=file.name,
        )
    return msg


class LogMailer:
    """A developer's machine only (settings refuse it elsewhere): each email is written as a
    .eml file, readable only by this user, to `outbox`. The log names the file and never
    holds the email itself: emails carry sign-in codes, reset links and customer text."""

    def __init__(self, outbox: str = "mail-outbox") -> None:
        self.outbox = Path(outbox)

    async def send(self, email: OutgoingEmail) -> None:
        name = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.eml"
        await asyncio.to_thread(self._write, name, build(email).as_bytes())
        logger.info("email written to the dev outbox, not sent", extra={"event": name})

    def _write(self, name: str, data: bytes) -> None:
        self.outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.outbox / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)


class NoMailer:
    async def send(self, email: OutgoingEmail) -> None:
        # How many, never who: an address is customer data.
        logger.warning(
            "email dropped: no mail server set up",
            extra={"count": len(email.to) + len(email.cc)},
        )


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.security = settings.smtp_security
        self.username = settings.smtp_username
        self.password = settings.smtp_password
        self.timeout = settings.smtp_timeout_seconds

    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send, build(email))

    def _send(self, msg: EmailMessage) -> None:
        context = ssl.create_default_context()
        try:
            smtp: smtplib.SMTP
            if self.security is SmtpSecurity.ssl:
                smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with smtp:
                if self.security is SmtpSecurity.starttls:
                    smtp.starttls(context=context)
                if self.username and self.password:
                    smtp.login(self.username, self.password.get_secret_value())
                refused = smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise SendError("The mail server refused the sign-in.", permanent=True) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise SendError("The mail server refused every recipient.", permanent=True) from exc
        except smtplib.SMTPResponseException as exc:
            # 5xx: the server will not take it; 4xx: try again later.
            raise SendError(
                f"The mail server said {exc.smtp_code}.", permanent=exc.smtp_code >= 500
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise SendError(f"Could not reach the mail server ({type(exc).__name__}).") from exc
        if refused:
            # How many were refused, never which addresses (customer data).
            logger.warning("some recipients refused", extra={"count": len(refused)})


def make_mailer(settings: Settings) -> Mailer:
    if settings.mail_backend is MailBackend.smtp:
        return SmtpMailer(settings)
    if settings.mail_backend is MailBackend.none:
        return NoMailer()
    return LogMailer(settings.mail_outbox_dir)


class MailboxError(Exception):
    pass


class ImapMailbox:
    """Unread emails in one folder, oldest first, at most `batch` per pass. They are only
    marked read (`done`) after they were handled, so nothing is lost if handling fails."""

    def __init__(self, settings: Settings, batch: int = 50) -> None:
        self.host = settings.imap_host
        self.port = settings.imap_port
        self.username = settings.imap_username or ""
        self.password = settings.imap_password
        self.folder = settings.imap_folder
        self.batch = batch
        self.timeout = settings.smtp_timeout_seconds

    def _open(self) -> imaplib.IMAP4_SSL:
        try:
            box = imaplib.IMAP4_SSL(
                self.host, self.port, ssl_context=ssl.create_default_context(),
                timeout=self.timeout,
            )  # fmt: skip
            secret = self.password.get_secret_value() if self.password else ""
            box.login(self.username, secret)
            status, _ = box.select(self.folder)
            if status != "OK":
                raise MailboxError(f"No folder {self.folder!r} in the mailbox.")
        except imaplib.IMAP4.error as exc:
            raise MailboxError("The mail server refused the sign-in.") from exc
        except OSError as exc:
            raise MailboxError(f"Could not reach the mail server ({type(exc).__name__}).") from exc
        return box

    def _fetch(self) -> list[tuple[str, bytes]]:
        box = self._open()
        try:
            status, data = box.uid("SEARCH", "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []
            found: list[tuple[str, bytes]] = []
            for uid in data[0].split()[: self.batch]:
                status, parts = box.uid("FETCH", uid, "(BODY.PEEK[])")
                for part in parts if status == "OK" else []:
                    if isinstance(part, tuple) and isinstance(part[1], bytes):
                        found.append((uid.decode(), part[1]))
                        break
            return found
        finally:
            _close(box)

    def _done(self, ids: list[str]) -> None:
        box = self._open()
        try:
            box.uid("STORE", ",".join(ids), "+FLAGS", "(\\Seen)")
        finally:
            _close(box)

    async def fetch(self) -> list[tuple[str, bytes]]:
        return await asyncio.to_thread(self._fetch)

    async def done(self, ids: Sequence[str]) -> None:
        await asyncio.to_thread(self._done, list(ids))


def _close(box: imaplib.IMAP4) -> None:
    try:
        box.logout()
    except (OSError, imaplib.IMAP4.error):
        pass


def make_mailbox(settings: Settings) -> ImapMailbox | None:
    return ImapMailbox(settings) if settings.imap_host else None
