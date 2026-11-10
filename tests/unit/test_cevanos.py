"""Cevanos connector pieces without a database: the webhook signature (Cevanos's
`t=<unix>,v1=<hmac>` over `<t>.<body>`), the sealed secrets, reading a chat, and the HTTP
client against a mock transport."""

import json
from typing import Any

import httpx
import pytest

from cedesk.cevanos_client import CevanosError, Connection, HttpCevanos, make_cevanos
from cedesk.crypto import CannotOpen, Sealer
from cedesk.helpdesk.domain import cevanos as rules
from cedesk.settings import Settings

NOW = 1_800_000_000


def test_signature_matches_cevanos_and_refuses_the_rest() -> None:
    body = b'{"event":"conversation.escalated"}'
    # Worked by hand: HMAC-SHA256("secret", "1800000000." + body).
    import hashlib
    import hmac

    expected = hmac.new(b"secret", b"1800000000." + body, hashlib.sha256).hexdigest()
    assert rules.sign("secret", body, NOW) == f"t={NOW},v1={expected}"
    rules.verify("secret", body, rules.sign("secret", body, NOW), now=NOW + 299)
    bad = [
        (None, "Missing"),
        ("v1=abc", "Malformed"),
        (rules.sign("secret", body, NOW - 301), "too old"),
        (rules.sign("secret", body, NOW + 301), "too old"),
        (rules.sign("other", body, NOW), "Wrong"),
        (rules.sign("secret", body + b" ", NOW), "Wrong"),
    ]
    for header, why in bad:
        with pytest.raises(rules.BadSignature, match=why):
            rules.verify("secret", body, header, now=NOW)


def test_events_are_read_strictly() -> None:
    event = rules.parse_event(
        json.dumps({"event": "conversation.escalated", "event_id": "e1",
                    "data": {"conversation_id": "01a1-b2", "reason": "manual_takeover"}}).encode()
    )  # fmt: skip
    assert event.conversation_id == "01a1-b2" and rules.is_echo(event)
    for raw in (b"not json", b"[]", b'{"event": 1}', b'{"event": "x", "event_id": "e"}'):
        with pytest.raises(rules.BadEvent):
            rules.parse_event(raw)
    odd = rules.WebhookEvent("conversation.closed", "e", {"conversation_id": "../x"})
    with pytest.raises(rules.BadEvent):
        _ = odd.conversation_id


def test_sealed_secrets_open_only_with_the_same_key_and_row() -> None:
    sealer = Sealer("k" * 32)
    sealed = sealer.seal("cvn_sk_secret", bound_to="ws-1:api_key")
    assert b"cvn_sk_secret" not in sealed
    assert sealer.open(sealed, bound_to="ws-1:api_key") == "cvn_sk_secret"
    assert sealer.seal("cvn_sk_secret", bound_to="ws-1:api_key") != sealed  # a fresh nonce
    with pytest.raises(CannotOpen):
        sealer.open(sealed, bound_to="ws-2:api_key")  # copied to another workspace
    with pytest.raises(CannotOpen):
        Sealer("j" * 32).open(sealed, bound_to="ws-1:api_key")  # CEDESK_SECRET_KEY changed
    with pytest.raises(CannotOpen):
        sealer.open(b"\x02" + sealed[1:], bound_to="ws-1:api_key")
    with pytest.raises(ValueError, match="too short"):
        Sealer("short")


def test_settings_values() -> None:
    assert rules.check_api_key("  cvn_sk_abcdEFGH1234  ") == "cvn_sk_abcdEFGH1234"
    for bad in ("sk_live_abc", "cvn_sk_short", "cvn_sk_has space here"):
        with pytest.raises(rules.BadSetting):
            rules.check_api_key(bad)
    assert rules.check_secret("whsec_0123456789abcdef") == "whsec_0123456789abcdef"
    for bad in ("short", "has a space in it ok", "x" * 201):
        with pytest.raises(rules.BadSetting):
            rules.check_secret(bad)
    assert rules.check_conversation_id(" 01a1-b2_c ") == "01a1-b2_c"
    with pytest.raises(rules.BadSetting):
        rules.check_conversation_id("a/b")


