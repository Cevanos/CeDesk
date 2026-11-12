"""Sends one webhook delivery (md PL3, section 9.6): signs the body, checks the host is
public right before connecting (the egress guard), never follows a redirect, waits at
most 10 seconds, and keeps only the first 300 characters of the answer.

The worker and the test ping use it; tests give it a fake transport and guard.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from cedesk.egress import BadAddress, Guard, check_public
from cedesk.helpdesk.domain import webhooks as rules

USER_AGENT = "CeDesk-Webhooks/1"


@dataclass(frozen=True)
class Answer:
    status: int | None  # None: no answer (refused address, timeout, connection error)
    ms: int
    error: str | None  # None when delivered
    response: str | None  # the start of the receiver's answer


def body_bytes(body: dict[str, Any]) -> bytes:
    """The exact bytes sent and signed."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


class WebhookClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        guard: Guard = check_public,
        timeout: float = rules.TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._guard = guard
        self._timeout = timeout

    async def check_host(self, url: str) -> None:
        """Raises `BadAddress` unless the URL's host resolves to public addresses only."""
        await self._guard(rules.host_of(url))

    async def send(
        self, url: str, secret: str, *, delivery_id: str, event: str, body: dict[str, Any]
    ) -> Answer:
        payload = body_bytes(body)
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-CeDesk-Event": event,
            "X-CeDesk-Delivery": delivery_id,
            "X-CeDesk-Timestamp": str(timestamp),
            "X-CeDesk-Signature": rules.signature(secret, timestamp, payload),
        }
        started = time.monotonic()

        def took() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            await self.check_host(url)
        except BadAddress as exc:
            return Answer(None, took(), str(exc)[: rules.RESPONSE_KEPT], None)
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport, timeout=self._timeout, follow_redirects=False
                ) as client,
                client.stream("POST", url, content=payload, headers=headers) as answer,
            ):
                kept = b""
                async for chunk in answer.aiter_bytes():
                    kept += chunk
                    if len(kept) >= rules.RESPONSE_KEPT:
                        break
                status = answer.status_code
        except httpx.TimeoutException:
            return Answer(None, took(), f"No answer in {self._timeout:g} s.", None)
        except httpx.HTTPError as exc:
            return Answer(None, took(), f"Couldn't connect ({type(exc).__name__}).", None)
        text = kept[: rules.RESPONSE_KEPT].decode("utf-8", errors="replace")
        if rules.delivered(status):
            return Answer(status, took(), None, text)
        reason = "Redirects aren't followed." if 300 <= status < 400 else f"HTTP {status}."
        return Answer(status, took(), reason, text)
