"""Request dependencies. Authenticated routes take `CallerDep`; none reads auth headers."""

from typing import Annotated

from fastapi import Depends, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cedesk import logs
from cedesk.dnscheck import TxtLookup
from cedesk.helpdesk.domain import idempotency as idempotency_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.service import auth, portal
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotSignedIn, TooManyAttempts
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.settings import Settings
from cedesk.storage import ObjectStore

_bearer = HTTPBearer(auto_error=False, description="The token from POST /api/v1/auth/sign-in.")


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


async def get_caller(
    sm: SessionmakerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Caller:
    if credentials is None or not credentials.credentials:
        raise NotSignedIn("Sign in first.")
    caller = await auth.resolve(sm, credentials.credentials.strip())
    logs.bind(workspace_id=caller.workspace_id, member_id=caller.member_id)
    return caller


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
