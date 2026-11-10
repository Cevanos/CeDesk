"""Delivering outbox events (md 3.2 step 7, 9 "Webhooks"): when to try again, and the key
that makes one event give one effect per handler. No database."""

import re
from uuid import UUID

RETRY_AFTER_SECONDS = (60, 300, 1800, 7200, 43200)
"""After the 1st to 5th failed try: 1 min, 5 min, 30 min, 2 h, 12 h."""
MAX_ATTEMPTS = len(RETRY_AFTER_SECONDS) + 1
ERROR_MAX = 500
HANDLER_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,59}$")


def retry_after(attempts: int) -> int | None:
    """Seconds to wait after `attempts` tries; None once they are used up."""
    if attempts < 1:
        raise ValueError("attempts starts at 1")
    if attempts >= MAX_ATTEMPTS:
        return None
    return RETRY_AFTER_SECONDS[attempts - 1]


def effect_key(event_id: UUID, handler: str) -> str:
    """Stable per event and handler: a handler sends it to the outside service (as its
    idempotency key), so a retry after a lost answer is not a second effect."""
    return f"{event_id}:{handler}"
