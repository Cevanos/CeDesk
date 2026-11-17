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


def test_zendesk_token_path_is_allowed_exactly() -> None:
    """Zendesk's token endpoint is `/oauth/tokens`; its `/api/v2/oauth/tokens` (the OAuth
    Tokens API, which creates tokens) and anything else ending alike stay refused."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"access_token": "t", "token_type": "bearer"})

    c = client(handler)
    got = asyncio.run(c.exchange_token("/oauth/tokens", {"grant_type": "client_credentials"}))
    assert got["access_token"] == "t" and seen[0].url.path == "/oauth/tokens"
    for path in ("/api/v2/oauth/tokens", "/x/oauth/tokens", "/oauth/tokens/../x"):
        with pytest.raises(ValueError, match="OAuth token path"):
            asyncio.run(c.exchange_token(path, {}))
    assert len(seen) == 1


# Files a source message points to (`download`)


def fetch(c: http.SourceHttp, url: str, limit: int = 1000) -> bytes | None:
    return asyncio.run(c.download(url, limit))


def test_a_download_returns_the_bytes_and_sends_credentials_only_to_the_account_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"data")

    c = client(handler)
    assert fetch(c, "https://acme.zendesk.com/attachments/1") == b"data"
    assert fetch(c, "https://files.cdn.example/attachments/1") == b"data"
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert "authorization" not in seen[1].headers  # a storage host never gets the key


def test_a_download_follows_a_redirect_by_hand_and_drops_the_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "acme.zendesk.com":
            return httpx.Response(302, headers={"location": "https://s3.example.com/f?sig=1"})
        return httpx.Response(200, content=b"stored")

    assert fetch(client(handler), "https://acme.zendesk.com/attachments/token/x") == b"stored"
    assert [r.url.host for r in seen] == ["acme.zendesk.com", "s3.example.com"]
    assert "authorization" not in seen[1].headers


def test_a_download_checks_every_hop_and_stops_after_three() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://s3.example.com/again"})

    assert fetch(client(handler), "https://s3.example.com/start") is None

    checked: list[str] = []

    async def guard(host: str) -> None:
        checked.append(host)
        if host == "evil.internal":
            raise BadAddress("not public")

    def to_internal(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.internal/x"})

    assert fetch(client(to_internal, guard=guard), "https://acme.zendesk.com/a") is None
    assert checked == ["acme.zendesk.com", "evil.internal"]


def test_a_download_refuses_what_is_not_plain_https() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made")

    c = client(handler)
    for url in (
        "http://acme.zendesk.com/a",
        "https://acme.zendesk.com:8443/a",
        "file:///etc/passwd",
        "https:///nohost",
        "ftp://acme.zendesk.com/a",
    ):
        assert fetch(c, url) is None
    assert fetch(client(handler, guard=refuse), "https://acme.zendesk.com/a") is None


def test_a_download_is_none_for_a_missing_file_and_for_one_over_the_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gone"):
            return httpx.Response(404)
        if request.url.path.endswith("/denied"):
            return httpx.Response(403)
        if request.url.path.endswith("/declared"):
            return httpx.Response(200, headers={"content-length": "5000"}, content=b"x" * 5000)
        return httpx.Response(200, content=b"x" * 2000)

    c = client(handler)
    for name in ("gone", "denied", "declared", "streamed"):
        assert fetch(c, f"https://acme.zendesk.com/{name}", 1000) is None
    assert len(fetch(c, "https://acme.zendesk.com/streamed", 2000) or b"") == 2000


def test_a_download_pauses_on_429_and_retries_on_a_server_error() -> None:
    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "30"})

    with pytest.raises(RateLimited) as caught:
        fetch(client(limited), "https://acme.zendesk.com/a")
    assert caught.value.retry_after == 30

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(SourceError) as err:
        fetch(client(down), "https://acme.zendesk.com/a")
    assert err.value.permanent is False
