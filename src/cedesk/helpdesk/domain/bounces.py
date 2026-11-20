"""Reading bounce and spam-report emails (feature-gap plan 6.4), no database or network.

A campaign email's envelope sender is its support address, so the mail server's
bounce (a delivery status notification, RFC 3464) or a mailbox provider's spam report
(a feedback loop report, ARF, RFC 5965) comes back to that address and in through CeDesk
like any email (IMAP or the inbound webhook). `read` finds:

- what it is: `hard` (Action: failed with a 5.x.x status), `soft` (failed with 4.x.x, or
  delayed: nothing is done with it) or `complaint` (a feedback report);
- the address(es) it is about (Final-Recipient / Original-Rcpt-To);
- the Message-ID of the email it is about, from the returned headers or message. That is
  what ties it to one campaign recipient: an address alone is never enough.

Anything else (an out-of-office, a bounce in free text only) is None and stays an
ignored automatic email, as before.
"""

import contextlib
import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser, HeaderParser
from enum import StrEnum

from cedesk.helpdesk.domain.email import normalise_address

_ID = re.compile(r"<[^<>\s]{1,250}>")
MAX_RECIPIENTS = 20


class Kind(StrEnum):
    hard = "hard"
    soft = "soft"
    complaint = "complaint"


@dataclass(frozen=True)
class Report:
    kind: Kind
    recipients: tuple[str, ...]
    message_ids: tuple[str, ...]


def _address(value: str | None) -> str | None:
    """`rfc822; jane@example.com` -> jane@example.com."""
    if not value:
        return None
    raw = value.split(";", 1)[-1].strip().strip("<>")
    return normalise_address(raw)


def _ids_from_headers(text: str) -> list[str]:
    headers = HeaderParser(policy=policy.default).parsestr(text, headersonly=True)
    return _ID.findall(str(headers.get("Message-ID", "")))


def _original_ids(report: EmailMessage) -> list[str]:
    """The Message-ID of the email the report is about."""
    for part in report.iter_parts():
        kind = part.get_content_type()
        if kind in ("text/rfc822-headers", "text/rfc822-header"):
            payload = part.get_payload(decode=True)
            text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
            return _ids_from_headers(text or str(part.get_payload()))
        if kind == "message/rfc822":
            inner = part.get_payload(0) if part.is_multipart() else None
            if isinstance(inner, Message):
                return _ID.findall(str(inner.get("Message-ID", "")))
    return []


def _status_blocks(part: Message) -> list[Message]:
    """The per-recipient blocks of a message/delivery-status part."""
    payload = part.get_payload()
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, Message)][1:]
    if isinstance(payload, str):  # some parsers leave it as text
        blocks = re.split(r"\r?\n\r?\n", payload.strip())
        return [HeaderParser().parsestr(b) for b in blocks[1:]]
    return []


def _delivery(report: EmailMessage) -> Report | None:
    for part in report.iter_parts():
        if part.get_content_type() != "message/delivery-status":
            continue
        hard: list[str] = []
        soft: list[str] = []
        for block in _status_blocks(part):
            action = str(block.get("Action", "")).strip().lower()
            status = str(block.get("Status", "")).strip()
            who = _address(str(block.get("Final-Recipient", "") or "")) or _address(
                str(block.get("Original-Recipient", "") or "")
            )
            if who is None:
                continue
            if action == "failed" and status.startswith("5"):
                hard.append(who)
            elif action in ("failed", "delayed"):
                soft.append(who)
        ids = tuple(_original_ids(report))
        if hard:
            return Report(Kind.hard, tuple(hard[:MAX_RECIPIENTS]), ids)
        if soft:
            return Report(Kind.soft, tuple(soft[:MAX_RECIPIENTS]), ids)
        return None
    return None


def _feedback(report: EmailMessage) -> Report | None:
    recipients: list[str] = []
    for part in report.iter_parts():
        if part.get_content_type() != "message/feedback-report":
            continue
        payload = part.get_payload()
        fields: Message
        if isinstance(payload, list) and payload and isinstance(payload[0], Message):
            fields = payload[0]
        else:
            raw = part.get_payload(decode=True)
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(payload)
            fields = HeaderParser().parsestr(text)
        for value in fields.get_all("Original-Rcpt-To", []) or []:
            who = _address(str(value))
            if who:
                recipients.append(who)
    return Report(Kind.complaint, tuple(recipients[:MAX_RECIPIENTS]), tuple(_original_ids(report)))


def read(raw: bytes) -> Report | None:
    """The bounce or spam report in this email, or None when it is neither."""
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        return None
    if not isinstance(msg, EmailMessage) or msg.get_content_type() != "multipart/report":
        return None
    report_type = str(msg.get_param("report-type", "") or "").lower()
    found: Report | None = None
    with contextlib.suppress(Exception):  # a broken report is just not a report
        if report_type == "feedback-report":
            found = _feedback(msg)
        else:
            found = _delivery(msg)
    return found
