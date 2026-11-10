"""Reading an incoming email (md C8, 7.4) with no database or network: the addresses,
the threading headers, the visible text (without the quoted history), the attached
files, the ticket label in the subject, and whether it is an automatic email that must not
make a ticket."""

import contextlib
import hashlib
import html
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

from cedesk.helpdesk.domain.attachments import ReceivedFile
from cedesk.helpdesk.domain.email import normalise_address

TEXT_MAX = 50_000
SUBJECT_MAX = 200
NAME_MAX = 120
MAX_IDS = 20

_ID = re.compile(r"<[^<>\s]{1,250}>")
_LABEL = re.compile(r"\[T-(\d{1,9})\]")
_TAG = re.compile(r"<[^>]*>")
_BLOCK = re.compile(r"<(br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", re.IGNORECASE)
_HIDDEN = re.compile(r"<(script|style|head)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
# Where a mail client starts the quoted earlier messages.
_QUOTE_START = (
    re.compile(r"^on\b.{0,300}\bwrote:\s*$", re.IGNORECASE),  # Gmail, Apple Mail
    re.compile(r"^-{2,}\s*original message\s*-{2,}\s*$", re.IGNORECASE),  # Outlook
    re.compile(r"^_{10,}\s*$"),  # Outlook web
    re.compile(r"^from:\s.+$", re.IGNORECASE),  # a forwarded/quoted header block
)


class InboundError(Exception):
    """The email can't be read; it is dropped."""


@dataclass(frozen=True)
class Inbound:
    message_id: str
    from_address: str
    from_name: str
    to: list[str]
    cc: list[str]
    subject: str
    text: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    auto: bool = False  # an auto-reply, bounce or mailing-list email
    files: tuple[ReceivedFile, ...] = ()

    def stored(self) -> dict[str, object]:
        """What goes in `hd_message.email`."""
        return {
            "message_id": self.message_id,
            "from": self.from_address,
            "to": self.to,
            "cc": self.cc,
            "subject": self.subject,
            "in_reply_to": self.in_reply_to,
            "references": self.references,
        }

    def thread_ids(self) -> list[str]:
        """Message-IDs this email answers, the most direct first."""
        ids = [self.in_reply_to] if self.in_reply_to else []
        ids += [r for r in reversed(self.references) if r not in ids]
        return ids[:MAX_IDS]


def ids_in(value: str | None) -> list[str]:
    return _ID.findall(value or "")


def _addresses(values: list[str]) -> list[str]:
    seen: list[str] = []
    for _, raw in getaddresses(values):
        address = normalise_address(raw)
        if address and address not in seen:
            seen.append(address)
    return seen[:20]


def html_to_text(value: str) -> str:
    value = _HIDDEN.sub("", value)
    value = _BLOCK.sub("\n", value)
    value = html.unescape(_TAG.sub("", value))
    lines = [line.strip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def visible_text(text: str) -> str:
    """The new part of a reply: cut at the first quoted-history marker, and drop trailing
    '>' lines. Falls back to the whole text when that would leave nothing."""
    lines = text.replace("\r\n", "\n").split("\n")
    cut = len(lines)
    for i, line in enumerate(lines):
        joined = line.strip()
        pair = f"{joined} {lines[i + 1].strip()}" if i + 1 < len(lines) else joined
        if any(p.match(joined) or (p is _QUOTE_START[0] and p.match(pair))
               for p in _QUOTE_START):  # fmt: skip
            if i > 0:  # a header block at the very top is the content of a forward
                cut = i
                break
    kept = lines[:cut]
    while kept and (not kept[-1].strip() or kept[-1].lstrip().startswith(">")):
        kept.pop()
    result = "\n".join(kept).strip()
    return result or text.strip()


def ticket_number(subject: str) -> int | None:
    """The number in a '[T-1001]' label we put in our replies' subjects."""
    found = _LABEL.search(subject)
    return int(found.group(1)) if found else None


def _is_auto(msg: EmailMessage, sender: str) -> bool:
    auto_submitted = str(msg.get("Auto-Submitted", "no")).strip().lower()
    precedence = str(msg.get("Precedence", "")).strip().lower()
    local = sender.split("@", 1)[0]
    return (
        auto_submitted != "no"
        or precedence in ("bulk", "junk", "list", "auto_reply")
        or any(
            h in msg
            for h in ("X-Autoreply", "X-Autorespond", "List-Id", "X-Auto-Response-Suppress")
        )
        or local in ("mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply")
        or msg.get_content_type() == "multipart/report"
    )


def _files(msg: EmailMessage) -> list[ReceivedFile]:
    """The attached files, in order. Not the text we read as the body, and not the small
    pictures shown inside the text (logos in signatures), which have a Content-ID."""
    body = msg.get_body(preferencelist=("plain", "html"))
    found: list[ReceivedFile] = []

    def visit(part: EmailMessage) -> None:
        if part is body:
            return
        kind = part.get_content_type()
        disposition = part.get_content_disposition()
        name = part.get_filename()
        if kind == "message/rfc822":  # a forwarded email, kept whole
            if disposition == "attachment" or name:
                inner = part.get_payload(0)
                if isinstance(inner, EmailMessage):
                    found.append(ReceivedFile(name or "forwarded-email.eml", kind,
                                              inner.as_bytes()))  # fmt: skip
            return
        if part.is_multipart():
            for sub in part.iter_parts():
                if isinstance(sub, EmailMessage):
                    visit(sub)
            return
        inline_picture = disposition != "attachment" and "Content-ID" in part
        if (disposition == "attachment" or name) and not inline_picture:
            data = part.get_payload(decode=True)
            if isinstance(data, bytes):
                found.append(ReceivedFile(name or "file", kind, data))

    # A broken part stops the search; the files read before it are kept, and the text
    # of the email still makes its message.
    with contextlib.suppress(Exception):
        visit(msg)
    return found


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):  # an unknown charset or broken encoding
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    if not isinstance(content, str):
        return ""
    return html_to_text(content) if part.get_content_type() == "text/html" else content


def parse(raw: bytes) -> Inbound:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:  # the parser is lenient; this is only for the truly broken
        raise InboundError("Not an email.") from exc
    if not isinstance(msg, EmailMessage):
        raise InboundError("Not an email.")
    try:
        name, raw_from = parseaddr(str(msg.get("From", "")))
        to_values = [str(v) for v in msg.get_all("To", [])]
        cc_values = [str(v) for v in msg.get_all("Cc", [])]
        subject = " ".join(str(msg.get("Subject", "")).split())
        message_ids = ids_in(str(msg.get("Message-ID", "")))
        in_reply_to = ids_in(str(msg.get("In-Reply-To", "")))
        references = ids_in(" ".join(str(v) for v in msg.get_all("References", [])))
    except Exception as exc:  # a header the parser can't decode
        raise InboundError("The email's headers can't be read.") from exc
    sender = normalise_address(raw_from)
    if sender is None:
        raise InboundError("The email has no sender address.")
    # Without a Message-ID, one made from the content still spots a second delivery.
    message_id = (
        message_ids[0]
        if message_ids
        else (f"<{hashlib.sha256(raw).hexdigest()[:40]}@cedesk.no-message-id>")
    )
    text = visible_text(_body(msg))[:TEXT_MAX].strip() or "(no text)"
    return Inbound(
        message_id=message_id,
        from_address=sender,
        from_name=" ".join(name.split())[:NAME_MAX] or sender.split("@", 1)[0][:NAME_MAX],
        to=_addresses(to_values),
        cc=_addresses(cc_values),
        subject=subject[:SUBJECT_MAX],
        text=text,
        in_reply_to=in_reply_to[0] if in_reply_to else None,
        references=references[-MAX_IDS:],
        auto=_is_auto(msg, sender),
        files=tuple(_files(msg)),
    )
