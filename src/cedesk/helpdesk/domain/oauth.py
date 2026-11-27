"""The rules of the OAuth 2.1 authorization server behind the MCP connector (AI apps
such as Claude and ChatGPT reaching a member's workspace), without a database.

The routes and the service hold the state; this module decides what a redirect URI, a
PKCE verifier, a client metadata document, an authorization request or a presented code
or refresh token is allowed to be.

Codes, access tokens and refresh tokens are `<prefix><workspace hex>_<secret>`, like API
keys: the workspace part only says where to look; the SHA-256 of the whole value must
match a stored hash in that workspace, so editing it gains nothing. Only the hash is kept.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit
from uuid import UUID

# An authorization code is redeemed by the client's own backend or CLI seconds
# after the redirect; a minute covers a slow hop without leaving a usable code in
# a browser history entry for longer.
CODE_TTL = timedelta(seconds=60)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)

# A tool still checks the member's own capabilities; the scope only narrows what
# the member let this client do.
SCOPES: tuple[str, ...] = ("read", "write")
DEFAULT_SCOPES: tuple[str, ...] = SCOPES

MAX_REDIRECT_URIS = 20
_MAX_URI = 2048
# `urlsplit().hostname` gives `::1` for `http://[::1]:8080/`.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# RFC 7636 4.1: 43-128 characters of the unreserved set.
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
# base64url of a sha256, unpadded: always 43 characters.
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")

# A metadata document is fetched again no sooner than this and no later than
# that, whatever its Cache-Control says.
DOCUMENT_MIN_TTL = timedelta(minutes=5)
DOCUMENT_MAX_TTL = timedelta(hours=24)
DOCUMENT_DEFAULT_TTL = timedelta(hours=1)

# The signed handle that carries a checked authorization request to the consent
# page and back. Long enough to sign in first; no table holds it.
REQUEST_TTL = timedelta(minutes=10)
_MAX_STATE = 2048

# What each scope lets a tool do; the member's own rights still apply on top.
READ, WRITE = "read", "write"

CODE_PREFIX = "cdoc_"
ACCESS_PREFIX = "cdoa_"
REFRESH_PREFIX = "cdor_"
CLIENT_PREFIX = "cdoapp_"
SECRET_PREFIX = "cdosec_"  # noqa: S105  (a prefix, not a secret)
_SECRET_BYTES = 32
_HEX = 32  # a UUID without dashes


def new_secret(prefix: str, workspace_id: UUID) -> str:
    """A code or token naming its workspace. 256 random bits, so SHA-256 is enough."""
    return f"{prefix}{workspace_id.hex}_{secrets.token_urlsafe(_SECRET_BYTES)}"


def secret_workspace(prefix: str, value: str) -> UUID | None:
    """The workspace a code or token names, or None when it isn't shaped like one."""
    if not value.startswith(prefix) or len(value) > 200:
        return None
    head, sep, rest = value[len(prefix) :].partition("_")
    if len(head) != _HEX or not sep or not rest:
        return None
    try:
        return UUID(hex=head)
    except ValueError:
        return None


def secret_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def new_client_id() -> str:
    """For a registered client. Never `https://`, so it can't pass for a metadata URL."""
    return f"{CLIENT_PREFIX}{secrets.token_urlsafe(18)}"


def new_client_secret() -> str:
    return f"{SECRET_PREFIX}{secrets.token_urlsafe(_SECRET_BYTES)}"


def secret_matches(presented: str, stored_hash: bytes | None) -> bool:
    if stored_hash is None:
        return False
    return hmac.compare_digest(secret_hash(presented), stored_hash)


