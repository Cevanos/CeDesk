"""Email rules (md C9, 7.4) with no database or network: which address a reply comes from,
its subject, and the headers that keep it in the customer's thread.

An email message's `hd_message.email` holds:
    {"message_id": "<...>", "from": "a@b.co", "to": ["support@acme.in"], "cc": [...],
     "subject": "...", "in_reply_to": "<...>" | null, "references": ["<...>", ...]}
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

NAME_MAX = 80
SIGNATURE_MAX = 2_000
MAX_ADDRESSES = 20
MAX_REFERENCES = 20  # older ids are dropped; mail clients thread on the newest ones
MAX_ATTEMPTS = 6
# Wait after attempt n (1-based) before the next try.
RETRY_AFTER_SECONDS = (60, 5 * 60, 15 * 60, 60 * 60, 4 * 60 * 60)

_RE = re.compile(r"^\s*(?:re|aw|sv)\s*:\s*", re.IGNORECASE)
# Deliberately simple: one @, a dot in the domain, no spaces or angle brackets. The mail
# server is the real judge; this only stops typos and header injection.
_ADDRESS = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\".]+$")


def normalise_address(raw: str) -> str | None:
    """The address in lower case, or None when it does not look like one."""
    value = raw.strip().lower()
    if not 3 <= len(value) <= 254 or not _ADDRESS.match(value):
        return None
    return value


@dataclass(frozen=True)
class Address:
    address: str
    name: str
    signature: str
    is_default: bool


def reply_subject(subject: str, label: str) -> str:
    """'Re: <subject> [T-1001]'. The label lets a reply find its ticket even when a mail
    client drops the threading headers."""
    base = _RE.sub("", subject).strip() or "Your request"
    if f"[{label}]" not in base:
        base = f"{base} [{label}]"
    return f"Re: {base}"


def addresses_in(value: Any) -> list[str]:
    """`to` or `cc` from a stored email: a list, or (older rows) one string."""
    if isinstance(value, str):
        return [value.lower()] if value else []
    if isinstance(value, list):
        return [str(v).lower() for v in value if v]
    return []


def pick_from(addresses: Sequence[Address], written_to: Sequence[str]) -> Address | None:
    """The address the customer wrote to, if it is one of ours; else the default one."""
    ours = {a.address: a for a in addresses}
    for to in written_to:
        if to.lower() in ours:
            return ours[to.lower()]
    return next((a for a in addresses if a.is_default), None)


def cc_for_reply(first_email: Mapping[str, Any] | None, ours: set[str], customer: str) -> list[str]:
    """CC from the customer's first email that had one, without our own addresses or the
    customer (they are in To)."""
    if not first_email:
        return []
    seen: list[str] = []
    for a in addresses_in(first_email.get("cc")):
        if a not in ours and a != customer.lower() and a not in seen:
            seen.append(a)
    return seen[:MAX_ADDRESSES]


def thread(last: Mapping[str, Any] | None) -> tuple[str | None, list[str]]:
    """In-Reply-To and References for a reply to the customer's latest email."""
    if not last or not last.get("message_id"):
        return None, []
    refs = [str(r) for r in last.get("references") or [] if r]
    refs.append(str(last["message_id"]))
    return str(last["message_id"]), refs[-MAX_REFERENCES:]


def new_message_id(from_address: str) -> str:
    domain = from_address.rsplit("@", 1)[-1] or "cedesk.local"
    return f"<{uuid4()}@{domain}>"


def with_signature(body: str, signature: str) -> str:
    signature = signature.strip()
    return f"{body}\n\n-- \n{signature}" if signature else body


def retry_after(attempts: int) -> int | None:
    """Seconds to wait after this many attempts; None: give up."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return RETRY_AFTER_SECONDS[min(attempts, len(RETRY_AFTER_SECONDS)) - 1]
