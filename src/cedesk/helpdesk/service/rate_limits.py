"""Request limits every API process shares, kept in the database (`rate_limit_hit`).

`cedesk.ratelimit` counts in one process's memory, which is fine for sign-in on one
server; the AI-app endpoints (/oauth/*, /mcp) are reached from a few large providers and
may run behind several API processes, so their counts must be the same everywhere. A fixed
window: at most `limit` requests from one key per window. Keys are hashed before they are
stored, so no address or token is kept.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from cedesk.helpdesk.repo import rate_limits as repo
from cedesk.helpdesk.service.auth import Sessionmaker

KEPT = timedelta(days=1)


async def hit(
    sm: Sessionmaker, bucket: str, key: str, *, limit: int, window_seconds: int
) -> int | None:
    """Counts one request. None if allowed, else the seconds to wait."""
    key_hash = hashlib.sha256(f"{bucket}\x00{key}".encode()).digest()
    async with sm() as s, s.begin():
        hits, start, now = await repo.hit(s, bucket, key_hash, window_seconds)
    if hits <= limit:
        return None
    ends = start + timedelta(seconds=window_seconds)
    return max(1, int((ends - now).total_seconds()) + 1)


async def sweep(sm: Sessionmaker) -> int:
    """Removes counts of windows that ended over a day ago (the worker, each pass)."""
    async with sm() as s, s.begin():
        return await repo.sweep(s, datetime.now(UTC) - KEPT)
