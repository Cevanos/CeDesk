"""Single sign-on with SAML (md S1): Admin settings, and the browser flow (start, the
identity provider's answer at the ACS URL, our metadata)."""

from datetime import datetime
from typing import Annotated
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, StringConstraints

from cedesk import saml
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, client_ip
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.service import sso
from cedesk.helpdesk.service.errors import Invalid, ServiceError
from cedesk.helpdesk.service.sso import SsoIn
from cedesk.settings import Settings

router = APIRouter(tags=["single sign-on"])

Slug = Annotated[str, Path(max_length=40)]
MAX_ANSWER_BYTES = 512 * 1024
BROWSER_COOKIE = "cedesk_sso"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _api_base(request: Request) -> str:
    return _settings(request).api_url.strip() or str(request.base_url).rstrip("/")


class SsoBody(Body):
    enabled: bool = False
    enforced: bool = Field(
        default=False,
        description="Password sign-in is refused (403 `SsoRequired`) for everyone except "
        "Admins, who keep it so a broken setup can be fixed.",
    )
    idp_entity_id: Annotated[str, StringConstraints(max_length=1024)] | None = None
    idp_sso_url: Annotated[str, StringConstraints(max_length=2048)] | None = Field(
        default=None, description="The IdP's SAML sign-in URL (https)."
    )
    idp_cert: Annotated[str, StringConstraints(max_length=20000)] | None = Field(
        default=None, description="The IdP's signing certificate (PEM)."
    )


class SsoOut(BaseModel):
    enabled: bool
    enforced: bool
    idp_entity_id: str | None
    idp_sso_url: str | None
    idp_cert: str | None
    sp_entity_id: str = Field(description="Give these three to the identity provider.")
    acs_url: str
    metadata_url: str
    updated_at: datetime | None = None
    updated_by: UUID | None = None
    updated_by_name: str | None = None


class SsoStatusOut(BaseModel):
    enabled: bool = Field(description='Show "Sign in with SSO".')
    enforced: bool = Field(description="Hide the password form (Admins can still use it).")
    start_url: str | None = Field(description="Open this in the browser to sign in.")


async def _out(request: Request, caller_slug: str, row: object) -> SsoOut:
    sp = sso.sp_for(_api_base(request), caller_slug)
    found = dict(row) if row is not None else {}  # type: ignore[call-overload]
    return SsoOut(
        enabled=bool(found.get("enabled")),
        enforced=bool(found.get("enforced")),
        idp_entity_id=found.get("idp_entity_id"),
        idp_sso_url=found.get("idp_sso_url"),
        idp_cert=found.get("idp_cert"),
        sp_entity_id=sp.entity_id,
        acs_url=sp.acs_url,
        metadata_url=sp.entity_id,
        updated_at=found.get("updated_at"),
        updated_by=found.get("updated_by"),
        updated_by_name=found.get("updated_by_name"),
    )


@router.get("/sso-settings", response_model=SsoOut, summary="Single sign-on settings (Admin)")
async def get_settings(request: Request, caller: CallerDep, sm: SessionmakerDep) -> SsoOut:
    row = await sso.get_settings(sm, caller)
    return await _out(request, await sso.workspace_slug(sm, caller), row)


@router.put("/sso-settings", response_model=SsoOut, summary="Save single sign-on (Admin)")
async def save_settings(
    body: SsoBody, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> SsoOut:
    row = await sso.save_settings(sm, caller, SsoIn(**body.model_dump()))
    return await _out(request, await sso.workspace_slug(sm, caller), row)


@router.get("/sso/{workspace}", response_model=SsoStatusOut, summary="Does this workspace use SSO?")
async def sso_status(workspace: Slug, request: Request, sm: SessionmakerDep) -> SsoStatusOut:
    enabled, enforced = await sso.status(sm, workspace)
    start = f"{_api_base(request)}/api/v1/sso/{workspace}/start" if enabled else None
    return SsoStatusOut(enabled=enabled, enforced=enforced, start_url=start)


@router.get(
    "/sso/{workspace}/start",
    status_code=302,
    response_class=RedirectResponse,
    summary="Start signing in: redirects to the identity provider",
    description="`next`: the app page to open after signing in (a path like /tickets).",
)
async def start(
    workspace: Slug,
    request: Request,
    sm: SessionmakerDep,
    next: Annotated[str | None, Query(max_length=500)] = None,
) -> RedirectResponse:
    api_base = _api_base(request)
    started = await sso.start(sm, slug=workspace, api_base=api_base, next_path=next)
    response = RedirectResponse(started.url, status_code=302)
    # The IdP posts back from its own site, so the cookie must be SameSite=None (and so
    # Secure); it is sent only to this workspace's ACS URL and lives 10 minutes.
    https = api_base.startswith("https://")
    response.set_cookie(
        BROWSER_COOKIE,
        started.browser,
        max_age=600,
        path=f"/api/v1/sso/{workspace}/acs",
        httponly=True,
        secure=https,
        samesite="none" if https else "lax",
    )
    return response


@router.post(
    "/sso/{workspace}/acs",
    status_code=303,
    response_class=RedirectResponse,
    summary="The identity provider's answer (ACS URL)",
    description="Redirects to `<app_url>/sso/callback#token=...&expires_at=...&next=...`, "
    "or `#error=...` when the answer is refused. The token is in the fragment, so it never "
    "reaches a server log.",
)
async def acs(workspace: Slug, request: Request, sm: SessionmakerDep) -> RedirectResponse:
    callback = _settings(request).app_url.rstrip("/") + "/sso/callback#"
    try:
        body = await request.body()
        if len(body) > MAX_ANSWER_BYTES:
            raise Invalid("The answer is too large.")
        form = parse_qs(body.decode("ascii", "replace"), max_num_fields=10)
        answer = (form.get("SAMLResponse") or [""])[0]
        if not answer:
            raise Invalid("No SAMLResponse was posted.")
        signed = await sso.finish(
            sm,
            slug=workspace,
            api_base=_api_base(request),
            saml_response=answer,
            relay_state=next(iter(form.get("RelayState") or []), None),
            ip=client_ip(request) or "",
            browser=request.cookies.get(BROWSER_COOKIE),
        )
    except (ServiceError, ValueError) as exc:
        message = exc.message if isinstance(exc, ServiceError) else "That answer can't be read."
        return RedirectResponse(callback + urlencode({"error": message}), status_code=303)
    fragment = urlencode(
        {
            "token": signed.token,
            "expires_at": signed.expires_at.isoformat(),
            "next": signed.next_path,
        }
    )
    response = RedirectResponse(callback + fragment, status_code=303)
    response.delete_cookie(BROWSER_COOKIE, path=f"/api/v1/sso/{workspace}/acs")
    return response


@router.get(
    "/sso/{workspace}/metadata",
    response_class=Response,
    summary="Our SAML service provider metadata (for the IdP's setup screen)",
    responses={200: {"content": {"application/samlmetadata+xml": {}}}},
)
async def metadata(workspace: Slug, request: Request) -> Response:
    try:
        xml = saml.metadata(sso.sp_for(_api_base(request), workspace.strip().lower()))
    except saml.SamlError as exc:
        raise Invalid(str(exc)) from exc
    return Response(xml, media_type="application/samlmetadata+xml")
