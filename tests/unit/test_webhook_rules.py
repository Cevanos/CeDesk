"""Webhook rules and the sending client (md PL3-PL6, section 9.6), no database: which URLs
are taken, which events a webhook gets, the signature, the retry schedule, and what the
client sends and keeps."""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest

from cedesk.egress import BadAddress
from cedesk.helpdesk.domain import webhooks as rules
from cedesk.webhook_client import WebhookClient, body_bytes


@pytest.mark.parametrize(
    "url",
    ["https://hooks.example.com/cedesk", "https://example.com:8443/x?a=1",
     "  https://Hooks.Example.com/path  "],
)  # fmt: skip
def test_good_urls(url: str) -> None:
    assert rules.check_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    ["", "http://hooks.example.com", "ftp://example.com", "https://user:pw@example.com",
     "https://10.0.0.1/x", "https://[::1]/x", "https://localhost/x", "https://intranet/x",
     "https://printer.local/x", "https://db.internal/x", "https://example.com/#frag",
     "https://example.com:99999/x", "https://" + "a" * 500 + ".com"],
)  # fmt: skip
def test_bad_urls(url: str) -> None:
    with pytest.raises(rules.BadUrl):
        rules.check_url(url)


def test_events_are_known_once_and_in_order() -> None:
    assert rules.check_events(["ticket.resolved", "ticket.created", "ticket.created"]) == [
        "ticket.created", "ticket.resolved"]  # fmt: skip
    for bad in ([], ["ticket.deleted"]):
        with pytest.raises(rules.BadUrl):
            rules.check_events(bad)


def test_message_events_only_for_what_a_customer_sees() -> None:
    hook = ["message.created", "ticket.created"]
    for kind, sent in (("customer", True), ("agent", True), ("note", False), ("system", False)):
        payload = {"message": {"id": "m", "kind": kind}}
        assert rules.wants(hook, "message.created", payload) is sent, kind
    assert rules.wants(hook, "ticket.created", {}) is True
    assert rules.wants(hook, "ticket.updated", {}) is False  # not listened to
    assert rules.wants(hook, "message.created", {}) is False  # no message: nothing to send


def test_the_signature_covers_the_timestamp_and_the_body() -> None:
    body = b'{"event":"ticket.created"}'
    sig = rules.signature("whsec_abc", 1700000000, body)
    want = hmac.new(b"whsec_abc", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert sig == "sha256=" + want
    assert rules.signature("whsec_abc", 1700000001, body) != sig  # a replay with a new time
    assert rules.signature("whsec_abd", 1700000000, body) != sig


def test_the_retry_schedule() -> None:
    waits = [rules.next_try(n) for n in range(1, 8)]
    assert waits == [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30),
                     timedelta(hours=2), timedelta(hours=12), None, None]  # fmt: skip
    assert rules.MAX_TRIES == 6
    assert rules.delivered(204) and not rules.delivered(302) and not rules.delivered(None)


def test_a_secret_is_long_and_shown_by_its_end() -> None:
    secret = rules.new_secret()
    assert secret.startswith("whsec_") and len(secret) > 40
    assert rules.hint(secret) == "whsec_…" + secret[-4:]


async def _public(host: str) -> None:
    return None


def _client(handler: object, guard: object = _public) -> WebhookClient:
    return WebhookClient(transport=httpx.MockTransport(handler), guard=guard)  # type: ignore[arg-type]


def test_the_client_sends_signed_json() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    body = {"event": "ticket.created", "ticket": {"subject": "नमस्ते"}}
    got = asyncio.run(_client(answer).send(
        "https://hooks.example.com/x", "whsec_s", delivery_id="d-1", event="ticket.created",
        body=body))  # fmt: skip
    assert (got.status, got.error, got.response) == (200, None, "ok")
    r = seen[0]
    assert r.method == "POST" and r.content == body_bytes(body)
    assert json.loads(r.content) == body
    assert r.headers["X-CeDesk-Event"] == "ticket.created"
    assert r.headers["X-CeDesk-Delivery"] == "d-1"
    ts = int(r.headers["X-CeDesk-Timestamp"])
    assert r.headers["X-CeDesk-Signature"] == rules.signature("whsec_s", ts, r.content)


def test_what_counts_as_not_delivered() -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    got = asyncio.run(_client(redirect).send("https://h.example.com", "s", delivery_id="d",
                                             event="e", body={}))  # fmt: skip
    assert got.status == 302 and got.error == "Redirects aren't followed."

    def big(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 10_000)

    got = asyncio.run(_client(big).send("https://h.example.com", "s", delivery_id="d",
                                        event="e", body={}))  # fmt: skip
    assert got.error == "HTTP 500." and got.response is not None and len(got.response) == 300

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    got = asyncio.run(_client(slow).send("https://h.example.com", "s", delivery_id="d",
                                         event="e", body={}))  # fmt: skip
    assert got.status is None and got.error is not None and "No answer" in got.error

    async def private(host: str) -> None:
        raise BadAddress(f"'{host}' isn't a public address.")

    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not connect")

    refused = _client(never, private)
    got = asyncio.run(
        refused.send("https://h.example.com", "s", delivery_id="d", event="e", body={})
    )
    assert got.status is None and got.error is not None and "public" in got.error
