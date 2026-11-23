"""Request dependencies. Authenticated routes take `CallerDep`; none reads auth headers."""

from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cedesk import logs
from cedesk.crypto import Sealer
from cedesk.dnscheck import TxtLookup
from cedesk.helpdesk.domain import api_keys as api_key_rules
from cedesk.helpdesk.domain import idempotency as idempotency_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.service import api_keys, auth, portal
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import (
    Forbidden,
    Invalid,
    NotSignedIn,
    PasswordExpired,
    TooManyAttempts,
    TwoFactorRequired,
    TwoFactorSetupRequired,
)
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.ratelimit import SlidingWindowLimiter
from cedesk.settings import Settings
from cedesk.storage import ObjectStore

_bearer = HTTPBearer(
    auto_error=False,
    description="The token from POST /api/v1/auth/sign-in, or an API key (`cdk_...`).",
)


def get_sessionmaker(request: Request) -> Sessionmaker:
    sm: Sessionmaker = request.app.state.sessionmaker
    return sm


SessionmakerDep = Annotated[Sessionmaker, Depends(get_sessionmaker)]


def get_store(request: Request) -> ObjectStore:
    store: ObjectStore = request.app.state.store
    return store


StoreDep = Annotated[ObjectStore, Depends(get_store)]


def get_dns(request: Request) -> TxtLookup:
    lookup: TxtLookup = request.app.state.dns
    return lookup


DnsDep = Annotated[TxtLookup, Depends(get_dns)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str | None:
    """The caller's address. Behind the proxy, uvicorn's --proxy-headers puts the
    X-Forwarded-For address here (the API port is only reachable through the proxy)."""
    return request.client.host if request.client else None


# With an expired password (S3) a member may only see who they are, change the password
# or sign out. Waiting for the 2FA code, only the code or sign-out; without the 2FA the
# workspace requires, only setting it up (S2).
_SIGN_OUT = "/api/v1/auth/sign-out"
_EXPIRED_PASSWORD_ALLOWS = frozenset({"/api/v1/me", "/api/v1/me/password", _SIGN_OUT})
_PENDING_CODE_ALLOWS = frozenset({"/api/v1/auth/2fa", _SIGN_OUT})
_SETUP_2FA_ALLOWS = frozenset(
    {"/api/v1/me", "/api/v1/me/2fa", "/api/v1/me/2fa/setup", "/api/v1/me/2fa/confirm", _SIGN_OUT}
)


async def get_caller(
    request: Request,
    sm: SessionmakerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Caller:
    if credentials is None or not credentials.credentials:
        raise NotSignedIn("Sign in first.")
    ip = client_ip(request)
    token = credentials.credentials.strip()
    if api_key_rules.is_key(token):
        return await _key_caller(request, sm, token, ip)
    caller = replace(await auth.resolve(sm, token, ip or ""), ip=ip)
    logs.bind(workspace_id=caller.workspace_id, member_id=caller.member_id)
    path = request.url.path
    if caller.second_factor_pending:
        if path not in _PENDING_CODE_ALLOWS:
            raise TwoFactorRequired("Enter the code from your authenticator app first.")
    elif caller.two_factor_setup_required and path not in _SETUP_2FA_ALLOWS:
        raise TwoFactorSetupRequired(
            "Your workspace requires two-step sign-in. Set it up to continue."
        )
    elif caller.password_expired and path not in _EXPIRED_PASSWORD_ALLOWS:
        raise PasswordExpired("Your password has expired. Choose a new one to continue.")
    return caller


async def _key_caller(request: Request, sm: Sessionmaker, key: str, ip: str | None) -> Caller:
    """An API key (md PL1, PL2): who it acts as, the workspace's request budget, and
    whether its level and the route allow this request."""
    caller = replace(await api_keys.resolve(sm, key, ip or ""), ip=ip)
    logs.bind(workspace_id=caller.workspace_id, member_id=caller.member_id)
    wait = request.app.state.api_key_requests.hit(str(caller.workspace_id))
    if wait is not None:
        raise TooManyAttempts(
            "Too many requests with this workspace's API keys. Wait and try again.", wait
        )
    level = api_key_rules.KeyAccess(str(caller.api_key_access))
    reason = api_key_rules.refusal(level, request.method, request.url.path)
    if reason is not None:
        raise Forbidden(reason)
    return caller


def get_sealer(request: Request) -> Sealer:
    """Opens secrets kept in the database (2FA secrets)."""
    sealer: Sealer | None = getattr(request.app.state, "sealer", None)
    if sealer is None:
        sealer = Sealer(request.app.state.settings.secret_key.get_secret_value())
        request.app.state.sealer = sealer
    return sealer


SealerDep = Annotated[Sealer, Depends(get_sealer)]


CallerDep = Annotated[Caller, Depends(get_caller)]

_portal_bearer = HTTPBearer(
    auto_error=False, description="The token from POST /api/v1/portal/{workspace}/session."
)


async def get_customer(
    sm: SessionmakerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_portal_bearer)],
) -> portal.Customer:
    """The signed-in portal customer. Agent tokens are refused (separate sessions)."""
    if credentials is None or not credentials.credentials:
        raise NotSignedIn("Sign in first.")
    customer = await portal.resolve(sm, credentials.credentials.strip())
    logs.bind(workspace_id=customer.workspace_id)
    return customer


