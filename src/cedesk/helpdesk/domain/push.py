"""Push notifications to the mobile app (md P8), the rules that need no database.

- A phone registers its push token after sign-in; it gets banners for the person signed
  in on it. Each person keeps their 10 newest devices. The app removes its device on
  sign-out; a deactivated member gets nothing.
- Banners: a new ticket for you (assigned), you were mentioned, the customer replied.
  Every other notification stays in the bell only.
- A banner names the ticket's number, never its subject or any message: the push
  service (Google, and Apple behind it) sees only that. The app opens the ticket with the
  normal API, which checks the person's rights as always.
- A banner older than 10 minutes is dropped (it is still in the bell): turning push on,
  or a worker that was down, never floods phones with old news.
"""

from datetime import timedelta
from enum import StrEnum

MAX_DEVICES = 10
MAX_AGE = timedelta(minutes=10)
BATCH = 200
TOKEN_MIN = 20
TOKEN_MAX = 4096


class Platform(StrEnum):
    ios = "ios"
    android = "android"


BANNERS: dict[str, str] = {
    "assigned": "New ticket for you",
    "mention": "You were mentioned",
    "customer_reply": "The customer replied",
}


class BadToken(ValueError):
    """The message is for the app's developer."""


def check_token(raw: str) -> str:
    token = raw.strip()
    if not TOKEN_MIN <= len(token) <= TOKEN_MAX or any(c.isspace() for c in token):
        raise BadToken("Send the Firebase Cloud Messaging registration token.")
    return token


def banner(kind: str, label: str) -> tuple[str, str] | None:
    """(title, body) for a notification that gets a banner, else None."""
    title = BANNERS.get(kind)
    return (title, label) if title else None


def hint(token: str) -> str:
    """What the device list shows instead of the token."""
    return f"…{token[-6:]}"