class OAuthRuleError(ValueError):
    """A value the authorization server refuses. `reason` is safe to show the client."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- PKCE --------------------------------------------------------------------


def is_challenge(challenge: str) -> bool:
    return bool(_CHALLENGE.fullmatch(challenge))


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def pkce_matches(verifier: str, challenge: str) -> bool:
    """S256 only. A malformed verifier never matches."""
    if not _VERIFIER.fullmatch(verifier) or not is_challenge(challenge):
        return False
    return hmac.compare_digest(s256(verifier), challenge)


# --- Redirect URIs -----------------------------------------------------------


def is_loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK_HOSTS


def _host_with_brackets(uri: str) -> str:
    netloc = urlsplit(uri).netloc
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host[: host.index("]") + 1]
    return host.split(":", 1)[0]


def redirect_matches(registered: tuple[str, ...] | list[str], requested: str) -> bool:
    """Exact string match, except that a loopback URI may differ in its port.

    RFC 8252 7.3: a native client binds whatever free port it gets, so for
    `http://127.0.0.1`, `http://[::1]` and `http://localhost` only the port is
    allowed to vary. Path and query must still be identical.
    """
    if requested in registered:
        return True
    if not is_loopback(requested):
        return False
    want = urlsplit(requested)
    want_host = _host_with_brackets(requested)
    for candidate in registered:
        if not is_loopback(candidate):
            continue
        have = urlsplit(candidate)
        if (
            _host_with_brackets(candidate) == want_host
            and have.path == want.path
            and have.query == want.query
            and have.fragment == want.fragment == ""
        ):
            return True
    return False


def check_redirect_uri(uri: str) -> str:
    """A redirect URI a client may register or publish. Returns it unchanged.

    `https://` anywhere, or `http://` on a loopback host. No fragment and no
    userinfo. Private-use schemes (`com.example.app:/cb`) are not accepted yet.
    """
    if len(uri) > _MAX_URI:
        raise OAuthRuleError("redirect_uri is too long")
    parts = urlsplit(uri)
    if parts.fragment or "#" in uri:
        raise OAuthRuleError("redirect_uri must not have a fragment")
    if parts.username or parts.password or "@" in parts.netloc:
        raise OAuthRuleError("redirect_uri must not carry credentials")
    if not parts.hostname:
        raise OAuthRuleError("redirect_uri must name a host")
    if parts.scheme == "https":
        return uri
    if parts.scheme == "http" and is_loopback(uri):
        return uri
    raise OAuthRuleError("redirect_uri must be https, or http on a loopback address")


def check_redirect_uris(uris: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not uris:
        raise OAuthRuleError("redirect_uris is required")
    if len(uris) > MAX_REDIRECT_URIS:
        raise OAuthRuleError(f"at most {MAX_REDIRECT_URIS} redirect_uris")
    return tuple(check_redirect_uri(u) for u in dict.fromkeys(uris))


def redirect_host(uri: str) -> str:
    """What the consent screen shows: where the code will be sent."""
    return urlsplit(uri).netloc.rsplit("@", 1)[-1]


def with_params(redirect_uri: str, params: dict[str, str]) -> str:
    """`redirect_uri` with `params` added to its query, keeping what it had.

    Every authorization response carries `iss` (RFC 9207), errors included, so
    callers pass it here rather than building the query themselves.
    """
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((k, v) for k, v in params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


# --- Scopes ------------------------------------------------------------------


def parse_scopes(requested: str | None) -> tuple[str, ...]:
    """The scopes a request asks for; none named means the defaults.

    An unknown scope is refused rather than dropped, so the client learns its
    request was not what it got.
    """
    if requested is None or not requested.strip():
        return DEFAULT_SCOPES
    asked = tuple(dict.fromkeys(requested.split()))
    unknown = [s for s in asked if s not in SCOPES]
    if unknown:
        raise OAuthRuleError(f"unknown scope: {' '.join(unknown)}")
    return asked


# --- Client ID Metadata Documents -------------------------------------------


def is_metadata_document_url(client_id: str) -> bool:
    return client_id.startswith("https://")


def check_metadata_document_url(client_id: str) -> str:
    """A client_id that names its own metadata document.

    https, a host, a path other than `/`, no query, no fragment, no userinfo and
    no dot segments. The fetch itself still goes through the egress guard.
    """
    if len(client_id) > _MAX_URI:
        raise OAuthRuleError("client_id is too long")
    parts = urlsplit(client_id)
    if parts.scheme != "https" or not parts.hostname:
        raise OAuthRuleError("client_id must be an https URL")
    if parts.username or parts.password or "@" in parts.netloc:
        raise OAuthRuleError("client_id must not carry credentials")
    if parts.query or parts.fragment or "?" in client_id or "#" in client_id:
        raise OAuthRuleError("client_id must not have a query or fragment")
    if parts.path in ("", "/"):
        raise OAuthRuleError("client_id must have a path")
    if any(seg in (".", "..") for seg in parts.path.split("/")):
        raise OAuthRuleError("client_id must not contain dot segments")
    return client_id


@dataclass(frozen=True, slots=True)
class ClientDocument:
    client_id: str
    client_name: str | None
    client_uri: str | None
    logo_uri: str | None
    redirect_uris: tuple[str, ...]


def _optional_text(doc: dict[str, object], key: str, limit: int) -> str | None:
    value = doc.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise OAuthRuleError(f"{key} must be a string")
    return value[:limit] or None


def parse_metadata_document(client_id: str, doc: object) -> ClientDocument:
    """The fetched document, checked. It speaks only for the URL it came from.

    `client_id` in the document must equal the URL. The client is public: a
    document that asks for a secret-based token endpoint method is refused.
    """
    if not isinstance(doc, dict):
        raise OAuthRuleError("client metadata document must be a JSON object")
    if doc.get("client_id") != client_id:
        raise OAuthRuleError("client_id in the document does not match its URL")
    method = doc.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise OAuthRuleError("a metadata document client must use token_endpoint_auth_method none")
    if "client_secret" in doc:
        raise OAuthRuleError("a metadata document must not contain a client_secret")
    raw = doc.get("redirect_uris")
    if not isinstance(raw, list) or not all(isinstance(u, str) for u in raw):
        raise OAuthRuleError("redirect_uris must be a list of strings")
    return ClientDocument(
        client_id=client_id,
        client_name=_optional_text(doc, "client_name", 200),
        client_uri=_optional_text(doc, "client_uri", _MAX_URI),
        logo_uri=_optional_text(doc, "logo_uri", _MAX_URI),
        redirect_uris=check_redirect_uris(raw),
    )


_MAX_AGE = re.compile(r"(?:^|[,\s])max-age\s*=\s*(\d+)", re.IGNORECASE)


def document_expires_at(now: datetime, cache_control: str | None) -> datetime:
    """When to fetch the document again: its max-age, clamped to 5 min - 24 h."""
    ttl = DOCUMENT_DEFAULT_TTL
    if cache_control:
        lowered = cache_control.lower()
        if "no-store" in lowered or "no-cache" in lowered:
            ttl = DOCUMENT_MIN_TTL
        elif match := _MAX_AGE.search(cache_control):
            ttl = timedelta(seconds=int(match.group(1)))
    return now + min(max(ttl, DOCUMENT_MIN_TTL), DOCUMENT_MAX_TTL)


# --- Codes and tokens --------------------------------------------------------


class CodeVerdict(StrEnum):
    usable = "usable"
    expired = "expired"
    # Presented twice: the second redemption revokes what the first one issued.
    reused = "reused"


def code_verdict(now: datetime, *, expires_at: datetime, used_at: datetime | None) -> CodeVerdict:
    if used_at is not None:
        return CodeVerdict.reused
    if now >= expires_at:
        return CodeVerdict.expired
    return CodeVerdict.usable


class RefreshVerdict(StrEnum):
    usable = "usable"
    expired = "expired"
    revoked = "revoked"
    # A rotated refresh token came back: someone holds a copy, so the family ends.
    reused = "reused"


def refresh_verdict(
    now: datetime,
    *,
    expires_at: datetime,
    used_at: datetime | None,
    revoked_at: datetime | None,
    grant_revoked_at: datetime | None,
) -> RefreshVerdict:
    if revoked_at is not None or grant_revoked_at is not None:
        return RefreshVerdict.revoked
    if used_at is not None:
        return RefreshVerdict.reused
    if now >= expires_at:
        return RefreshVerdict.expired
    return RefreshVerdict.usable


def access_is_live(
    now: datetime,
    *,
    expires_at: datetime,
    revoked_at: datetime | None,
    grant_revoked_at: datetime | None,
) -> bool:
    return revoked_at is None and grant_revoked_at is None and now < expires_at


def resource_matches(issued_for: str, presented_to: str) -> bool:
    """A token is good only at the resource it was issued for (RFC 8707)."""
    return issued_for.rstrip("/") == presented_to.rstrip("/")


# --- The authorization request -----------------------------------------------


class NoRedirectError(OAuthRuleError):
    """The client or its redirect URI is not known: answer the browser directly.

    Redirecting here would send the browser wherever the request said, which is
    an open redirect (RFC 6749 4.1.2.1).
    """


class AuthorizeError(OAuthRuleError):
    """An error the client hears about at its redirect URI, with `state` and `iss`."""

    def __init__(self, error: str, reason: str) -> None:
        super().__init__(reason)
        self.error = error


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """A checked request to /authorize: everything the code will be bound to."""

    client_id: str
    redirect_uri: str
    redirect_uri_explicit: bool
    code_challenge: str
    scopes: tuple[str, ...]
    resource: str
    state: str | None


def resolve_redirect_uri(registered: tuple[str, ...], requested: str | None) -> tuple[str, bool]:
    """The redirect URI to use and whether the request named it.

    Raises `NoRedirectError`: until this has passed there is nowhere safe to
    send an error.
    """
    if requested:
        if not redirect_matches(registered, requested):
            raise NoRedirectError("redirect_uri is not registered for this client")
        return requested, True
    if len(registered) == 1:
        return registered[0], False
    raise NoRedirectError("redirect_uri is required when a client registers more than one")


def check_authorization_request(
    params: Mapping[str, str],
    *,
    registered_redirects: tuple[str, ...],
    client_scope: str | None,
    resource: str,
) -> AuthorizationRequest:
    """Check an authorization request for a known client.

    `resource` is the one MCP resource this server issues for; an absent
    `resource` parameter means it. Raises `NoRedirectError` for a bad redirect
    URI and `AuthorizeError` for everything after it.
    """
    redirect_uri, explicit = resolve_redirect_uri(registered_redirects, params.get("redirect_uri"))
    if params.get("response_type") != "code":
        raise AuthorizeError("unsupported_response_type", "response_type must be code")
    method = params.get("code_challenge_method") or "S256"
    if method != "S256":
        raise AuthorizeError("invalid_request", "code_challenge_method must be S256")
    challenge = params.get("code_challenge") or ""
    if not is_challenge(challenge):
        raise AuthorizeError("invalid_request", "code_challenge must be an S256 challenge")
    try:
        scopes = parse_scopes(params.get("scope"))
    except OAuthRuleError as exc:
        raise AuthorizeError("invalid_scope", exc.reason) from exc
    if client_scope is not None:
        allowed = set(client_scope.split())
        if not set(scopes) <= allowed:
            raise AuthorizeError("invalid_scope", "scope exceeds what this client registered")
    requested = params.get("resource")
    if requested is not None and not resource_matches(resource, requested):
        raise AuthorizeError(
            "invalid_target", "resource is not served by this authorization server"
        )
    state = params.get("state")
    if state is not None and len(state) > _MAX_STATE:
        raise AuthorizeError("invalid_request", "state is too long")
    # Every grant is a member saying yes on our consent page.
    if params.get("prompt") == "none":
        raise AuthorizeError("consent_required", "this server always asks the member")
    return AuthorizationRequest(
        client_id=params.get("client_id", ""),
        redirect_uri=redirect_uri,
        redirect_uri_explicit=explicit,
        code_challenge=challenge,
        scopes=scopes,
        resource=resource,
        state=state,
    )


# --- The signed request handle -------------------------------------------------

_HANDLE_PURPOSE = b"cedesk-oauth-authorization-request.v1."


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal_request(request: AuthorizationRequest, *, key: str, now: datetime) -> str:
    """The handle the consent page carries: the request, its expiry and an HMAC.

    Not encrypted. It holds nothing the browser did not already send.
    """
    body = {
        "c": request.client_id,
        "r": request.redirect_uri,
        "x": request.redirect_uri_explicit,
        "p": request.code_challenge,
        "s": list(request.scopes),
        "a": request.resource,
        "t": request.state,
        "e": int((now + REQUEST_TTL).timestamp()),
    }
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = hmac.new(key.encode("utf-8"), _HANDLE_PURPOSE + payload.encode("ascii"), "sha256")
    return f"{payload}.{_b64(mac.digest())}"


def open_request(handle: str, *, key: str, now: datetime) -> AuthorizationRequest:
    """The request a handle carries, if its signature holds and it has not expired."""
    payload, _, signature = handle.partition(".")
    if not payload or not signature:
        raise OAuthRuleError("this request is not one we issued")
    expected = hmac.new(key.encode("utf-8"), _HANDLE_PURPOSE + payload.encode("ascii"), "sha256")
    try:
        presented = _unb64(signature)
        body = json.loads(_unb64(payload))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise OAuthRuleError("this request is not one we issued") from exc
    if not hmac.compare_digest(expected.digest(), presented):
        raise OAuthRuleError("this request is not one we issued")
    if not isinstance(body, dict) or now >= datetime.fromtimestamp(int(body["e"]), UTC):
        raise OAuthRuleError("this request has expired; start again from the app")
    return AuthorizationRequest(
        client_id=str(body["c"]),
        redirect_uri=str(body["r"]),
        redirect_uri_explicit=bool(body["x"]),
        code_challenge=str(body["p"]),
        scopes=tuple(str(s) for s in body["s"]),
        resource=str(body["a"]),
        state=None if body["t"] is None else str(body["t"]),
    )


# --- Dynamic client registration (RFC 7591) -----------------------------------

AUTH_METHODS: tuple[str, ...] = ("none", "client_secret_post", "client_secret_basic")
GRANT_TYPES: tuple[str, ...] = ("authorization_code", "refresh_token")


class RegistrationError(OAuthRuleError):
    """RFC 7591 3.2.2: `error` is invalid_redirect_uri or invalid_client_metadata."""

    def __init__(self, error: str, reason: str) -> None:
        super().__init__(reason)
        self.error = error


@dataclass(frozen=True, slots=True)
class Registration:
    client_name: str | None
    client_uri: str | None
    logo_uri: str | None
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    scope: str | None


def _https_or_none(doc: Mapping[str, object], key: str) -> str | None:
    value = _optional_text(dict(doc), key, _MAX_URI)
    if value is not None and urlsplit(value).scheme != "https":
        raise RegistrationError("invalid_client_metadata", f"{key} must be an https URL")
    return value


def parse_registration(doc: object) -> Registration:
    """A client's registration request, checked. RFC 7591's default auth method is
    client_secret_basic; public clients (most AI apps) send `none`."""
    if not isinstance(doc, dict):
        raise RegistrationError("invalid_client_metadata", "send a JSON object")
    raw = doc.get("redirect_uris")
    if not isinstance(raw, list) or not all(isinstance(u, str) for u in raw):
        raise RegistrationError("invalid_redirect_uri", "redirect_uris must be a list of strings")
    try:
        redirects = check_redirect_uris(raw)
    except OAuthRuleError as exc:
        raise RegistrationError("invalid_redirect_uri", exc.reason) from exc
    method = doc.get("token_endpoint_auth_method", "client_secret_basic")
    if method not in AUTH_METHODS:
        raise RegistrationError(
            "invalid_client_metadata",
            f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}",
        )
    grants = doc.get("grant_types", list(GRANT_TYPES))
    if not isinstance(grants, list) or not set(grants) <= set(GRANT_TYPES):
        raise RegistrationError(
            "invalid_client_metadata", "grant_types may be authorization_code and refresh_token"
        )
    responses = doc.get("response_types", ["code"])
    if responses != ["code"]:
        raise RegistrationError("invalid_client_metadata", "response_types must be [code]")
    scope = doc.get("scope")
    if scope is not None:
        if not isinstance(scope, str):
            raise RegistrationError("invalid_client_metadata", "scope must be a string")
        try:
            scope = " ".join(parse_scopes(scope))
        except OAuthRuleError as exc:
            raise RegistrationError("invalid_client_metadata", exc.reason) from exc
    try:
        name = _optional_text(doc, "client_name", 200)
    except OAuthRuleError as exc:
        raise RegistrationError("invalid_client_metadata", exc.reason) from exc
    return Registration(
        client_name=name,
        client_uri=_https_or_none(doc, "client_uri"),
        logo_uri=_https_or_none(doc, "logo_uri"),
        redirect_uris=redirects,
        token_endpoint_auth_method=str(method),
        scope=scope,
    )


def client_label(client_name: str | None, client_id: str) -> str:
    """How the consent screen, Connected apps and the audit log name an app."""
    if client_name:
        return client_name
    if is_metadata_document_url(client_id):
        return urlsplit(client_id).hostname or client_id
    return "An unnamed app"


def basic_credentials(header: str | None) -> tuple[str, str] | None:
    """`Authorization: Basic` for client_secret_basic (RFC 6749 2.3.1: form-encoded parts)."""
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    client_id, sep, secret = decoded.partition(":")
    if not sep:
        return None
    return unquote_plus(client_id), unquote_plus(secret)