CustomerDep = Annotated[portal.Customer, Depends(get_customer)]


def limit_attempts(request: Request) -> None:
    """Sign-in and password-change attempts per client IP (429 with Retry-After)."""
    ip = request.client.host if request.client else "unknown"
    wait = request.app.state.attempts.hit(ip)
    if wait is not None:
        raise TooManyAttempts(
            f"Too many attempts from this address. Try again in {wait} seconds.", wait
        )


AttemptLimit = Depends(limit_attempts)


def limit_form_posts(request: Request) -> None:
    """Public web form posts per client IP (429 with Retry-After)."""
    ip = request.client.host if request.client else "unknown"
    wait = request.app.state.form_attempts.hit(ip)
    if wait is not None:
        raise TooManyAttempts(
            f"Too many messages from this address. Try again in {wait} seconds.", wait
        )


FormLimit = Depends(limit_form_posts)


def limit_votes(request: Request) -> None:
    """Help article votes per client IP (429 with Retry-After)."""
    ip = request.client.host if request.client else "unknown"
    wait = request.app.state.vote_attempts.hit(ip)
    if wait is not None:
        raise TooManyAttempts(
            f"Too many votes from this address. Try again in {wait} seconds.", wait
        )


VoteLimit = Depends(limit_votes)


def limit_public_reads(request: Request) -> None:
    """Public pages (CSAT link, help center) per client IP: its own budget, so customers
    behind one office address never use up that address's sign-ins."""
    ip = request.client.host if request.client else "unknown"
    wait = request.app.state.public_attempts.hit(ip)
    if wait is not None:
        raise TooManyAttempts(
            f"Too many requests from this address. Try again in {wait} seconds.", wait
        )


PublicLimit = Depends(limit_public_reads)


def within(limiter: SlidingWindowLimiter, key: str, what: str) -> None:
    """One more request against a per-member (or per-workspace) budget: 429 with
    Retry-After when it is used up."""
    wait = limiter.hit(key)
    if wait is not None:
        raise TooManyAttempts(f"Too many {what}. Try again in {wait} seconds.", wait)


def fallback_sender(settings: Settings) -> Sender | None:
    """The server's sender (`CEDESK_MAIL_FROM_ADDRESS`) for mail a workspace sends before
    it has a support address of its own."""
    if not settings.mail_from_address:
        return None
    return Sender(settings.mail_from_address, settings.mail_from_name)


IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Optional, any unique string (a UUID is good), up to 255 characters. "
        "Send the same key when you retry: a repeat returns what the first request made "
        "(header `Idempotent-Replayed: true`) instead of making it twice. The same key with "
        "a different request is 422. Keys last 24 hours, per member.",
    ),
]


def idempotency(key: str | None, scope: str, *parts: bytes) -> Idempotency | None:
    """`scope`: the route with its ids; `parts`: what was sent."""
    if key is None:
        return None
    try:
        checked = idempotency_rules.check_key(key)
    except idempotency_rules.BadKey as exc:
        raise Invalid(str(exc)) from exc
    return Idempotency(checked, idempotency_rules.fingerprint(scope, *parts))


def mark_replayed(idem: Idempotency | None, response: Response) -> None:
    if idem is not None and idem.replayed:
        response.headers["Idempotent-Replayed"] = "true"
