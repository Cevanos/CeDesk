"""CAPTCHA check for self-service sign-up (Cloudflare Turnstile).

The sign-up page shows the Turnstile widget with the site key; the browser gets a one-time
token and sends it with the sign-up. The API asks Cloudflare whether the token is good,
with the secret, before it does any slow work (hashing the password, sending an email).
Unset secret: no CAPTCHA (settings refuse that on the cloud edition outside dev).
"""

import logging
from typing import Protocol

import httpx

logger = logging.getLogger("cedesk.captcha")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0


class Captcha(Protocol):
    async def passed(self, token: str, ip: str | None) -> bool: ...


class Turnstile:
    def __init__(self, secret: str, client: httpx.AsyncClient | None = None) -> None:
        self._secret = secret
        self._client = client

    async def passed(self, token: str, ip: str | None) -> bool:
        if not token or len(token) > 2048:
            return False
        data = {"secret": self._secret, "response": token}
        if ip:
            data["remoteip"] = ip
        try:
            if self._client is not None:
                answer = await self._client.post(VERIFY_URL, data=data, timeout=TIMEOUT_SECONDS)
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    answer = await client.post(VERIFY_URL, data=data, timeout=TIMEOUT_SECONDS)
            return answer.status_code == 200 and answer.json().get("success") is True
        except (httpx.HTTPError, ValueError) as exc:
            # Cloudflare unreachable: refuse (a bot must not get in because the check is
            # down); the person can try again. Only the error type is logged.
            logger.warning("captcha check failed: %s", type(exc).__name__)
            return False
