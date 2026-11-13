"""Sending push banners through Firebase Cloud Messaging (md P8): the HTTP v1 API, which
delivers to Android, and to iPhones through the app's Apple key uploaded to Firebase.

The service account signs a short JWT (RS256, with `cryptography`); Google's token
endpoint swaps it for an access token (kept until a minute before it ends). Only two hosts
are ever called, both fixed here (never the `token_uri` in the key file), through the
egress guard and a pinned connection; https, no redirects, at most 10 s. A device token
and the key are never logged or put in an error.
"""

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cedesk.egress import BadAddress, Guard, check_public, pinned

OAUTH_HOST = "oauth2.googleapis.com"
FCM_HOST = "fcm.googleapis.com"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TIMEOUT_SECONDS = 10.0
USER_AGENT = "CeDesk/1 (+push)"
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,29}$")


class PushConfigError(ValueError):
    """The project id or the service account key can't be used."""


class PushError(Exception):
    """`config`: our key or project is refused (stop and tell the operator);
    otherwise the push service didn't answer (that banner is skipped)."""

    def __init__(self, message: str, *, config: bool) -> None:
        super().__init__(message)
        self.config = config


@dataclass(frozen=True)
class Sent:
    ok: bool
    gone: bool  # the token is no longer valid: delete the device


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FcmClient:
    def __init__(
        self,
        project_id: str,
        service_account_json: str,
        *,
        guard: Guard | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = time.time,
    ) -> None:
        if not _PROJECT.match(project_id):
            raise PushConfigError("CEDESK_PUSH_FCM_PROJECT_ID isn't a Firebase project id.")
        try:
            account = json.loads(service_account_json)
            self._email = str(account["client_email"])
            key = serialization.load_pem_private_key(
                str(account["private_key"]).encode(), password=None
            )
        except (ValueError, KeyError, TypeError):
            raise PushConfigError(
                "CEDESK_PUSH_FCM_SERVICE_ACCOUNT isn't a Firebase service account key (JSON)."
            ) from None
        if not isinstance(key, rsa.RSAPrivateKey) or account.get("type") != "service_account":
            raise PushConfigError("CEDESK_PUSH_FCM_SERVICE_ACCOUNT isn't a service account key.")
        self._key = key
        self._project = project_id
        self._guard = guard or check_public
        self._transport = transport
        self._clock = clock
        self._access: tuple[str, float] | None = None  # token, when to renew it

    def _assertion(self) -> str:
        now = int(self._clock())
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64(json.dumps({"iss": self._email, "scope": SCOPE,
                                  "aud": f"https://{OAUTH_HOST}/token", "iat": now,
                                  "exp": now + 3600}).encode())  # fmt: skip
        signed = f"{header}.{claims}".encode()
        signature = self._key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_b64(signature)}"

    async def _request(
        self, host: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        try:
            await self._guard(host)
        except BadAddress as exc:
            raise PushError(str(exc), config=False) from None
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=pinned(self._transport)
            ) as client:
                return await client.post(
                    f"https://{host}{path}",
                    headers={"User-Agent": USER_AGENT, **(headers or {})},
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise PushError(f"{host} can't be reached ({type(exc).__name__}).",
                            config=False) from None  # fmt: skip

    async def _access_token(self) -> str:
        if self._access is not None and self._clock() < self._access[1]:
            return self._access[0]
        response = await self._request(OAUTH_HOST, "/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": self._assertion()})  # fmt: skip
        if response.status_code in (400, 401, 403):
            raise PushError("Google refused the push service account key.", config=True)
        if response.status_code != 200:
            raise PushError(f"Google's token service answered {response.status_code}.",
                            config=False)  # fmt: skip
        try:
            data = response.json()
            token, lasts = str(data["access_token"]), int(data.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError):
            raise PushError("Google's token service answered something else.",
                            config=False) from None  # fmt: skip
        self._access = (token, self._clock() + max(lasts - 60, 30))
        return token

    async def send(self, device_token: str, title: str, body: str, data: dict[str, str]) -> Sent:
        access = await self._access_token()
        message = {
            "message": {
                "token": device_token,
                "notification": {"title": title, "body": body},
                "data": data,
                "android": {"priority": "high"},
                "apns": {"payload": {"aps": {"sound": "default"}}},
            }
        }
        response = await self._request(
            FCM_HOST, f"/v1/projects/{self._project}/messages:send", json=message,
            headers={"Authorization": f"Bearer {access}"},
        )  # fmt: skip
        status = response.status_code
        if status == 200:
            return Sent(ok=True, gone=False)
        if status == 404 or (status == 400 and _fcm_error(response) in _GONE):
            return Sent(ok=False, gone=True)
        if status in (401, 403):
            self._access = None
            raise PushError("Firebase refused the push service account key.", config=True)
        return Sent(ok=False, gone=False)  # 429, 5xx...: this banner is skipped


_GONE = frozenset({"UNREGISTERED", "INVALID_ARGUMENT"})


def _fcm_error(response: httpx.Response) -> str | None:
    """FCM's error code (UNREGISTERED, INVALID_ARGUMENT...) from a refusal."""
    try:
        details = response.json()["error"].get("details") or []
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    for d in details:
        if isinstance(d, dict) and d.get("errorCode"):
            return str(d["errorCode"])
    return None
