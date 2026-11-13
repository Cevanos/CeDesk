"""Push notifications to the mobile app (md P8), the rules that need no database.

- A phone registers its push token after sign-in; it gets banners for the person signed
  in on it, while that sign-in lasts: signing out (or out everywhere), a password change
  or reset, or deactivation removes it. Each person keeps their 10 newest devices. A
  phone still registered to someone else's live sign-in can't be taken over (409).
- Banners: a new ticket for you (assigned), you were mentioned, the customer replied, a
  Cevanos chat is waiting for Accept (handover: to the assignee, else the team's
  Available agents). Every other notification stays in the bell only. An "assigned" for
  a chat still waiting for Accept gets no banner: the handover banner says it.
- A banner names the ticket's number, never its subject or any message: the push
  service (Google, and Apple behind it) sees only that. The app opens the ticket with the
  normal API, which checks the person's rights as always.
- A banner older than 10 minutes is dropped (it is still in the bell): turning push on,
  or a worker that was down, never floods phones with old news.
- Sending: one pass sends at most 10 at a time and stops after 20 s; what is left waits
  for the next pass. A failed try (network, the push service busy) is retried a minute
  later, at most 5 tries.
"""

from datetime import timedelta
from enum import StrEnum

MAX_DEVICES = 10
MAX_AGE = timedelta(minutes=10)
BATCH = 200
CONCURRENCY = 10
PASS_BUDGET_SECONDS = 20.0
MAX_ATTEMPTS = 5
RETRY_AFTER = timedelta(minutes=1)
LEASE = timedelta(minutes=2)  # longer than a pass: a dead worker's claims come back
TOKEN_MIN = 20
TOKEN_MAX = 4096


class Platform(StrEnum):
    ios = "ios"
    android = "android"


BANNERS: dict[str, str] = {
    "assigned": "New ticket for you",
    "mention": "You were mentioned",
    "customer_reply": "The customer replied",
    "handover": "A chat is waiting for you",
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


def handover_text(label: str) -> str:
    """The bell's line for a chat waiting for Accept (no subject, no message)."""
    return f"{label}: a Cevanos chat is waiting for Accept"


def hint(token: str) -> str:
    """What the device list shows instead of the token."""
    return f"…{token[-6:]}"
