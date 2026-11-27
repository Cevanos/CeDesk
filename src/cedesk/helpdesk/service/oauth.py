"""The OAuth 2.1 authorization server for AI apps (the MCP connector): clients, the
authorization request, the member's consent, codes, tokens, revocation, and resolving an
access token to the member it acts as.

- A member consents on the web app after signing in the usual way (password, 2FA, SSO,
  the IP list); each member belongs to one workspace, so there is no workspace to pick.
- An access token acts as that member, with their current role, through `Caller` with
  `via_app` set: every tool runs the same checks as the web app, and the audit log says
  "Neha via Claude". Members, roles, security, approvals and campaigns are never reached
  through an app (`NEVER`), whatever the member's role.
- Codes are single use: a second redemption revokes what the first one issued. Refresh
  tokens rotate; a used one presented again revokes its family. A revoked grant, a
  deactivated member or an expired token stops at once (every request checks the database).
"""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.client_documents import DocumentFetch, FetchFailed
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import oauth as rules
from cedesk.helpdesk.domain import security as security_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action, Role
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import oauth as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.errors import (
    Forbidden,
    Invalid,
    IpNotAllowed,
    NotFound,
    TwoFactorSetupRequired,
)

logger = logging.getLogger("cedesk.oauth")

# What an app never does, whatever the member's role: managing people and access,
# approving, exporting every contact, bulk email.
NEVER = frozenset(
    {Action.manage_access, Action.approve, Action.export_contacts, Action.manage_campaigns}
)
# Ended codes and tokens are deleted once they ended this long ago.
ENDED_KEPT = timedelta(days=1)
NOT_FOR_APPS = "Connect AI apps signed in as a person, not through an API key or another app."


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuthServer:
    """This instance's authorization server: from the settings, once per process."""

    issuer: str
    resource: str
    consent_url: str  # the web app's consent page; the signed request goes in `?request=`
    handle_key: str  # signs the request handle


@dataclass(frozen=True)
class Client:
    client_id: str
    registration: str
    client_name: str | None
    client_uri: str | None
    logo_uri: str | None
    redirect_uris: tuple[str, ...]
    auth_method: str
    secret_hash: bytes | None
    scope: str | None

    @property
    def label(self) -> str:
        return rules.client_label(self.client_name, self.client_id)


def _client(row: Mapping[Any, Any]) -> Client:
    secret = row["client_secret_hash"]
    return Client(
        client_id=str(row["client_id"]),
        registration=str(row["registration"]),
        client_name=row["client_name"],
        client_uri=row["client_uri"],
        logo_uri=row["logo_uri"],
        redirect_uris=tuple(row["redirect_uris"]),
        auth_method=str(row["token_endpoint_auth_method"]),
        secret_hash=bytes(secret) if secret is not None else None,
        scope=row["scope"],
    )


class ClientUnknown(Exception):
    """No such client, or its redirect URI isn't one it registered: the browser is told
    directly and never sent anywhere (an open redirect otherwise)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class OAuthError(Exception):
    """An RFC 6749 / 7591 error answer: `error`, a description, and the HTTP status."""

    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status


# --- Clients ---------------------------------------------------------------------


async def find_client(sm: Sessionmaker, client_id: str, fetch: DocumentFetch) -> Client:
    """The client a sign-in names. An https `client_id` is a metadata document, fetched
    (through the egress guard) when it isn't cached or its copy is due again."""
    if rules.is_metadata_document_url(client_id):
        return await _document_client(sm, client_id, fetch)
    async with sm() as s, s.begin():
        row = await repo.get_client(s, client_id)
    if row is None or row["disabled_at"] is not None:
        raise ClientUnknown("This app isn't registered with this CeDesk. Connect it again.")
    return _client(row)


