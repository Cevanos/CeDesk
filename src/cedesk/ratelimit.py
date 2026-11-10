"""A small in-memory limit on attempts per key (here: per client IP) in a sliding window.

Used on sign-in and password change, on top of the per-account lockout: that lockout stops
guessing one account, this stops one address trying many accounts, locking people out on
purpose, or flooding the slow password check. It lives in the API process, so with several
API processes each counts on its own (fine for one server; use a shared store when scaled).
"""

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._calls = 0

    def hit(self, key: str) -> int | None:
        """Counts one attempt. None if allowed, else the seconds to wait (the attempt is
        not counted, so waiting is always enough)."""
        now = self._clock()
        self._calls += 1
        if self._calls % 1000 == 0:
            self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(1, int(hits[0] + self.window - now) + 1)
        hits.append(now)
        return None

    def _sweep(self, now: float) -> None:
        """Drops keys with no attempt inside the window, so memory stays bounded."""
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[key]
