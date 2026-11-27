"""AI apps (the MCP connector): the OAuth 2.1 authorization server they sign in with, and
the web app's consent and Connected apps screens.

`public` is served at the app root, outside /api/v1, because OAuth clients find it from
the issuer: `/.well-known/oauth-authorization-server`, `/oauth/authorize`, `/oauth/token`,
`/oauth/register`, `/oauth/revoke` and `/.well-known/openai-apps-challenge`. Their answers
are OAuth's own shapes (RFC 6749, 7591, 7009, 8414), not CeDesk's error shape. Every
authorization redirect carries `iss` (RFC 9207), errors included. `router` (under /api/v1)
is what the web app calls for the signed-in member.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from mcp.shared.auth import OAuthMetadata
from pydantic import BaseModel, Field

from cedesk.client_documents import DocumentFetch
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep, client_ip
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import oauth as rules
from cedesk.helpdesk.service import oauth, rate_limits
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import NotFound, TooManyAttempts
from cedesk.helpdesk.service.oauth import AuthServer, ClientUnknown, OAuthError, Tokens
from cedesk.settings import Settings

public = APIRouter(tags=["ai apps (oauth)"])
router = APIRouter(tags=["connected apps"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def auth_server(settings: Settings) -> AuthServer:
    issuer = settings.issuer()
    return AuthServer(
        issuer=issuer,
        resource=settings.mcp_resource(),
        consent_url=f"{settings.app_url.rstrip('/')}/connect",
        # Its own key, derived from the secret key: a handle can't pass for anything else.
        handle_key=f"oauth-request:{settings.secret_key.get_secret_value()}",
    )


def _server(request: Request) -> AuthServer:
    server: AuthServer = request.app.state.oauth_server
    return server


def _fetch(request: Request) -> DocumentFetch:
    fetch: DocumentFetch = request.app.state.oauth_fetch
    return fetch


def _oauth_error(exc: OAuthError, *, basic: bool = False) -> JSONResponse:
    headers = dict(_NO_STORE)
    if exc.status == 401 and basic:
        headers["WWW-Authenticate"] = 'Basic realm="CeDesk"'
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description},
        status_code=exc.status,
        headers=headers,
    )


async def _limit(
    request: Request, sm: Sessionmaker, bucket: str, limit: int, window_seconds: int
) -> None:
    key = client_ip(request) or "unknown"
    wait = await rate_limits.hit(sm, bucket, key, limit=limit, window_seconds=window_seconds)
    if wait is not None:
        raise TooManyAttempts(f"Too many requests from this address. Try again in {wait} s.", wait)


def _text(values: Mapping[str, Any]) -> dict[str, str]:
    """Form or query values; a repeated or file field counts as absent (RFC 6749 3.1)."""
    return {k: v for k, v in values.items() if isinstance(v, str)}


# --- Discovery -------------------------------------------------------------------


def metadata(server: AuthServer) -> dict[str, Any]:
    base = server.issuer
    doc = OAuthMetadata.model_validate(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "scopes_supported": list(rules.SCOPES),
            "response_types_supported": ["code"],
            "grant_types_supported": list(rules.GRANT_TYPES),
            "token_endpoint_auth_methods_supported": list(rules.AUTH_METHODS),
            "revocation_endpoint_auth_methods_supported": list(rules.AUTH_METHODS),
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }
    )
    out = doc.model_dump(mode="json", exclude_none=True)
    out["issuer"] = base  # exactly as configured: clients compare it as a string
    return out


@public.get(
    "/.well-known/oauth-authorization-server",
    summary="OAuth authorization server metadata (RFC 8414) for AI apps",
)
async def authorization_server_metadata(request: Request) -> JSONResponse:
    return JSONResponse(metadata(_server(request)), headers={"Cache-Control": "max-age=3600"})


@public.get(
    "/.well-known/openai-apps-challenge",
    response_class=PlainTextResponse,
    summary="OpenAI's domain challenge for the CeDesk app (404 when not set)",
)
async def openai_apps_challenge(settings: SettingsDep) -> PlainTextResponse:
    token = settings.openai_apps_challenge
    if token is None:
        raise NotFound("Not found.")
    return PlainTextResponse(token.get_secret_value(), media_type="text/plain")


# --- Authorization ---------------------------------------------------------------


_AUTHORIZE_HELP = (
    "OAuth 2.1 authorization code with PKCE (S256 only). `client_id` is a registered client "
    "or the https URL of a Client ID Metadata Document. The member signs in to CeDesk if "
    "needed and approves on `<app_url>/connect`. An unknown client or redirect URI is a plain "
    "400 page, never a redirect; other errors go back to the app with `error`, `state` and "
    "`iss`."
)
_AUTHORIZE_RESPONSES: dict[int | str, dict[str, Any]] = {
    302: {"description": "On to the consent page, or back to the app with an error"},
    400: {"description": "Unknown client or redirect URI"},
}


async def _authorize(
    request: Request, sm: Sessionmaker, settings: Settings, params: dict[str, str]
) -> Response:
    await _limit(request, sm, "oauth", settings.oauth_requests_per_minute, 60)
    try:
        url = await oauth.start_authorization(sm, _server(request), params, _fetch(request))
    except ClientUnknown as exc:
        return PlainTextResponse(
            f"This sign-in can't continue: {exc.reason}", status_code=400, headers=_NO_STORE
        )
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


@public.get(
    "/oauth/authorize",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Start an AI app's sign-in",
    description=_AUTHORIZE_HELP,
    responses=_AUTHORIZE_RESPONSES,
)
async def authorize(request: Request, sm: SessionmakerDep, settings: SettingsDep) -> Response:
    return await _authorize(request, sm, settings, _text(request.query_params))


@public.post(
    "/oauth/authorize",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Start an AI app's sign-in (form post)",
    description=_AUTHORIZE_HELP,
    responses=_AUTHORIZE_RESPONSES,
)
async def authorize_form(request: Request, sm: SessionmakerDep, settings: SettingsDep) -> Response:
    return await _authorize(request, sm, settings, _text(await request.form()))


# --- Tokens ----------------------------------------------------------------------


def _token_answer(tokens: Tokens) -> JSONResponse:
    return JSONResponse(
        {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,
            "scope": " ".join(tokens.scopes),
        },
        headers=_NO_STORE,
    )


async def _client_from(request: Request, sm: Sessionmaker, form: Mapping[str, str]) -> oauth.Client:
    basic = rules.basic_credentials(request.headers.get("authorization"))
    if basic is not None:
        return await oauth.authenticate_client(sm, basic[0], basic[1])
    return await oauth.authenticate_client(sm, form.get("client_id"), form.get("client_secret"))


@public.post(
    "/oauth/token",
    summary="Exchange a code, or refresh (form-encoded, RFC 6749)",
    description="`grant_type=authorization_code` (with `code`, `code_verifier`, "
    "`redirect_uri`) or `refresh_token`. Refresh tokens rotate: a used one presented again "
    "revokes every token from that sign-in. Access tokens last an hour, refresh tokens 30 days.",
)
async def token(request: Request, sm: SessionmakerDep, settings: SettingsDep) -> JSONResponse:
    await _limit(request, sm, "oauth", settings.oauth_requests_per_minute, 60)
    form = _text(await request.form())
    basic = request.headers.get("authorization", "").lower().startswith("basic ")
    try:
        client = await _client_from(request, sm, form)
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            if not form.get("code") or not form.get("code_verifier"):
                raise OAuthError("invalid_request", "code and code_verifier are required")
            tokens = await oauth.exchange_code(
                sm,
                client,
                code=form["code"],
                verifier=form["code_verifier"],
                redirect_uri=form.get("redirect_uri"),
                resource=form.get("resource"),
            )
        elif grant_type == "refresh_token":
            if not form.get("refresh_token"):
                raise OAuthError("invalid_request", "refresh_token is required")
            tokens = await oauth.refresh(
                sm,
                client,
                refresh_token=form["refresh_token"],
                scope=form.get("scope"),
                resource=form.get("resource"),
            )
        else:
            raise OAuthError(
                "unsupported_grant_type", "grant_type must be authorization_code or refresh_token"
            )
    except OAuthError as exc:
        return _oauth_error(exc, basic=basic)
    return _token_answer(tokens)


@public.post(
    "/oauth/revoke",
    summary="Revoke an access or refresh token (RFC 7009; always 200)",
)
async def revoke(request: Request, sm: SessionmakerDep, settings: SettingsDep) -> Response:
    await _limit(request, sm, "oauth", settings.oauth_requests_per_minute, 60)
    form = _text(await request.form())
    basic = request.headers.get("authorization", "").lower().startswith("basic ")
    try:
        client = await _client_from(request, sm, form)
    except OAuthError as exc:
        return _oauth_error(exc, basic=basic)
    if form.get("token"):
        await oauth.revoke_token(sm, client, form["token"])
    return Response(status_code=200, headers=_NO_STORE)


@public.post(
    "/oauth/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register an AI app (RFC 7591 dynamic client registration)",
    description="Redirect URIs: https anywhere, or http on 127.0.0.1, [::1] or localhost "
    "(the port may then change at sign-in). A public client (`none`) gets no secret; others "
    "get one, shown only here.",
)
async def register(request: Request, sm: SessionmakerDep, settings: SettingsDep) -> JSONResponse:
    await _limit(request, sm, "oauth_register", settings.oauth_registrations_per_hour, 3600)
    try:
        doc = await request.json()
    except ValueError:
        return _oauth_error(OAuthError("invalid_client_metadata", "send a JSON object"))
    try:
        made = await oauth.register(sm, doc)
    except OAuthError as exc:
        return _oauth_error(exc)
    c = made.client
    body: dict[str, Any] = {
        "client_id": c.client_id,
        "client_id_issued_at": int(made.issued_at.timestamp()),
        "redirect_uris": list(c.redirect_uris),
        "token_endpoint_auth_method": c.auth_method,
        "grant_types": list(rules.GRANT_TYPES),
        "response_types": ["code"],
    }
    for key, value in (
        ("client_name", c.client_name),
        ("client_uri", c.client_uri),
        ("logo_uri", c.logo_uri),
        ("scope", c.scope),
    ):
        if value is not None:
            body[key] = value
    if made.secret is not None:
        body["client_secret"] = made.secret
        body["client_secret_expires_at"] = 0
    return JSONResponse(body, status_code=201, headers=_NO_STORE)


# --- The web app: consent and Connected apps ----------------------------------------


class ConnectedAppOut(BaseModel):
    client_id: str
    name: str = Field(description="The app's own name, or its domain when it gave none.")
    client_uri: str | None
    logo_uri: str | None
    registration: Literal["dynamic", "metadata_document", "manual"]
    domain: str | None = Field(description="The host of client_uri, or of a metadata URL.")


class ConsentWorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str | None


class ConsentMemberOut(BaseModel):
    id: UUID
    name: str
    email: str


class ConsentOut(BaseModel):
    app: ConnectedAppOut
    redirect_host: str = Field(description="Where the app's sign-in code will be sent.")
    scopes: list[str] = Field(description="read: look things up. write: make changes.")
    resource: str
    workspace: ConsentWorkspaceOut
    member: ConsentMemberOut


class ConsentIn(Body):
    request: str = Field(max_length=8000, description="`request` from the consent page's URL.")
    decision: Literal["approve", "deny"]


class ConsentDone(BaseModel):
    redirect_to: str = Field(description="Send the browser here: back to the app.")


class ConnectedAppGrantOut(BaseModel):
    id: UUID
    app: ConnectedAppOut
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None


def _domain(client_uri: str | None, client_id: str) -> str | None:
    for value in (client_uri, client_id if rules.is_metadata_document_url(client_id) else None):
        if value:
            host = urlsplit(value).hostname
            if host:
                return host
    return None


def _app(row: Mapping[Any, Any] | oauth.Client) -> ConnectedAppOut:
    if isinstance(row, oauth.Client):
        cid, name, uri, logo, reg = (
            row.client_id, row.client_name, row.client_uri, row.logo_uri, row.registration,
        )  # fmt: skip
    else:
        cid, name, uri, logo, reg = (
            str(row["client_id"]), row["client_name"], row["client_uri"], row["logo_uri"],
            str(row["registration"]),
        )  # fmt: skip
    return ConnectedAppOut(
        client_id=cid,
        name=rules.client_label(name, cid),
        client_uri=uri,
        logo_uri=logo,
        registration=reg,
        domain=_domain(uri, cid),
    )


RequestQuery = Annotated[
    str, Query(alias="request", max_length=8000, description="From the consent page's URL.")
]


@router.get(
    "/oauth/consent",
    response_model=ConsentOut,
    summary="What an AI app is asking for, for the signed-in member to approve",
)
async def consent(
    request: Request, caller: CallerDep, sm: SessionmakerDep, request_: RequestQuery
) -> ConsentOut:
    view = await oauth.consent_view(sm, _server(request), caller, request_)
    return ConsentOut(
        app=_app(view.client),
        redirect_host=view.redirect_host,
        scopes=list(view.scopes),
        resource=view.resource,
        workspace=ConsentWorkspaceOut(
            id=view.workspace["id"], name=view.workspace["name"], slug=view.workspace["slug"]
        ),
        member=ConsentMemberOut(
            id=view.member["id"], name=view.member["name"], email=view.member["email"]
        ),
    )


@router.post(
    "/oauth/consent",
    response_model=ConsentDone,
    summary="Approve or deny an AI app; answers where to send the browser",
)
async def decide(
    request: Request, body: ConsentIn, caller: CallerDep, sm: SessionmakerDep
) -> ConsentDone:
    url = await oauth.decide(
        sm, _server(request), caller, body.request, approve=body.decision == "approve"
    )
    return ConsentDone(redirect_to=url)


@router.get(
    "/oauth/grants",
    response_model=list[ConnectedAppGrantOut],
    summary="The AI apps you connected to this workspace",
)
async def list_grants(caller: CallerDep, sm: SessionmakerDep) -> list[ConnectedAppGrantOut]:
    return [
        ConnectedAppGrantOut(
            id=r["id"],
            app=_app(r),
            scopes=list(r["scopes"]),
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
        )
        for r in await oauth.my_grants(sm, caller)
    ]


@router.delete(
    "/oauth/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect an AI app: its tokens stop working at once",
)
async def revoke_grant(grant_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await oauth.revoke_grant(sm, caller, grant_id)