async def _document_client(sm: Sessionmaker, client_id: str, fetch: DocumentFetch) -> Client:
    try:
        rules.check_metadata_document_url(client_id)
    except rules.OAuthRuleError as exc:
        raise ClientUnknown(exc.reason) from exc
    now = _now()
    async with sm() as s, s.begin():
        row = await repo.get_client(s, client_id)
    if row is not None and row["registration"] != "metadata_document":
        raise ClientUnknown("This client_id is registered another way.")
    if row is not None and row["disabled_at"] is not None:
        raise ClientUnknown("This app has been turned off on this CeDesk.")
    if row is not None and row["document_expires_at"] > now:
        return _client(row)
    try:
        fetched = await fetch(client_id)
        document = rules.parse_metadata_document(client_id, json.loads(fetched.body))
    except FetchFailed as exc:
        raise ClientUnknown(str(exc)) from exc
    except (ValueError, rules.OAuthRuleError) as exc:
        reason = exc.reason if isinstance(exc, rules.OAuthRuleError) else "not JSON"
        raise ClientUnknown(f"The app's client metadata document is not valid: {reason}") from exc
    async with sm() as s, s.begin():
        saved = await repo.save_document_client(
            s,
            client_id=client_id,
            client_name=document.client_name,
            client_uri=document.client_uri,
            logo_uri=document.logo_uri,
            redirect_uris=document.redirect_uris,
            expires_at=rules.document_expires_at(now, fetched.cache_control),
        )
    return _client(saved)


@dataclass(frozen=True)
class Registered:
    client: Client
    secret: str | None  # shown once, in the registration answer
    issued_at: datetime


async def register(
    sm: Sessionmaker, doc: object, *, registration: str = "dynamic", client_id: str | None = None
) -> Registered:
    """RFC 7591 dynamic registration; `manual` (the command line) may choose the id."""
    try:
        reg = rules.parse_registration(doc)
    except rules.RegistrationError as exc:
        raise OAuthError(exc.error, exc.reason) from exc
    if client_id is not None and rules.is_metadata_document_url(client_id):
        raise OAuthError("invalid_client_metadata", "an https client_id is a metadata document")
    public = reg.token_endpoint_auth_method == "none"  # noqa: S105  (a method name)
    secret = None if public else rules.new_client_secret()
    async with sm() as s, s.begin():
        row = await repo.insert_registered_client(
            s,
            client_id=client_id or rules.new_client_id(),
            registration=registration,
            client_name=reg.client_name,
            client_uri=reg.client_uri,
            logo_uri=reg.logo_uri,
            redirect_uris=reg.redirect_uris,
            auth_method=reg.token_endpoint_auth_method,
            secret_hash=rules.secret_hash(secret) if secret else None,
            scope=reg.scope,
        )
    return Registered(_client(row), secret, row["created_at"])


async def authenticate_client(
    sm: Sessionmaker, client_id: str | None, secret: str | None
) -> Client:
    """At the token and revocation endpoints. A public client (`none`) sends only its id;
    a metadata-document client must have signed in here first."""
    if not client_id:
        raise OAuthError("invalid_client", "client_id is required", 401)
    async with sm() as s, s.begin():
        row = await repo.get_client(s, client_id)
    if row is None or row["disabled_at"] is not None:
        raise OAuthError("invalid_client", "unknown client", 401)
    client = _client(row)
    if client.auth_method != "none" and not (
        secret and rules.secret_matches(secret, client.secret_hash)
    ):
        raise OAuthError("invalid_client", "client authentication failed", 401)
    return client


# --- The authorization request ---------------------------------------------------


async def start_authorization(
    sm: Sessionmaker, server: AuthServer, params: Mapping[str, str], fetch: DocumentFetch
) -> str:
    """Where to send the browser: the consent page, or the app's redirect URI with an
    error, `state` and `iss` (RFC 9207). Raises `ClientUnknown` when there is nowhere safe."""
    client_id = params.get("client_id") or ""
    if not client_id:
        raise ClientUnknown("client_id is required.")
    client = await find_client(sm, client_id, fetch)
    try:
        request = rules.check_authorization_request(
            params,
            registered_redirects=client.redirect_uris,
            client_scope=client.scope,
            resource=server.resource,
        )
    except rules.NoRedirectError as exc:
        raise ClientUnknown(exc.reason) from exc
    except rules.AuthorizeError as exc:
        redirect, _ = rules.resolve_redirect_uri(client.redirect_uris, params.get("redirect_uri"))
        return _error_redirect(server, redirect, exc.error, exc.reason, params.get("state"))
    handle = rules.seal_request(request, key=server.handle_key, now=_now())
    return rules.with_params(server.consent_url, {"request": handle})


