"""The AI-app authorization server's rules (domain/oauth.py), without a database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import oauth

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)

# RFC 7636 appendix B.
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


# --- PKCE --------------------------------------------------------------------


def test_the_rfc_7636_example_verifier_matches_its_challenge() -> None:
    assert oauth.s256(VERIFIER) == CHALLENGE
    assert oauth.pkce_matches(VERIFIER, CHALLENGE)


def test_a_different_verifier_does_not_match() -> None:
    assert not oauth.pkce_matches(VERIFIER[:-1] + "A", CHALLENGE)


@pytest.mark.parametrize("verifier", ["short", "x" * 129, "a" * 42 + "!"])
def test_a_malformed_verifier_never_matches(verifier: str) -> None:
    assert not oauth.pkce_matches(verifier, oauth.s256("a" * 43))


def test_a_plain_challenge_is_not_a_challenge() -> None:
    # `plain` PKCE sends the verifier as the challenge; only S256 is accepted.
    assert not oauth.is_challenge(VERIFIER + "xyz")
    assert oauth.is_challenge(CHALLENGE)


# --- Redirect URIs -----------------------------------------------------------


def test_a_redirect_matches_only_exactly() -> None:
    registered = ("https://claude.ai/api/mcp/auth_callback",)
    assert oauth.redirect_matches(registered, "https://claude.ai/api/mcp/auth_callback")
    assert not oauth.redirect_matches(registered, "https://claude.ai/api/mcp/auth_callback/")
    assert not oauth.redirect_matches(registered, "https://claude.ai/api/mcp/auth_callback?x=1")
    assert not oauth.redirect_matches(registered, "https://evil.example/api/mcp/auth_callback")


@pytest.mark.parametrize(
    ("registered", "requested"),
    [
        ("http://127.0.0.1/callback", "http://127.0.0.1:53211/callback"),
        ("http://127.0.0.1:1/callback", "http://127.0.0.1:8787/callback"),
        ("http://[::1]/callback", "http://[::1]:40000/callback"),
        ("http://localhost:3000/callback", "http://localhost:61000/callback"),
    ],
)
def test_a_loopback_redirect_may_differ_only_in_its_port(registered: str, requested: str) -> None:
    assert oauth.redirect_matches((registered,), requested)


@pytest.mark.parametrize(
    ("registered", "requested"),
    [
        ("http://127.0.0.1/callback", "http://127.0.0.1:5000/other"),
        ("http://127.0.0.1/callback", "http://localhost:5000/callback"),
        ("http://127.0.0.1/callback", "http://127.0.0.1:5000/callback?x=1"),
        ("https://127.0.0.1/callback", "https://127.0.0.1:5000/callback"),
        ("http://127.0.0.1/callback/abc", "http://127.0.0.1:5000/callback"),
    ],
)
def test_a_loopback_redirect_keeps_its_host_path_and_query(registered: str, requested: str) -> None:
    assert not oauth.redirect_matches((registered,), requested)


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "http://127.0.0.1:33418",
        "http://localhost:8787/callback",
        "http://[::1]/callback",
    ],
)
def test_https_and_loopback_redirects_can_be_registered(uri: str) -> None:
    assert oauth.check_redirect_uri(uri) == uri


@pytest.mark.parametrize(
    ("uri", "reason"),
    [
        ("http://example.com/cb", "https"),
        ("https://example.com/cb#frag", "fragment"),
        ("https://user:pw@example.com/cb", "credentials"),
        ("javascript:alert(1)", "host"),
        ("com.example.app:/callback", "host"),
        ("https://" + "a" * 2050 + ".com/", "too long"),
    ],
)
def test_other_redirects_are_refused_with_a_reason(uri: str, reason: str) -> None:
    with pytest.raises(oauth.OAuthRuleError, match=reason):
        oauth.check_redirect_uri(uri)


def test_registering_needs_at_least_one_and_at_most_twenty_redirects() -> None:
    with pytest.raises(oauth.OAuthRuleError):
        oauth.check_redirect_uris([])
    with pytest.raises(oauth.OAuthRuleError):
        oauth.check_redirect_uris([f"https://a.example/{i}" for i in range(21)])
    assert oauth.check_redirect_uris(["https://a.example/1", "https://a.example/1"]) == (
        "https://a.example/1",
    )


def test_the_consent_screen_shows_the_redirect_host() -> None:
    assert oauth.redirect_host("https://chatgpt.com/connector/oauth/abc") == "chatgpt.com"
    assert oauth.redirect_host("http://127.0.0.1:5000/callback") == "127.0.0.1:5000"


def test_iss_is_added_to_the_redirect_without_losing_its_query() -> None:
    url = oauth.with_params(
        "https://client.example/cb?keep=1",
        {"code": "c", "state": "s", "iss": "https://mcp.cedesk.in"},
    )
    assert url == (
        "https://client.example/cb?keep=1&code=c&state=s&iss=https%3A%2F%2Fmcp.cedesk.in"
    )


# --- Scopes ------------------------------------------------------------------


def test_no_scope_named_means_the_defaults() -> None:
    assert oauth.parse_scopes(None) == oauth.DEFAULT_SCOPES
    assert oauth.parse_scopes("  ") == oauth.DEFAULT_SCOPES


def test_named_scopes_are_kept_once_each() -> None:
    assert oauth.parse_scopes("read read") == ("read",)


def test_an_unknown_scope_is_refused_not_dropped() -> None:
    with pytest.raises(oauth.OAuthRuleError, match="admin"):
        oauth.parse_scopes("read admin")


# --- Client ID Metadata Documents -------------------------------------------


@pytest.mark.parametrize(
    "client_id",
    [
        "https://chatgpt.com/oauth/client.json",
        "https://claude.ai/oauth/claude-code-client-metadata",
    ],
)
def test_a_metadata_document_url_is_accepted(client_id: str) -> None:
    assert oauth.check_metadata_document_url(client_id) == client_id


@pytest.mark.parametrize(
    "client_id",
    [
        "http://chatgpt.com/oauth/client.json",
        "https://chatgpt.com",
        "https://chatgpt.com/",
        "https://chatgpt.com/client.json?v=1",
        "https://chatgpt.com/client.json#x",
        "https://u@chatgpt.com/client.json",
        "https://chatgpt.com/a/../client.json",
    ],
)
def test_other_metadata_document_urls_are_refused(client_id: str) -> None:
    with pytest.raises(oauth.OAuthRuleError):
        oauth.check_metadata_document_url(client_id)


def _doc(**overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "client_id": "https://chatgpt.com/oauth/client.json",
        "client_name": "ChatGPT",
        "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "token_endpoint_auth_method": "none",
    }
    doc.update(overrides)
    return doc


def test_a_metadata_document_gives_the_client_its_name_and_redirects() -> None:
    client = oauth.parse_metadata_document("https://chatgpt.com/oauth/client.json", _doc())
    assert client.client_name == "ChatGPT"
    assert client.redirect_uris == ("https://chatgpt.com/connector_platform_oauth_redirect",)


def test_a_metadata_document_speaks_only_for_its_own_url() -> None:
    with pytest.raises(oauth.OAuthRuleError, match="does not match"):
        oauth.parse_metadata_document(
            "https://chatgpt.com/oauth/client.json",
            _doc(client_id="https://evil.example/client.json"),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_endpoint_auth_method": "client_secret_post"},
        {"client_secret": "s"},
        {"redirect_uris": "https://chatgpt.com/cb"},
        {"redirect_uris": []},
        {"redirect_uris": ["http://chatgpt.com/cb"]},
        {"client_name": 7},
    ],
)
def test_a_malformed_metadata_document_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(oauth.OAuthRuleError):
        oauth.parse_metadata_document("https://chatgpt.com/oauth/client.json", _doc(**overrides))


def test_a_metadata_document_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(oauth.OAuthRuleError):
        oauth.parse_metadata_document("https://chatgpt.com/oauth/client.json", ["x"])


@pytest.mark.parametrize(
    ("cache_control", "ttl"),
    [
        (None, timedelta(hours=1)),
        ("max-age=600", timedelta(minutes=10)),
        ("public, max-age=10", timedelta(minutes=5)),
        ("max-age=999999", timedelta(hours=24)),
        ("no-store", timedelta(minutes=5)),
    ],
)
def test_a_metadata_document_is_fetched_again_within_bounds(
    cache_control: str | None, ttl: timedelta
) -> None:
    assert oauth.document_expires_at(NOW, cache_control) == NOW + ttl


# --- Codes and tokens --------------------------------------------------------


def test_a_code_is_usable_once_and_only_before_it_expires() -> None:
    expires = NOW + oauth.CODE_TTL
    assert oauth.code_verdict(NOW, expires_at=expires, used_at=None) is oauth.CodeVerdict.usable
    assert (
        oauth.code_verdict(expires, expires_at=expires, used_at=None) is oauth.CodeVerdict.expired
    )
    assert oauth.code_verdict(NOW, expires_at=expires, used_at=NOW) is oauth.CodeVerdict.reused


def test_a_code_lives_at_most_a_minute() -> None:
    assert timedelta(seconds=60) >= oauth.CODE_TTL


def _refresh(**kw: datetime | None) -> oauth.RefreshVerdict:
    args: dict[str, datetime | None] = {
        "used_at": None,
        "revoked_at": None,
        "grant_revoked_at": None,
    }
    args.update(kw)
    return oauth.refresh_verdict(NOW, expires_at=NOW + timedelta(days=1), **args)


def test_a_fresh_refresh_token_is_usable() -> None:
    assert _refresh() is oauth.RefreshVerdict.usable


def test_a_rotated_refresh_token_that_comes_back_is_reuse() -> None:
    assert _refresh(used_at=NOW) is oauth.RefreshVerdict.reused


def test_a_revoked_grant_or_token_beats_everything() -> None:
    assert _refresh(grant_revoked_at=NOW, used_at=NOW) is oauth.RefreshVerdict.revoked
    assert _refresh(revoked_at=NOW) is oauth.RefreshVerdict.revoked


def test_an_expired_refresh_token_is_expired() -> None:
    verdict = oauth.refresh_verdict(
        NOW, expires_at=NOW, used_at=None, revoked_at=None, grant_revoked_at=None
    )
    assert verdict is oauth.RefreshVerdict.expired


def test_an_access_token_is_live_until_it_expires_or_is_revoked() -> None:
    later = NOW + oauth.ACCESS_TTL
    kw = {"revoked_at": None, "grant_revoked_at": None}
    assert oauth.access_is_live(NOW, expires_at=later, **kw)
    assert not oauth.access_is_live(later, expires_at=later, **kw)
    assert not oauth.access_is_live(NOW, expires_at=later, revoked_at=NOW, grant_revoked_at=None)
    assert not oauth.access_is_live(NOW, expires_at=later, revoked_at=None, grant_revoked_at=NOW)


def test_a_token_is_good_only_at_its_own_resource() -> None:
    assert oauth.resource_matches("https://mcp.cedesk.in/mcp", "https://mcp.cedesk.in/mcp")
    assert oauth.resource_matches("https://mcp.cedesk.in/mcp/", "https://mcp.cedesk.in/mcp")
    assert not oauth.resource_matches("https://mcp.cedesk.in/mcp", "https://desk.example.com/mcp")


# --- The authorization request -------------------------------------------------

RESOURCE = "https://mcp.cedesk.in/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _params(**overrides: str) -> dict[str, str]:
    params = {
        "client_id": "https://claude.ai/oauth/claude-client.json",
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "xyz",
    }
    params.update(overrides)
    return {k: v for k, v in params.items() if v != ""}


def _check(**overrides: str) -> oauth.AuthorizationRequest:
    return oauth.check_authorization_request(
        _params(**overrides), registered_redirects=(REDIRECT,), client_scope=None, resource=RESOURCE
    )


def test_a_good_request_defaults_its_scopes_and_resource() -> None:
    request = _check()
    assert request.scopes == ("read", "write")
    assert request.resource == RESOURCE
    assert request.redirect_uri_explicit
    assert request.state == "xyz"


def test_an_absent_redirect_uri_means_the_only_registered_one() -> None:
    request = _check(redirect_uri="")
    assert request.redirect_uri == REDIRECT
    assert not request.redirect_uri_explicit


def test_an_unregistered_redirect_is_never_redirected_to() -> None:
    with pytest.raises(oauth.NoRedirectError):
        _check(redirect_uri="https://evil.example/cb")
    with pytest.raises(oauth.NoRedirectError):
        oauth.check_authorization_request(
            _params(redirect_uri=""),
            registered_redirects=(REDIRECT, "https://claude.ai/other"),
            client_scope=None,
            resource=RESOURCE,
        )


def test_a_loopback_redirect_may_use_any_port() -> None:
    request = oauth.check_authorization_request(
        _params(redirect_uri="http://127.0.0.1:53111/callback"),
        registered_redirects=("http://127.0.0.1/callback",),
        client_scope=None,
        resource=RESOURCE,
    )
    assert request.redirect_uri == "http://127.0.0.1:53111/callback"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"code_challenge": CHALLENGE[:-1]}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"scope": "read admin"}, "invalid_scope"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"state": "s" * 2049}, "invalid_request"),
        ({"prompt": "none"}, "consent_required"),
    ],
)
def test_other_mistakes_go_back_to_the_client(overrides: dict[str, str], error: str) -> None:
    with pytest.raises(oauth.AuthorizeError) as raised:
        _check(**overrides)
    assert raised.value.error == error


def test_the_resource_may_carry_a_trailing_slash() -> None:
    assert _check(resource=RESOURCE + "/").resource == RESOURCE


def test_a_client_cannot_ask_for_more_than_it_registered() -> None:
    with pytest.raises(oauth.AuthorizeError):
        oauth.check_authorization_request(
            _params(scope="read write"),
            registered_redirects=(REDIRECT,),
            client_scope="read",
            resource=RESOURCE,
        )


# --- The signed request handle -------------------------------------------------

KEY = "test-only-request-signing-key-0000000000"


def test_a_sealed_request_opens_to_itself() -> None:
    request = _check()
    handle = oauth.seal_request(request, key=KEY, now=NOW)
    assert oauth.open_request(handle, key=KEY, now=NOW + timedelta(minutes=9)) == request


def test_a_handle_expires_after_ten_minutes() -> None:
    handle = oauth.seal_request(_check(), key=KEY, now=NOW)
    with pytest.raises(oauth.OAuthRuleError, match="expired"):
        oauth.open_request(handle, key=KEY, now=NOW + oauth.REQUEST_TTL)


def test_a_handle_signed_with_another_key_is_refused() -> None:
    handle = oauth.seal_request(_check(), key=KEY, now=NOW)
    with pytest.raises(oauth.OAuthRuleError):
        oauth.open_request(handle, key=KEY.replace("0", "1"), now=NOW)


def test_a_tampered_handle_is_refused() -> None:
    import base64
    import json

    handle = oauth.seal_request(_check(), key=KEY, now=NOW)
    payload, _, signature = handle.partition(".")
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    body["r"] = "https://evil.example/cb"
    forged = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    for bad in (f"{forged}.{signature}", "garbage", "a.b", f"{payload}.", ""):
        with pytest.raises(oauth.OAuthRuleError):
            oauth.open_request(bad, key=KEY, now=NOW)


# --- Codes and tokens name their workspace -------------------------------------------


def test_a_secret_names_its_workspace_and_only_with_its_own_prefix() -> None:
    ws = uuid4()
    code = oauth.new_secret(oauth.CODE_PREFIX, ws)
    assert oauth.secret_workspace(oauth.CODE_PREFIX, code) == ws
    assert oauth.secret_workspace(oauth.ACCESS_PREFIX, code) is None
    assert oauth.secret_hash(code) != oauth.secret_hash(oauth.new_secret(oauth.CODE_PREFIX, ws))
    assert len(oauth.secret_hash(code)) == 32


@pytest.mark.parametrize(
    "value", ["cdoa_", "cdoa_nothex_abc", "cdoa_" + "a" * 32, "cdoa_" + "a" * 32 + "_x" * 200]
)
def test_a_malformed_secret_names_no_workspace(value: str) -> None:
    assert oauth.secret_workspace(oauth.ACCESS_PREFIX, value) is None


def test_a_registered_client_id_never_looks_like_a_metadata_url() -> None:
    assert not oauth.is_metadata_document_url(oauth.new_client_id())


def test_a_client_secret_is_checked_against_its_hash_only() -> None:
    secret = oauth.new_client_secret()
    assert oauth.secret_matches(secret, oauth.secret_hash(secret))
    assert not oauth.secret_matches(secret + "x", oauth.secret_hash(secret))
    assert not oauth.secret_matches(secret, None)


# --- Dynamic registration ----------------------------------------------------------


def test_a_public_registration_is_accepted() -> None:
    reg = oauth.parse_registration(
        {
            "client_name": "Cursor",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "read",
        }
    )
    assert reg.token_endpoint_auth_method == "none"
    assert reg.scope == "read"


def test_registration_defaults_to_client_secret_basic() -> None:
    reg = oauth.parse_registration({"redirect_uris": ["https://app.example/cb"]})
    assert reg.token_endpoint_auth_method == "client_secret_basic"


@pytest.mark.parametrize(
    ("doc", "error"),
    [
        ({"redirect_uris": ["http://app.example/cb"]}, "invalid_redirect_uri"),
        ({"redirect_uris": "https://app.example/cb"}, "invalid_redirect_uri"),
        ({}, "invalid_redirect_uri"),
        ({"redirect_uris": ["https://a.example/cb"], "grant_types": ["password"]},
         "invalid_client_metadata"),
        ({"redirect_uris": ["https://a.example/cb"], "response_types": ["token"]},
         "invalid_client_metadata"),
        ({"redirect_uris": ["https://a.example/cb"], "token_endpoint_auth_method": "jwt"},
         "invalid_client_metadata"),
        ({"redirect_uris": ["https://a.example/cb"], "logo_uri": "http://a.example/l.png"},
         "invalid_client_metadata"),
        ({"redirect_uris": ["https://a.example/cb"], "scope": "admin"}, "invalid_client_metadata"),
    ],
)  # fmt: skip
def test_a_bad_registration_says_which_rfc_7591_error(doc: dict[str, object], error: str) -> None:
    with pytest.raises(oauth.RegistrationError) as raised:
        oauth.parse_registration(doc)
    assert raised.value.error == error


def test_basic_client_credentials_are_read_form_decoded() -> None:
    import base64

    header = "Basic " + base64.b64encode(b"my%20app:s%3Acret").decode()
    assert oauth.basic_credentials(header) == ("my app", "s:cret")
    assert oauth.basic_credentials("Bearer x") is None
    assert oauth.basic_credentials("Basic !!!") is None


def test_an_app_without_a_name_is_shown_by_its_domain() -> None:
    assert oauth.client_label("Claude", "x") == "Claude"
    assert oauth.client_label(None, "https://chatgpt.com/oauth/c.json") == "chatgpt.com"
    assert oauth.client_label(None, "cdoapp_x") == "An unnamed app"