def test_reading_a_chat() -> None:
    raw = {"data": [
        {"id": "1", "role": "customer", "text": "Order  #4821\nlate",
         "created_at": "2026-11-10T10:00:00Z"},
        {"id": "2", "role": "agent", "text": "Checking", "created_at": "bad date"},
        {"id": "3", "role": "system", "text": "Handed over"},
        {"id": "4", "role": "customer", "text": "   "},
        {"id": 5, "role": "customer", "text": "no id"},
        "junk",
    ]}  # fmt: skip
    messages = rules.parse_messages(raw)
    assert [m.id for m in messages] == ["1", "2", "3", "4"]
    assert messages[0].created_at is not None and messages[1].created_at is None
    assert [m.id for m in rules.kept(messages)] == ["1", "2"]
    assert [m.id for m in rules.after(messages, "2")] == ["3", "4"]
    assert [m.id for m in rules.after(messages, "gone")] == ["1", "2", "3", "4"]
    assert rules.after(messages, "4") == []
    assert rules.subject(messages, "widget") == "Order #4821 late"
    assert rules.subject([], "whatsapp") == "Chat handed over from Cevanos (whatsapp)"
    assert rules.subject(rules.parse_messages({"data": [
        {"id": "1", "role": "customer", "text": "x" * 200}]}), None).endswith("…")  # fmt: skip
    assert rules.order_id(messages) == "4821"
    assert rules.contact_email("01a1-b2/../x") == "chat-01a1-b2x@cevanos.invalid"
    with pytest.raises(rules.BadEvent):
        rules.parse_messages({"error": {}})


def test_long_replies_split_and_polling_backs_off() -> None:
    assert rules.split_reply("hi") == ["hi"]
    parts = rules.split_reply("a" * 8001)
    assert [len(p) for p in parts] == [8000, 1]
    assert [rules.poll_wait(n) for n in (0, 1, 2, 3, 9)] == [5, 15, 60, 300, 300]
    assert rules.SENTIMENT["frustrated"] == "negative"


# The HTTP client


def _client(handler: Any) -> HttpCevanos:
    return HttpCevanos(
        Connection("https://api.cevanos.test/", "cvn_sk_test1234"),
        transport=httpx.MockTransport(handler),
    )


async def test_client_sends_key_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"data": [{"id": "1", "role": "customer", "text": "hi"}]}
            )
        return httpx.Response(201, json={"data": {}})

    api = _client(handler)
    assert (await api.messages("c/1"))["data"][0]["text"] == "hi"
    await api.take_over("c1", operator_name="Asha", key="k-1")
    await api.reply("c1", body="Done", operator_name="Asha", key="k-2")
    await api.close("c1", key="k-3")
    paths = [(r.method, r.url.raw_path.decode()) for r in seen]
    assert paths == [
        ("GET", "/v1/conversations/c%2F1/messages"),
        ("POST", "/v1/conversations/c1/take-over"),
        ("POST", "/v1/conversations/c1/reply"),
        ("POST", "/v1/conversations/c1/close"),
    ]
    assert all(r.headers["authorization"] == "Bearer cvn_sk_test1234" for r in seen)
    assert "idempotency-key" not in seen[0].headers
    assert [r.headers["idempotency-key"] for r in seen[1:]] == ["k-1", "k-2", "k-3"]
    assert json.loads(seen[2].content) == {"body": "Done", "operator_name": "Asha"}


@pytest.mark.parametrize(
    ("status", "permanent"),
    [(400, True), (401, True), (403, True), (404, True), (409, True), (422, True),
     (408, False), (429, False), (500, False), (502, False), (503, False)],
)  # fmt: skip
async def test_client_sorts_errors(status: int, permanent: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "conversation_not_found", "message": "No such conversation"}}
        return httpx.Response(status, json=body, headers={"Retry-After": "7"})

    with pytest.raises(CevanosError) as caught:
        await _client(handler).reply("c1", body="x", operator_name="A", key="k")
    assert caught.value.permanent is permanent and caught.value.status == status
    assert str(caught.value) == (
        f"Cevanos answered {status} conversation_not_found: No such conversation"
    )
    assert caught.value.retry_after == 7


async def test_client_network_errors_are_temporary_and_hide_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(CevanosError) as caught:
        await _client(handler).messages("c1")
    assert not caught.value.permanent and "cvn_sk" not in str(caught.value)

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    with pytest.raises(CevanosError, match="isn't JSON"):
        await _client(not_json).messages("c1")

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    with pytest.raises(CevanosError) as caught:
        await _client(redirect).messages("c1")  # a redirect is not followed
    assert caught.value.permanent


def test_the_address_is_a_deployment_setting() -> None:
    cevanos = make_cevanos(Settings(cevanos_url="https://cevanos.example/ "))
    assert cevanos.base_url == "https://cevanos.example"
    problems = Settings(env="production", cevanos_url="http://cevanos.example").unsafe()
    assert "CEDESK_CEVANOS_URL: use https" in problems
