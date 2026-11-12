"""Outgoing webhooks (md PL3-PL6, section 9.6), the rules that need no database.

- An Admin adds a URL and picks events. The URL must be https with a public host (no IP
  address typed in, no user or password in it); the worker checks again, before every
  send, that the host resolves only to public addresses (the egress guard), and never
  follows a redirect.
- Each webhook has its own secret `whsec_...`, shown once. A delivery is signed:
  `X-CeDesk-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>` with the
  timestamp in `X-CeDesk-Timestamp` (unix seconds). Signing the timestamp too means an old
  delivery can't be replayed as new: receivers check it is recent (5 minutes).
- The body is the outbox event as stored: `{event, ticket: {id, number, subject, status,
  priority, team, topic}, id, occurred_at, ...}` (PL5); `message.created` names the
  message (id, kind), never its text. `X-CeDesk-Delivery` is the same on every retry, so a
  receiver can drop a repeat.
- A 2xx answer is delivered. Anything else, or no answer in 10 s, is tried again after 1
  min, 5 min, 30 min, 2 h and 12 h; after the 6th try the delivery has failed, is listed
  for Admins, and can be sent again by hand.
"""

import hashlib
import hmac
import ipaddress
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

SECRET_PREFIX = "whsec_"  # noqa: S105 - a prefix, not a secret
EVENTS: tuple[str, ...] = ("ticket.created", "ticket.updated", "ticket.resolved", "message.created")
"""What a webhook can listen to. `message.created` is only for messages a customer would
see (customer and agent replies), never notes or system lines."""
MESSAGE_KINDS = frozenset({"customer", "agent"})
PING = "ping"  # the test delivery
MAX_WEBHOOKS = 20  # per workspace
URL_MAX = 500
TIMEOUT_SECONDS = 10.0
RETRY_AFTER: tuple[timedelta, ...] = (
    timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
    timedelta(hours=12),
)  # fmt: skip
MAX_TRIES = len(RETRY_AFTER) + 1
RESPONSE_KEPT = 300  # characters of the receiver's answer kept in the log
SIGNATURE_TOLERANCE_SECONDS = 300  # what receivers are told to accept


class BadUrl(ValueError):
    """The message is for the Admin."""


def check_url(raw: str) -> str:
    """The URL as saved, or `BadUrl`. The public-address check needs DNS, so it is the
    egress guard's, at save and before each send."""
    url = raw.strip()
    if not url or len(url) > URL_MAX:
        raise BadUrl(f"Give a URL of at most {URL_MAX} characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BadUrl("That isn't a URL.") from exc
    if parts.scheme != "https":
        raise BadUrl("The URL must start with https://.")
    if parts.username or parts.password:
        raise BadUrl("Put no user or password in the URL; check the signature instead.")
    host = (parts.hostname or "").lower()
    if not host or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise BadUrl("The URL needs a public host name.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BadUrl("Use a host name, not an IP address.")
    if port == 0:
        raise BadUrl("That port can't be used.")
    if parts.fragment:
        raise BadUrl("Leave out the # part.")
    return url


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def check_events(events: list[str]) -> list[str]:
    """Known events, each once, in the order of `EVENTS`."""
    unknown = sorted(set(events) - set(EVENTS))
    if unknown:
        raise BadUrl(f"Unknown event: {', '.join(unknown)}. Pick from {', '.join(EVENTS)}.")
    if not events:
        raise BadUrl("Pick at least one event.")
    return [e for e in EVENTS if e in events]


def wants(events: list[str], event: str, payload: dict[str, object]) -> bool:
    """Whether a webhook listening to `events` gets this outbox event."""
    if event not in events:
        return False
    if event == "message.created":
        message = payload.get("message")
        kind = message.get("kind") if isinstance(message, dict) else None
        return kind in MESSAGE_KINDS
    return True


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def hint(secret: str) -> str:
    return f"{SECRET_PREFIX}…{secret[-4:]}"


def signature(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def next_try(tries_done: int) -> timedelta | None:
    """After `tries_done` failed tries, how long until the next one; None: failed for good."""
    if tries_done < 1 or tries_done > len(RETRY_AFTER):
        return None
    return RETRY_AFTER[tries_done - 1]


def delivered(status: int | None) -> bool:
    return status is not None and 200 <= status < 300
