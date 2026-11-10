"""Cevanos connector rules (md C4-C7, section 7.1). No database, no HTTP.

Cevanos signs every webhook: header `CXOps-Signature: t=<unix>,v1=<hex>`, the hex being
HMAC-SHA256 with the endpoint's secret over `<t>.<raw body>`. The body is
`{"event", "event_id", "delivered_at", "data"}`; `data` carries ids and enums, never chat
text (Cevanos's rule), so CeDesk reads the chat itself with the `/v1` API key.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SIGNATURE_HEADER = "CXOps-Signature"
MAX_SKEW_SECONDS = 300
"""A signed request older (or newer) than this is refused, so a captured one can't be
replayed later."""
BODY_MAX = 256 * 1024

HANDLED_EVENTS = frozenset(
    {"conversation.escalated", "conversation.classified", "conversation.reopened",
     "conversation.closed"}
)  # fmt: skip
TAKEOVER_REASON = "manual_takeover"
"""Cevanos's reason when a person took the chat in its Inbox, including the take-over
CeDesk asks for on Accept. Not a handover to CeDesk, so no ticket."""

API_KEY = re.compile(r"^cvn_sk_[A-Za-z0-9_-]{8,200}$")
SECRET_MIN, SECRET_MAX = 16, 200
CONTACT_DOMAIN = "cevanos.invalid"
"""Cevanos's /v1 chats are anonymous for now (md 7.1): the ticket's contact is
`chat-<conversation>@cevanos.invalid`, a reserved domain that never receives mail."""
SUBJECT_MAX = 120
REPLY_MAX = 8000  # Cevanos's limit on one reply
POLL_SECONDS = 5
POLL_BACKOFF_SECONDS = (5, 15, 60, 300)
"""Wait before the next read of a chat after 0, 1, 2, 3+ failures in a row."""
POLL_LEASE_SECONDS = 60
READS_PER_MINUTE = 40
"""Chat reads per workspace key per minute. Cevanos allows 60 a key; the other 20 are
left for replies and take-overs."""
PAUSE_SECONDS = 60  # after a 429 without Retry-After
REQUIRED_SCOPES = ("conversations:read", "conversations:write")
CHAT_CUSTOMER = "Chat customer"
AI_NAME = "Cevanos AI"
TEAM_NAME = "Cevanos team"  # a person who wrote in Cevanos itself (the /v1 read has no name)
CONVERTED = "converted_by_agent"
SENTIMENT = {
    "positive": "positive", "neutral": "neutral", "negative": "negative",
    "frustrated": "negative",
}  # fmt: skip
"""Cevanos's moods onto CeDesk's three."""
ENDED_BY = {
    "customer": "the customer marked it solved",
    "quiet": "it went quiet",
    "person": "closed by a person",
}  # fmt: skip
_CONVERSATION = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

_SIGNATURE = re.compile(r"^t=(\d{1,12}),v1=([0-9a-f]{64})$")
_ORDER_ID = re.compile(r"#(\d{4,})")


class BadSignature(ValueError):
    pass


class BadSetting(ValueError):
    pass


class BadEvent(ValueError):
    pass


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """What Cevanos sends (used by the tests and the fake Cevanos)."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def verify(secret: str, body: bytes, header: str | None, *, now: int) -> None:
    if not header:
        raise BadSignature("Missing signature.")
    found = _SIGNATURE.match(header.strip())
    if not found:
        raise BadSignature("Malformed signature.")
    timestamp = int(found.group(1))
    if abs(now - timestamp) > MAX_SKEW_SECONDS:
        raise BadSignature("Signature too old.")
    if not hmac.compare_digest(sign(secret, body, timestamp), header.strip()):
        raise BadSignature("Wrong signature.")


@dataclass(frozen=True)
class WebhookEvent:
    event: str
    event_id: str
    data: Mapping[str, Any]

    @property
    def conversation_id(self) -> str:
        value = self.data.get("conversation_id")
        if not isinstance(value, str) or not _CONVERSATION.match(value):
            raise BadEvent("conversation_id is missing.")
        return value


