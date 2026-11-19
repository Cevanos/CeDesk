"""Self-service sign-up: create a workspace from the sign-in page, after an email code.

Off unless CEDESK_SIGNUP_ENABLED: then every route but `signup-config` answers 404, as if
it did not exist. The config route always answers, so the sign-in page knows whether to
show the "Create a workspace" link.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.routing import APIRoute

from cedesk.helpdesk.api.deps import AttemptLimit, PublicLimit, SessionmakerDep, client_ip
from cedesk.helpdesk.api.schemas import (
    SignupBody,
    SignupConfigOut,
    SignupDoneOut,
    SignupResendBody,
    SignupStartedOut,
    SignupVerifyBody,
)
from cedesk.helpdesk.service import signup
from cedesk.helpdesk.service.errors import NotFound
from cedesk.settings import Settings


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    if not settings.signup_enabled:
        raise NotFound("Not Found")
    return settings


def _limits(settings: Settings) -> signup.Limits:
    return signup.Limits(
        per_ip_hour=settings.signup_per_ip_hour,
        workspaces_per_ip_day=settings.signup_workspaces_per_ip_day,
    )


class _OnlyWhenOn(APIRoute):
    """Answers 404 when sign-up is off, before the body is read or checked: a switched-off
    server can't be told apart from one without these routes, even by a bad body."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            _settings(request)
            return await handler(request)

        return guarded


router = APIRouter(tags=["sign-up"])
_when_on = APIRouter(tags=["sign-up"], route_class=_OnlyWhenOn)


@router.get(
    "/auth/signup-config",
    response_model=SignupConfigOut,
    summary="Whether self-service sign-up is on, and the CAPTCHA site key",
    dependencies=[PublicLimit],
)
async def signup_config(request: Request) -> SignupConfigOut:
    settings: Settings = request.app.state.settings
    if not settings.signup_enabled:
        return SignupConfigOut(enabled=False)
    return SignupConfigOut(enabled=True, captcha_site_key=settings.signup_captcha_site_key or None)


@_when_on.post(
    "/auth/signup",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SignupStartedOut,
    summary="Start creating a workspace: emails a 6-digit code",
    description="Nothing is created yet: the code (10 minutes, 5 tries) goes to the email, "
    "and POST /auth/signup/verify creates the workspace. A new sign-up for the same email "
    "from the same address replaces its waiting one.",
    responses={
        403: {"description": "`captcha_failed`: the CAPTCHA answer was missing or refused"},
        404: {"description": "Self-service sign-up is off on this server"},
        409: {"description": "`slug_taken`: another workspace has this address"},
        422: {"description": "An address, email, name or password that isn't allowed"},
        429: {"description": "Too many sign-ups from this address or codes for this email"},
    },
    dependencies=[AttemptLimit],
)
async def start_signup(body: SignupBody, request: Request, sm: SessionmakerDep) -> SignupStartedOut:
    settings = _settings(request)
    started = await signup.start(
        sm,
        slug=body.workspace,
        workspace_name=body.workspace_name,
        email=body.email,
        admin_name=body.name,
        password=body.password,
        ip=client_ip(request),
        secret_key=settings.secret_key.get_secret_value(),
        captcha=request.app.state.captcha,
        captcha_token=body.captcha_token,
        hash_slots=request.app.state.signup_hash_slots,
        ip_limits=_limits(settings),
    )
    return SignupStartedOut(
        signup_id=started.signup_id,
        code_expires_at=started.code_expires_at,
        resend_after=started.resend_after,
    )


@_when_on.post(
    "/auth/signup/verify",
    response_model=SignupDoneOut,
    summary="Finish: the emailed code creates the workspace and its first Admin",
    description="Sign in afterwards with POST /auth/sign-in. Safe to repeat: a second call "
    "with the same code answers the same.",
    responses={
        404: {"description": "Self-service sign-up is off on this server"},
        409: {"description": "`slug_taken`: send the same code again with a new `workspace`"},
        410: {"description": "`code_expired` (resend a code) or `signup_expired` (start over)"},
        422: {"description": "`bad_code`: the code is wrong; try again"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def verify_signup(
    body: SignupVerifyBody, request: Request, sm: SessionmakerDep
) -> SignupDoneOut:
    settings = _settings(request)
    done = await signup.verify(
        sm,
        signup_id=body.signup_id,
        code=body.code,
        slug=body.workspace,
        ip=client_ip(request),
        secret_key=settings.secret_key.get_secret_value(),
        default_plan=settings.signup_default_plan,
        ip_limits=_limits(settings),
    )
    return SignupDoneOut(workspace=done.workspace)


@_when_on.post(
    "/auth/signup/resend",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SignupStartedOut,
    summary="Email a new code for a waiting sign-up (the old one stops working)",
    responses={
        404: {"description": "Self-service sign-up is off on this server"},
        410: {"description": "`signup_expired`: start the sign-up again"},
        429: {"description": "Asked too soon (60 s) or too often (3 an hour); see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def resend_code(
    body: SignupResendBody, request: Request, sm: SessionmakerDep
) -> SignupStartedOut:
    settings = _settings(request)
    started = await signup.resend(
        sm, signup_id=body.signup_id, secret_key=settings.secret_key.get_secret_value()
    )
    return SignupStartedOut(
        signup_id=started.signup_id,
        code_expires_at=started.code_expires_at,
        resend_after=started.resend_after,
    )


router.include_router(_when_on)
