"""Fetching an AI app's Client ID Metadata Document (its `client_id` is an https URL).

The URL comes from whoever starts a sign-in, so it is fetched like any address a
workspace typed: through the egress guard's `PinnedTransport` (public addresses only,
checked at connect time), https only, no redirects, a short timeout and a small body cap.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from cedesk.egress import pinned

MAX_BYTES = 64 * 1024
TIMEOUT_SECONDS = 5.0


class FetchFailed(Exception):
    """The document could not be fetched. The message is safe to show the app."""


@dataclass(frozen=True)
class Fetched:
    body: bytes
    cache_control: str | None


DocumentFetch = Callable[[str], Awaitable[Fetched]]


def make_fetch(transport: httpx.AsyncBaseTransport | None = None) -> DocumentFetch:
    async def fetch(url: str) -> Fetched:
        if not url.startswith("https://"):
            raise FetchFailed("client_id must be an https URL")
        try:
            async with (
                httpx.AsyncClient(
                    transport=pinned(transport), timeout=TIMEOUT_SECONDS, follow_redirects=False
                ) as client,
                client.stream("GET", url, headers={"Accept": "application/json"}) as response,
            ):
                if response.status_code != 200:
                    raise FetchFailed(
                        f"the client metadata document answered {response.status_code}"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchFailed("the client metadata document is too large")
                return Fetched(bytes(body), response.headers.get("cache-control"))
        except httpx.HTTPError:
            raise FetchFailed("the client metadata document could not be fetched") from None

    return fetch