def parse_event(body: bytes) -> WebhookEvent:
    try:
        raw = json.loads(body)
    except ValueError as exc:
        raise BadEvent("Not JSON.") from exc
    if not isinstance(raw, dict):
        raise BadEvent("Not an event.")
    event, event_id, data = raw.get("event"), raw.get("event_id"), raw.get("data")
    if not isinstance(event, str) or not isinstance(event_id, str) or not isinstance(data, dict):
        raise BadEvent("Not an event.")
    return WebhookEvent(event=event, event_id=event_id, data=data)


def check_conversation_id(value: str) -> str:
    value = value.strip()
    if not _CONVERSATION.match(value):
        raise BadSetting("A Cevanos conversation id: letters, digits, - and _, up to 100.")
    return value


def split_reply(body: str) -> list[str]:
    """Cevanos takes up to 8000 characters a message; a longer reply goes in parts."""
    body = body.strip()
    return [body[i : i + REPLY_MAX] for i in range(0, len(body), REPLY_MAX)] or [""]


def is_echo(event: WebhookEvent) -> bool:
    return event.data.get("reason") == TAKEOVER_REASON


# Settings


def check_api_key(key: str) -> str:
    key = key.strip()
    if not API_KEY.match(key):
        raise BadSetting("Cevanos API key: the cvn_sk_... key from Cevanos > Settings > API keys.")
    return key


def check_secret(secret: str) -> str:
    secret = secret.strip()
    if not SECRET_MIN <= len(secret) <= SECRET_MAX or any(c.isspace() for c in secret):
        raise BadSetting("Webhook secret: the signing secret Cevanos showed for the webhook.")
    return secret


def hint(key: str) -> str:
    return key[-4:]


# The chat


@dataclass(frozen=True)
class ChatMessage:
    id: str
    role: str  # customer, agent (the AI), operator (a person in Cevanos), system
    text: str
    created_at: datetime | None


ROLE_KIND = {"customer": "customer", "agent": "ai", "operator": "agent"}
"""How a chat message is kept on the ticket. Cevanos system lines are left out."""


def parse_messages(raw: object) -> list[ChatMessage]:
    """Cevanos's `{"data": [{id, role, text, created_at}]}`, oldest first."""
    rows = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise BadEvent("Unexpected answer from Cevanos.")
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid, role, text = row.get("id"), row.get("role"), row.get("text")
        if not (isinstance(mid, str) and isinstance(role, str) and isinstance(text, str)):
            continue
        at = row.get("created_at")
        created = None
        if isinstance(at, str):
            try:
                created = datetime.fromisoformat(at)
            except ValueError:
                created = None
        found.append(ChatMessage(id=mid[:100], role=role, text=text, created_at=created))
    return found


def kept(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    return [m for m in messages if m.role in ROLE_KIND and m.text.strip()]


def after(messages: Sequence[ChatMessage], last_id: str | None) -> list[ChatMessage]:
    """The messages after `last_id` (all of them when it is unknown or gone)."""
    if last_id is None:
        return list(messages)
    for i, m in enumerate(messages):
        if m.id == last_id:
            return list(messages[i + 1 :])
    return list(messages)


def subject(messages: Sequence[ChatMessage], channel_kind: str | None) -> str:
    """The first thing the customer wrote, cut to one line."""
    for m in messages:
        if m.role == "customer" and m.text.strip():
            line = " ".join(m.text.split())
            return line if len(line) <= SUBJECT_MAX else line[: SUBJECT_MAX - 1] + "…"
    return f"Chat handed over from Cevanos ({channel_kind or 'chat'})"


def order_id(messages: Sequence[ChatMessage]) -> str | None:
    """md 7.1 point 5: the first `#1234` the customer wrote."""
    for m in messages:
        if m.role == "customer":
            found = _ORDER_ID.search(m.text)
            if found:
                return found.group(1)
    return None


def contact_email(conversation_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9-]", "", conversation_id)[:60] or "unknown"
    return f"chat-{safe}@{CONTACT_DOMAIN}"


def handover(event: WebhookEvent) -> dict[str, Any]:
    """What the ticket keeps from the escalation (shown on the handover card)."""
    keep = ("reason", "channel_kind", "agent_id", "seconds_since_start")
    return {k: event.data.get(k) for k in keep if event.data.get(k) is not None}


def poll_wait(failures: int) -> int:
    return POLL_BACKOFF_SECONDS[min(failures, len(POLL_BACKOFF_SECONDS) - 1)]
