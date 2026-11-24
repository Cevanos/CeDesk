"""Custom domain for the help center (feature-gap plan 4.2). Admin (Settings) registers and
checks the domain; the public host lookup is for the frontend, and the TLS check is for Caddy
(it exists only when `CEDESK_TLS_ASK_KEY` is set). Nothing here serves the pages."""

import hmac
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import (
    CallerDep,
    DnsDep,
    PublicLimit,
    SessionmakerDep,
    SettingsDep,
    within,
)
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import help_domain as rules
from cedesk.helpdesk.service import help_domain as service
from cedesk.helpdesk.service.errors import NotFound
from cedesk.settings import Settings

router = APIRouter(tags=["help center"])
internal_router = APIRouter(tags=["help center"], include_in_schema=False)

_ADMIN: dict[int | str, dict[str, Any]] = {403: {"description": "Admins only"}}


class DnsStep(BaseModel):
    type: str = Field(description="TXT, or CNAME.")
    name: str
    value: str


class HelpDomainOut(BaseModel):
    domain: str
    status: str = Field(
        description="`pending` until the TXT record is found, then `verified`. Only a "
        "verified domain is served."
    )
    verified_at: datetime | None
    last_checked_at: datetime | None
    failed_checks: int = Field(
        description="Checks in a row that did not find the record. A verified domain goes "
        "back to pending after 3."
    )
    last_error: str | None
    dns: list[DnsStep] = Field(description="The records to add at the domain's DNS host.")


class HelpDomainIn(Body):
    domain: Annotated[str, StringConstraints(min_length=1, max_length=300)] = Field(
        description="A domain you own, e.g. help.example.com (no https://, port or path)."
    )


def _reserved(settings: Settings) -> set[str]:
    return rules.hosts_of(
        [settings.app_url, settings.portal_url, settings.api_url, f"//{settings.help_cname_target}"]
    )


def _out(row: Any, settings: Settings) -> HelpDomainOut:
    domain = str(row["domain"])
    steps = [DnsStep(type="TXT", name=rules.txt_name(domain),
                     value=rules.txt_value(str(row["verify_token"])))]  # fmt: skip
    if settings.help_cname_target:
        steps.append(DnsStep(type="CNAME", name=domain, value=settings.help_cname_target))
    return HelpDomainOut(**{k: row[k] for k in (
        "domain", "status", "verified_at", "last_checked_at", "failed_checks", "last_error")},
        dns=steps)  # fmt: skip


@router.get(
    "/help-center/domain",
    response_model=HelpDomainOut | None,
    summary="The help center's custom domain, or null (Admin)",
    responses=_ADMIN,
)
async def get_domain(
    caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep
) -> HelpDomainOut | None:
    row = await service.get_domain(sm, caller)
    return _out(row, settings) if row else None


@router.put(
    "/help-center/domain",
    response_model=HelpDomainOut,
    summary="Register the help center's domain (Admin)",
    description="Starts as `pending` with a TXT record to publish; replaces an earlier "
    "domain (and its proof). Sending the same domain again changes nothing.",
    responses={**_ADMIN, 422: {"description": "Not a domain, or one of the platform's own"}},
)
async def put_domain(
    body: HelpDomainIn, caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep
) -> HelpDomainOut:
    row = await service.set_domain(sm, caller, body.domain, reserved=_reserved(settings))
    return _out(row, settings)


@router.delete(
    "/help-center/domain",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the help center's domain (Admin)",
    responses={**_ADMIN, 404: {"description": "No domain"}},
)
async def delete_domain(caller: CallerDep, sm: SessionmakerDep) -> None:
    await service.remove_domain(sm, caller)


@router.post(
    "/help-center/domain/check",
    response_model=HelpDomainOut,
    summary="Read the TXT record now (Admin)",
    description="Verifies the domain when the record holds the token. A verified domain "
    "stays verified until it has failed 3 checks in a row (the worker checks daily).",
    responses={**_ADMIN, 404: {"description": "No domain"},
               409: {"description": "Another workspace has already proven that domain"}},
)  # fmt: skip
async def check_domain(
    caller: CallerDep, sm: SessionmakerDep, dns: DnsDep, settings: SettingsDep
) -> HelpDomainOut:
    return _out(await service.check_domain(sm, caller, dns), settings)


class HostOut(BaseModel):
    workspace: str = Field(
        description="The workspace's slug: the help center is at `/<slug>/help`."
    )
    default_locale: str


@router.get(
    "/public-hosts/{host}",
    response_model=HostOut,
    summary="Which workspace's help center is served on this host (no sign-in)",
    description="For the frontend: it maps the `Host` of a request to a workspace. Only a "
    "verified domain answers; anything else is 404.",
    responses={404: {"description": "No help center on that host"},
               429: {"description": "Too many requests from this IP"}},
    dependencies=[PublicLimit],
)  # fmt: skip
async def host_workspace(host: str, sm: SessionmakerDep, response: Response) -> HostOut:
    found = await service.host_workspace(sm, host)
    response.headers["Cache-Control"] = "public, max-age=60"  # the frontend calls it per page
    return HostOut(workspace=found.workspace, default_locale=found.default_locale)


@internal_router.get("/internal/tls-ask")
async def tls_ask(
    request: Request,
    sm: SessionmakerDep,
    settings: SettingsDep,
    domain: Annotated[str, Query(max_length=300)] = "",
    key: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    """Caddy's on-demand TLS `ask`: 200 for a verified domain, 404 for anything else. Needs
    the key from `CEDESK_TLS_ASK_KEY`; a wrong one counts against the caller's own budget
    (never the sign-in one)."""
    secret = settings.tls_ask_key.get_secret_value() if settings.tls_ask_key else ""
    if not secret or not hmac.compare_digest(key.encode(), secret.encode()):
        ip = request.client.host if request.client else "unknown"
        within(request.app.state.tls_attempts, ip, "attempts")
        raise NotFound("Not found.")
    if not await service.tls_allowed(sm, domain):
        raise NotFound("Not found.")
    return Response(status_code=status.HTTP_200_OK)
