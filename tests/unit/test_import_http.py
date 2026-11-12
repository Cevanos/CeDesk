"""The one HTTP client every importer uses: read only, the guard runs before each call, a
redirect is never followed, and every status maps to what a run should do about it."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from cedesk.egress import BadAddress
from cedesk.helpdesk.domain.imports import RateLimited, SourceError
from cedesk.helpdesk.service.imports import http


async def allow(host: str) -> None:
    return None


async def refuse(host: str) -> None:
    raise BadAddress("'x' isn't a public address. Check the account name.")


def client(handler: Any, *, guard: Any = allow) -> http.SourceHttp:
    return http.SourceHttp(
        "acme.zendesk.com",
        headers=http.bearer("tok"),
        guard=guard,
        transport=httpx.MockTransport(handler),
        label="Zendesk",
    )


def get(c: http.SourceHttp, path: str = "/x", **kw: Any) -> Any:
    return asyncio.run(c.get(path, **kw))


def test_a_get_sends_the_credentials_and_returns_the_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    assert get(client(handler), "/api/v2/tickets", params={"page": 2, "skip": None}) == {"ok": True}
    request = seen[0]
    assert str(request.url) == "https://acme.zendesk.com/api/v2/tickets?page=2"
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["user-agent"].startswith("CeDesk/")
    assert request.method == "GET"


def test_the_guard_runs_before_the_call_and_a_refusal_is_permanent() -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    with pytest.raises(SourceError) as caught:
        get(client(handler, guard=refuse))
    assert caught.value.permanent and "public address" in str(caught.value)
    assert called == []  # nothing was sent


def test_a_redirect_is_never_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.zendesk.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        raise AssertionError("the redirect was followed")

    with pytest.raises(SourceError) as caught:
        get(client(handler))
    assert caught.value.permanent and "not followed" in str(caught.value)


@pytest.mark.parametrize(
    ("status", "permanent"),
    [(401, True), (403, True), (404, True), (400, True), (422, True),
     (408, False), (500, False), (502, False), (503, False)],
)  # fmt: skip
def test_statuses_are_permanent_or_worth_retrying(status: int, permanent: bool) -> None:
    with pytest.raises(SourceError) as caught:
        get(client(lambda r: httpx.Response(status, json={})))
    assert caught.value.permanent is permanent


def test_credentials_never_appear_in_an_error() -> None:
    with pytest.raises(SourceError) as caught:
        get(client(lambda r: httpx.Response(401, json={"error": "bad token tok"})))
    assert "tok" not in str(caught.value) and "Bearer" not in str(caught.value)


def test_a_404_can_be_expected() -> None:
    assert get(client(lambda r: httpx.Response(404)), missing_ok=True) is None


def test_a_429_carries_retry_after() -> None:
    with pytest.raises(RateLimited) as caught:
        get(client(lambda r: httpx.Response(429, headers={"Retry-After": "42"})))
    assert caught.value.retry_after == 42
    with pytest.raises(RateLimited) as caught:
        get(client(lambda r: httpx.Response(429)))
    assert caught.value.retry_after is None


def test_a_dropped_connection_and_a_non_json_answer_are_worth_retrying() -> None:
    def dropped(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(SourceError) as caught:
        get(client(dropped))
    assert caught.value.permanent is False and "can't be reached" in str(caught.value)
    with pytest.raises(SourceError) as caught:
        get(client(lambda r: httpx.Response(200, text="<html>")))
    assert caught.value.permanent is False and "isn't JSON" in str(caught.value)


def test_retry_after_reads_seconds_or_a_reset_time() -> None:
    now = datetime(2025, 3, 1, 10, 0, 0, tzinfo=UTC)
    assert http.retry_after({"retry-after": "30"}, now) == 30
    assert http.retry_after({"x-ratelimit-retry-after": "7"}, now) == 7  # Help Scout
    assert http.retry_after({"retry-after": "999999"}, now) == http.MAX_RETRY_AFTER
    reset = str(int(now.timestamp()) + 12)
    assert http.retry_after({"x-ratelimit-reset": reset}, now) == 12
    assert http.retry_after({"x-ratelimit-reset": str(int(now.timestamp()) - 5)}, now) == 1
    assert http.retry_after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, now) is None
    assert http.retry_after({}, now) is None


def test_times_are_read_from_iso_and_unix() -> None:
    expected = datetime(2025, 3, 1, 10, 15, tzinfo=UTC)
    assert http.when("2025-03-01T10:15:00Z") == expected
    assert http.when("2025-03-01T10:15:00+00:00") == expected
    assert http.when("2025-03-01T11:15:00+01:00") == expected
    assert http.when("2025-03-01T10:15:00") == expected  # no zone: UTC
    assert http.when(int(expected.timestamp())) == expected
    assert http.when(str(int(expected.timestamp()))) == expected
    for nothing in (None, "", "yesterday", True, "99999999999999999999"):
        assert http.when(nothing) is None


def test_an_id_from_the_source_cannot_add_a_path_segment() -> None:
    assert http.segment(123) == "123"
    assert http.segment("a/b") == "a%2Fb"
    assert http.segment("../x") == "..%2Fx"
    assert http.basic("a@b.c", "tok")["Authorization"] == "Basic YUBiLmM6dG9r"


def test_only_the_oauth_token_exchange_may_post() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "t"})

    c = client(handler)
    got = asyncio.run(c.exchange_token("/v2/oauth2/token", {"grant_type": "client_credentials"}))
    assert got == {"access_token": "t"}
    assert seen[0].method == "POST" and b"client_credentials" in seen[0].content
    for path in ("/v2/conversations", "/v2/oauth2/token/../x", "/tickets"):
        with pytest.raises(ValueError, match="OAuth token path"):
            asyncio.run(c.exchange_token(path, {}))
    assert len(seen) == 1  # nothing else was sent
