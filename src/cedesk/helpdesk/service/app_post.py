"""Sending to an app (md PL7): Jira's "create issue" and a Slack alert. The read-only
cards use `imports.http.SourceHttp`; this is the one place an app is written to, and only
by a person's action (create an issue) or an alert they turned on, never by itself.

The same rules as every outbound call: the host was built or fixed by the code and is
checked before each call (public addresses only, the egress guard), https only, a redirect
is never followed, at most 10 s, credentials only in headers and never in an error.
"""

from collections.abc import Mapping
from typing import Any

import httpx

from cedesk.egress import BadAddress, Guard, check_public, pinned

TIMEOUT_SECONDS = 10.0
USER_AGENT = "CeDesk/1 (+apps)"


class AppCallError(Exception):
    """`permanent`: trying again won't help (refused, bad request); otherwise it might.
    `status`: the provider's HTTP answer, when there was one, so a caller can say what was
    missing (Jira: which issue or project) instead of the general message."""

    def __init__(self, message: str, *, permanent: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.status = status


class AppPost:
    def __init__(
        self,
        host: str,
        *,
        headers: Mapping[str, str],
        label: str,
        guard: Guard | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.host = host
        self._headers = {**headers, "User-Agent": USER_AGENT, "Accept": "application/json"}
        self._label = label
        self._guard = guard or check_public
        self._transport = transport

    async def get(self, path: str) -> Any:
        return await self._send("GET", path, None)

    async def post(self, path: str, body: Mapping[str, Any]) -> Any:
        return await self._send("POST", path, body)

    async def _send(self, method: str, path: str, body: Mapping[str, Any] | None) -> Any:
        try:
            await self._guard(self.host)
        except BadAddress as exc:
            raise AppCallError(str(exc), permanent=True) from None
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=pinned(self._transport)
            ) as client:
                response = await client.request(
                    method, f"https://{self.host}{path}", json=body, headers=self._headers
                )
        except httpx.HTTPError as exc:
            raise AppCallError(
                f"{self._label} can't be reached ({type(exc).__name__}).", permanent=False
            ) from None
        status = response.status_code
        if status in (401, 403):
            raise AppCallError(
                f"{self._label} refused the credentials.", permanent=True, status=status
            )
        if status == 404:
            raise AppCallError(
                f"{self._label} couldn't find what was asked for (404).", permanent=True, status=404
            )
        if 300 <= status < 400:
            raise AppCallError(
                f"{self._label} sent us elsewhere; not followed.", permanent=True, status=status
            )
        if status == 429 or status >= 500:
            raise AppCallError(
                f"{self._label} answered {status}; try again.", permanent=False, status=status
            )
        if status >= 400:
            raise AppCallError(
                f"{self._label} refused the request ({status}).", permanent=True, status=status
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text[:200]  # Slack answers plain "ok"
