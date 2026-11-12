"""One way to call a help desk's API, for every importer.

A connector never opens a connection itself: it asks `SourceHttp` for a path under its
host. That is where the rules that apply to every source live, once:

- the host was built by `cedesk.egress` and is checked (public addresses only) before each
  call; https only; a redirect is never followed;
- **read only**: there is a `get`, and one narrow `exchange_token` for OAuth (it POSTs an app
  id and secret to the source's token path and nothing else), so no importer can write to
  the source;
- a 429 becomes `RateLimited` (the run pauses for Retry-After); 401/403 and a missing
  account are permanent `SourceError`; a timeout, a 5xx or an answer that isn't JSON is a
  temporary one (the run tries again);
- the credentials ride in headers set once and are never logged or put in an error message.

Tests pass `transport` (an `httpx.MockTransport`) and `guard` (a check that does not resolve
names); nothing else differs from production.
"""

import base64
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from cedesk.egress import BadAddress, Guard, check_public
from cedesk.helpdesk.domain.imports import RateLimited, SourceError

TIMEOUT_SECONDS = 30
USER_AGENT = "CeDesk/1 (+import)"
MAX_RETRY_AFTER = 3600


def basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def segment(value: object) -> str:
    """One path segment from an id the source gave: nothing in it can add a `/` or `..`."""
    return quote(str(value), safe="")


def retry_after(headers: Mapping[str, str], now: datetime | None = None) -> int | None:
    """Seconds to wait from `Retry-After` or `X-RateLimit-Retry-After` (seconds), or from an
    `X-RateLimit-Reset` that is a Unix time (Intercom). None when the answer says nothing usable."""
    for name in ("retry-after", "x-ratelimit-retry-after"):  # the second is Help Scout's
        value = headers.get(name, "").strip()
        if value.isdigit():
            return min(int(value), MAX_RETRY_AFTER)
    reset = headers.get("x-ratelimit-reset", "").strip()
    if reset.isdigit() and int(reset) > 10**9:
        wait = int(reset) - int((now or datetime.now(UTC)).timestamp())
        return max(1, min(wait, MAX_RETRY_AFTER))
    return None


def when(value: object) -> datetime | None:
    """A time as the sources write it: ISO 8601 (`2025-03-01T10:15:00Z`, with or without a
    zone) or Unix seconds. UTC when no zone is given. None for anything else."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return when(int(text))
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class SourceHttp:
    def __init__(
        self,
        host: str,
        *,
        headers: Mapping[str, str],
        guard: Guard | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        base_path: str = "",
        label: str = "The source",
    ) -> None:
        self.host = host
        self._base = f"https://{host}{base_path}"
        self._headers = {
            **headers,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        self._guard = guard or check_public
        self._transport = transport
        self._label = label

    async def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        missing_ok: bool = False,
    ) -> Any:
        """The JSON of `GET path`. With `missing_ok`, a 404 is None (an archived ticket the
        source no longer serves, an article deleted since it was listed)."""
        return await self._send("GET", path, params=params, form=None, missing_ok=missing_ok)

    async def exchange_token(self, path: str, form: Mapping[str, str]) -> Any:
        """OAuth's token exchange (`POST` of a form to the source's token path), for a source
        that hands out a short-lived access token for an app id and secret. It is the only
        request here that isn't a `GET`, it reads nothing of the customer's, and it is refused
        for any path that doesn't end in `/oauth2/token`."""
        if not path.rstrip("/").endswith("/oauth2/token"):
            raise ValueError("exchange_token is for an OAuth token path only")
        return await self._send("POST", path, params=None, form=form, missing_ok=False)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        form: Mapping[str, str] | None,
        missing_ok: bool,
    ) -> Any:
        try:
            await self._guard(self.host)
        except BadAddress as exc:
            raise SourceError(str(exc), permanent=True) from None
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=self._transport
            ) as client:
                response = await client.request(
                    method,
                    self._base + path,
                    params=_clean(params),
                    data=None if form is None else dict(form),
                    headers=self._headers,
                )
        except httpx.HTTPError as exc:
            raise SourceError(
                f"{self._label} can't be reached: {type(exc).__name__}", permanent=False
            ) from None
        status = response.status_code
        if status == 429:
            raise RateLimited(retry_after(response.headers))
        if status in (401, 403):
            raise SourceError(
                f"{self._label} refused these credentials (check the key and what it may read).",
                permanent=True,
            )
        if status == 404 and missing_ok:
            return None
        if status == 404:
            raise SourceError(f"{self._label} has no such account or record.", permanent=True)
        if 300 <= status < 400:
            raise SourceError(f"{self._label} sent us elsewhere; not followed.", permanent=True)
        if status in (408, 425) or status >= 500:
            raise SourceError(f"{self._label} answered {status}; will retry.", permanent=False)
        if status >= 400:
            raise SourceError(f"{self._label} refused the request ({status}).", permanent=True)
        try:
            return response.json()
        except ValueError:
            raise SourceError(
                f"{self._label} sent an answer that isn't JSON.", permanent=False
            ) from None


def _clean(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}
