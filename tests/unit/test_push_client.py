"""The Firebase Cloud Messaging client (md P8) against a fake Google: the signed JWT
(RS256, checked with the public key), the access token kept and renewed, the message
sent, which answers mean "the device is gone", a refused key, only Google's two fixed
hosts (never the key file's token_uri), the egress guard, and no token or key in errors."""

import asyncio
import base64
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cedesk.egress import BadAddress
from cedesk.push_client import FcmClient, PushConfigError, PushError

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
ACCOUNT = {"type": "service_account", "client_email": "push@cedesk-mobile.iam.gserviceaccount.com",
           "private_key": PEM, "token_uri": "https://evil.test/token"}  # fmt: skip
DEVICE = "fcm-device-token-0123456789-abcdef"


def unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class FakeGoogle:
    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.answer: tuple[int, Any] = (200, {"name": "projects/x/messages/1"})
        self.token_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": f"ya29.{len(self.seen)}",
                                             "expires_in": 3600})  # fmt: skip
        if request.url.host == "fcm.googleapis.com":
            return httpx.Response(self.answer[0], json=self.answer[1])
        return httpx.Response(404)


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def client(fake: FakeGoogle, clock: Clock | None = None, **kw: Any) -> FcmClient:
    async def public(host: str) -> None:
        return None

    return FcmClient("cedesk-mobile", json.dumps(ACCOUNT), guard=kw.get("guard", public),
                     transport=httpx.MockTransport(fake), clock=clock or Clock())  # fmt: skip


async def test_signs_in_and_sends() -> None:
    fake, clock = FakeGoogle(), Clock()
    fcm = client(fake, clock)
    sent = await fcm.send(DEVICE, "You were mentioned", "T-1042", {"ticket_id": "t1"})
    assert (sent.ok, sent.gone) == (True, False)

    token_call, send_call = fake.seen
    assert token_call.url.host == "oauth2.googleapis.com"  # never the key file's token_uri
    form = parse_qs(token_call.content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    header, claims, signature = form["assertion"][0].split(".")
    KEY.public_key().verify(unb64(signature), f"{header}.{claims}".encode(),
                            padding.PKCS1v15(), hashes.SHA256())  # fmt: skip
    body = json.loads(unb64(claims))
    assert body["iss"] == ACCOUNT["client_email"]
    assert body["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
    assert body["aud"] == "https://oauth2.googleapis.com/token"
    assert body["exp"] - body["iat"] == 3600 and json.loads(unb64(header))["alg"] == "RS256"

    assert send_call.url == "https://fcm.googleapis.com/v1/projects/cedesk-mobile/messages:send"
    assert send_call.headers["Authorization"] == "Bearer ya29.1"
    message = json.loads(send_call.content)["message"]
    assert message["token"] == DEVICE
    assert message["notification"] == {"title": "You were mentioned", "body": "T-1042"}
    assert message["data"] == {"ticket_id": "t1"}

    # The access token is kept, then renewed a minute before it ends.
    await fcm.send(DEVICE, "t", "b", {})
    assert [r.url.host for r in fake.seen].count("oauth2.googleapis.com") == 1
    clock.now += 3600 - 59
    await fcm.send(DEVICE, "t", "b", {})
    assert [r.url.host for r in fake.seen].count("oauth2.googleapis.com") == 2


@pytest.mark.parametrize(
    ("status", "answer", "gone", "retry"),
    [
        (404, {"error": {"status": "NOT_FOUND"}}, True, False),
        (400, {"error": {"details": [{"errorCode": "UNREGISTERED"}]}}, True, False),
        # A bad message or token format: refused, but the app may still be there.
        (400, {"error": {"details": [{"errorCode": "INVALID_ARGUMENT"}]}}, False, False),
        (400, {"error": {"status": "INVALID_ARGUMENT"}}, False, False),
        (400, {"error": {"details": [{"errorCode": "SENDER_ID_MISMATCH"}]}}, False, False),
        (429, {"error": {}}, False, True),
        (503, {"error": {}}, False, True),
    ],
)
async def test_answers(status: int, answer: Any, gone: bool, retry: bool) -> None:
    fake = FakeGoogle()
    fake.answer = (status, answer)
    sent = await client(fake).send(DEVICE, "t", "b", {})
    assert (sent.ok, sent.gone, sent.retry) == (False, gone, retry)


async def test_one_pool_and_one_token_for_concurrent_sends() -> None:
    fake = FakeGoogle()
    fcm = client(fake)
    async with fcm.opened():
        results = await asyncio.gather(*(fcm.send(DEVICE, "t", "b", {}) for _ in range(5)))
    assert all(r.ok for r in results)
    assert [r.url.host for r in fake.seen].count("oauth2.googleapis.com") == 1
    assert fcm._client is None  # closed with the pass


async def test_a_refused_key_stops_without_leaking() -> None:
    fake = FakeGoogle()
    fake.token_status = 400
    with pytest.raises(PushError) as refused:
        await client(fake).send(DEVICE, "t", "b", {})
    assert refused.value.config and DEVICE not in str(refused.value)
    assert "PRIVATE" not in str(refused.value)

    fake = FakeGoogle()
    fake.answer = (403, {"error": {}})
    with pytest.raises(PushError) as forbidden:
        await client(fake).send(DEVICE, "t", "b", {})
    assert forbidden.value.config and DEVICE not in str(forbidden.value)


async def test_the_guard_runs_before_each_call() -> None:
    async def private(host: str) -> None:
        raise BadAddress(f"'{host}' isn't a public address.")

    fake = FakeGoogle()
    with pytest.raises(PushError) as refused:
        await client(fake, guard=private).send(DEVICE, "t", "b", {})
    assert not refused.value.config and fake.seen == []


@pytest.mark.parametrize(
    ("project", "account"),
    [
        ("Bad Project!", json.dumps(ACCOUNT)),
        ("cedesk-mobile", "not json"),
        ("cedesk-mobile", json.dumps({**ACCOUNT, "type": "user"})),
        ("cedesk-mobile", json.dumps({**ACCOUNT, "private_key": "nope"})),
        ("cedesk-mobile", json.dumps({"type": "service_account"})),
    ],
)
def test_a_bad_setting_is_refused_at_start(project: str, account: str) -> None:
    with pytest.raises(PushConfigError) as bad:
        FcmClient(project, account)
    assert "PRIVATE" not in str(bad.value) and PEM[40:80] not in str(bad.value)