def _error_redirect(
    server: AuthServer, redirect_uri: str, error: str, description: str, state: str | None
) -> str:
    params = {"error": error, "error_description": description}
    if state is not None:
        params["state"] = state
    params["iss"] = server.issuer
    return rules.with_params(redirect_uri, params)


@dataclass(frozen=True)
class ConsentView:
    client: Client
    redirect_uri: str
    redirect_host: str
    scopes: tuple[str, ...]
    resource: str
    workspace: RowMapping
    member: RowMapping


def _person(caller: Caller) -> None:
    if caller.api_key_id is not None or caller.via_app is not None:
        raise Forbidden(NOT_FOR_APPS)


def _open(server: AuthServer, handle: str) -> rules.AuthorizationRequest:
    try:
        return rules.open_request(handle, key=server.handle_key, now=_now())
    except rules.OAuthRuleError as exc:
        raise Invalid(f"{exc.reason[:1].upper()}{exc.reason[1:]}.") from exc


async def _known(sm: Sessionmaker, client_id: str) -> Client:
    async with sm() as s, s.begin():
        row = await repo.get_client(s, client_id)
    if row is None or row["disabled_at"] is not None:
        raise Invalid("This app is no longer known here. Start again from the app.")
    return _client(row)


async def consent_view(
    sm: Sessionmaker, server: AuthServer, caller: Caller, handle: str
) -> ConsentView:
    """What the consent page shows: the app, where the code goes, what it may do, and
    the workspace and member it will act as."""
    _person(caller)
    request = _open(server, handle)
    client = await _known(sm, request.client_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        workspace = await auth_repo.workspace_row(s)
        member = await auth_repo.get_member(s, caller.member_id)
    assert workspace is not None and member is not None
    return ConsentView(
        client=client,
        redirect_uri=request.redirect_uri,
        redirect_host=rules.redirect_host(request.redirect_uri),
        scopes=request.scopes,
        resource=request.resource,
        workspace=workspace,
        member=member,
    )


async def decide(
    sm: Sessionmaker, server: AuthServer, caller: Caller, handle: str, *, approve: bool
) -> str:
    """The member's answer. Returns where the browser goes next: the app's redirect URI
    with a code (or `access_denied`), `state` and `iss`."""
    _person(caller)
    request = _open(server, handle)
    client = await _known(sm, request.client_id)
    if not approve:
        return _error_redirect(
            server,
            request.redirect_uri,
            "access_denied",
            "The member did not allow the connection.",
            request.state,
        )
    code = rules.new_secret(rules.CODE_PREFIX, caller.workspace_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        grant_id = await repo.upsert_grant(
            s,
            workspace_id=caller.workspace_id,
            member_id=caller.member_id,
            client_id=client.client_id,
            resource=request.resource,
            scopes=request.scopes,
            via_sso=await repo.session_via_sso(s, caller.session_id),
        )
        await repo.insert_code(
            s,
            workspace_id=caller.workspace_id,
            grant_id=grant_id,
            code_hash=rules.secret_hash(code),
            redirect_uri=request.redirect_uri,
            redirect_uri_explicit=request.redirect_uri_explicit,
            code_challenge=request.code_challenge,
            scopes=request.scopes,
            resource=request.resource,
            expires_at=_now() + rules.CODE_TTL,
        )
        await audit.record(
            s,
            caller,
            Area.connected_apps,
            "granted",
            client.label,
            target_id=grant_id,
            detail={"client_id": client.client_id, "scopes": list(request.scopes)},
        )
    params = {"code": code}
    if request.state is not None:
        params["state"] = request.state
    params["iss"] = server.issuer
    return rules.with_params(request.redirect_uri, params)


# --- Tokens ----------------------------------------------------------------------


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: tuple[str, ...]


async def _issue(
    s: AsyncSession,
    workspace_id: UUID,
    grant_id: UUID,
    code_id: UUID,
    family_id: UUID,
    scopes: Sequence[str],
) -> Tokens:
    now = _now()
    access = rules.new_secret(rules.ACCESS_PREFIX, workspace_id)
    refresh = rules.new_secret(rules.REFRESH_PREFIX, workspace_id)
    for kind, value, ttl in (
        ("access", access, rules.ACCESS_TTL),
        ("refresh", refresh, rules.REFRESH_TTL),
    ):
        await repo.insert_token(
            s,
            workspace_id=workspace_id,
            grant_id=grant_id,
            code_id=code_id,
            family_id=family_id,
            kind=kind,
            token_hash=rules.secret_hash(value),
            scopes=scopes,
            expires_at=now + ttl,
        )
    return Tokens(access, refresh, int(rules.ACCESS_TTL.total_seconds()), tuple(scopes))


_BAD_CODE = "the authorization code is not valid"


async def exchange_code(
    sm: Sessionmaker,
    client: Client,
    *,
    code: str,
    verifier: str,
    redirect_uri: str | None,
    resource: str | None,
) -> Tokens:
    workspace_id = rules.secret_workspace(rules.CODE_PREFIX, code)
    if workspace_id is None:
        raise OAuthError("invalid_grant", _BAD_CODE)
    failure: OAuthError | None = None
    tokens: Tokens | None = None
    # Reuse revokes in this transaction, which must commit: the error is raised after it.
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.code_for_update(s, rules.secret_hash(code))
        verdict = (
            None
            if row is None
            else rules.code_verdict(_now(), expires_at=row["expires_at"], used_at=row["used_at"])
        )
        if row is None or row["client_id"] != client.client_id:
            failure = OAuthError("invalid_grant", _BAD_CODE)
        elif verdict is rules.CodeVerdict.reused:
            await repo.revoke_code_tokens(s, row["id"])
            logger.warning("authorization code presented twice; its tokens were revoked")
            failure = OAuthError("invalid_grant", "the authorization code was already used")
        elif verdict is rules.CodeVerdict.expired:
            failure = OAuthError("invalid_grant", "the authorization code has expired")
        elif row["grant_revoked_at"] is not None or not row["is_active"]:
            failure = OAuthError("invalid_grant", "this connection was revoked")
        elif (row["redirect_uri_explicit"] or redirect_uri is not None) and (
            redirect_uri != row["redirect_uri"]
        ):
            failure = OAuthError("invalid_grant", "redirect_uri does not match the authorization")
        elif not rules.pkce_matches(verifier, row["code_challenge"]):
            failure = OAuthError("invalid_grant", "code_verifier does not match")
        elif resource is not None and not rules.resource_matches(row["resource"], resource):
            failure = OAuthError("invalid_target", "resource is not the one this code is for")
        else:
            await repo.mark_code_used(s, row["id"])
            tokens = await _issue(
                s, workspace_id, row["grant_id"], row["id"], uuid4(), tuple(row["scopes"])
            )
    if failure is not None:
        raise failure
    assert tokens is not None
    return tokens


async def refresh(
    sm: Sessionmaker,
    client: Client,
    *,
    refresh_token: str,
    scope: str | None,
    resource: str | None,
) -> Tokens:
    workspace_id = rules.secret_workspace(rules.REFRESH_PREFIX, refresh_token)
    bad = OAuthError("invalid_grant", "the refresh token is not valid")
    if workspace_id is None:
        raise bad
    failure: OAuthError | None = None
    tokens: Tokens | None = None
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.token_for_update(s, rules.secret_hash(refresh_token))
        if row is None or row["kind"] != "refresh" or row["client_id"] != client.client_id:
            failure = bad
        else:
            verdict = rules.refresh_verdict(
                _now(),
                expires_at=row["expires_at"],
                used_at=row["used_at"],
                revoked_at=row["revoked_at"],
                grant_revoked_at=row["grant_revoked_at"],
            )
            have = tuple(row["scopes"])
            if verdict is rules.RefreshVerdict.reused:
                await repo.revoke_family(s, row["family_id"])
                logger.warning("a rotated refresh token came back; its family was revoked")
                failure = OAuthError(
                    "invalid_grant", "the refresh token was already used; connect the app again"
                )
            elif verdict is not rules.RefreshVerdict.usable or not row["is_active"]:
                failure = OAuthError(
                    "invalid_grant", "the refresh token has expired or was revoked"
                )
            elif resource is not None and not rules.resource_matches(row["resource"], resource):
                failure = OAuthError("invalid_target", "resource is not the one this token is for")
            else:
                try:
                    wanted = rules.parse_scopes(scope) if scope else have
                except rules.OAuthRuleError as exc:
                    wanted, failure = have, OAuthError("invalid_scope", exc.reason)
                if failure is None and not set(wanted) <= set(have):
                    failure = OAuthError("invalid_scope", "scope exceeds what was granted")
                if failure is None:
                    await repo.mark_token_used(s, row["id"])
                    tokens = await _issue(
                        s, workspace_id, row["grant_id"], row["code_id"], row["family_id"], wanted
                    )
    if failure is not None:
        raise failure
    assert tokens is not None
    return tokens


async def revoke_token(sm: Sessionmaker, client: Client, token: str) -> None:
    """RFC 7009: ends the token's whole family. Unknown or someone else's: nothing, and
    the answer is the same."""
    workspace_id = rules.secret_workspace(rules.ACCESS_PREFIX, token) or rules.secret_workspace(
        rules.REFRESH_PREFIX, token
    )
    if workspace_id is None:
        return
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.token_for_update(s, rules.secret_hash(token))
        if row is not None and row["client_id"] == client.client_id:
            await repo.revoke_family(s, row["family_id"])


# --- The member behind an access token ---------------------------------------------


@dataclass(frozen=True)
class AppAccess:
    """A live access token: who it acts as and for which app. Checked per request."""

    workspace_id: UUID
    member_id: UUID
    grant_id: UUID
    client_id: str
    app_name: str
    scopes: tuple[str, ...]
    resource: str
    expires_at: datetime
    row: Mapping[str, Any]


async def verify_access(sm: Sessionmaker, token: str) -> AppAccess | None:
    """None for anything but a live access token of an active member under a live grant."""
    workspace_id = rules.secret_workspace(rules.ACCESS_PREFIX, token)
    if workspace_id is None:
        return None
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.live_access(s, rules.secret_hash(token))
        if row is None:
            return None
        await repo.touch_grant(s, row["grant_id"])
    granted = set(row["grant_scopes"])
    return AppAccess(
        workspace_id=workspace_id,
        member_id=row["member_id"],
        grant_id=row["grant_id"],
        client_id=str(row["client_id"]),
        app_name=rules.client_label(row["client_name"], str(row["client_id"])),
        scopes=tuple(sc for sc in row["scopes"] if sc in granted),
        resource=str(row["resource"]),
        expires_at=row["expires_at"],
        row=dict(row),
    )


def app_caller(access: AppAccess, ip: str | None) -> Caller:
    """The `Caller` tools run as: the member's current role without `NEVER`, refused from
    outside the workspace's IP list, or without the 2FA the workspace now requires."""
    row = access.row
    networks = security_rules.parse_networks(row["ip_allowlist"] or ())
    if not security_rules.ip_allowed(ip, networks):
        raise IpNotAllowed(security_rules.ip_refused(ip))
    if row["require_2fa"] and not row["two_factor_on"] and not row["via_sso"]:
        raise TwoFactorSetupRequired(
            "Your workspace requires two-step sign-in. Set it up in CeDesk, then connect "
            "the app again."
        )
    member = member_access(row)
    return Caller(
        access.workspace_id,
        access.member_id,
        Role(row["role"]),
        access.grant_id,
        replace(member, actions=member.actions - NEVER),
        ip=ip,
        via_app=access.app_name,
        grant_id=access.grant_id,
    )


# --- Connected apps (the member's own list) -----------------------------------------


async def my_grants(sm: Sessionmaker, caller: Caller) -> Sequence[RowMapping]:
    _person(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.live_grants(s, caller.member_id)


async def revoke_grant(sm: Sessionmaker, caller: Caller, grant_id: UUID) -> None:
    _person(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.revoke_grant(s, grant_id, caller.member_id)
        if row is None:
            raise NotFound("No such connected app.")
        await audit.record(
            s,
            caller,
            Area.connected_apps,
            "revoked",
            rules.client_label(row["client_name"], str(row["client_id"])),
            target_id=grant_id,
            detail={"client_id": str(row["client_id"])},
        )


async def sweep_everywhere(sm: Sessionmaker) -> int:
    """Deletes codes and tokens that ended over a day ago, in every workspace (the worker)."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    before = _now() - ENDED_KEPT
    removed = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            removed += await repo.sweep(s, before)
    return removed
