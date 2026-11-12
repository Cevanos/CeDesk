"""Sign-in, sign-out and the signed-in member."""

from fastapi import APIRouter, Request, status

from cedesk.helpdesk.api.deps import AttemptLimit, CallerDep, SessionmakerDep, client_ip
from cedesk.helpdesk.api.schemas import (
    AccessOut,
    ChangePasswordBody,
    ForgotPasswordBody,
    MemberOut,
    MeOut,
    ResetWithLinkBody,
    SignedInOut,
    SignInBody,
    WorkspaceOut,
)
from cedesk.helpdesk.service import auth, members, password_reset
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.settings import Settings

router = APIRouter(tags=["sign-in"])


@router.post(
    "/auth/sign-in",
    response_model=SignedInOut,
    summary="Sign in with workspace address, email and password",
    responses={
        401: {"description": "The details did not match (same answer for every reason)"},
        403: {"description": "This address isn't on the workspace's IP list"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def sign_in(body: SignInBody, request: Request, sm: SessionmakerDep) -> SignedInOut:
    signed = await auth.sign_in(
        sm,
        workspace=body.workspace,
        email=body.email,
        password=body.password,
        ip=client_ip(request) or "",
    )
    return SignedInOut(
        token=signed.token,
        expires_at=signed.expires_at,
        member_id=signed.caller.member_id,
        role=signed.caller.role,
        password_expired=signed.password_expired,
        two_factor_required=signed.two_factor_required,
    )


@router.post(
    "/auth/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email me a link to choose a new password",
    description="Always 202, whether or not the workspace and email exist, so it can't "
    "be used to find accounts. The link lasts 30 minutes; at most 3 are sent per hour.",
    responses={429: {"description": "Too many attempts from this address; see Retry-After"}},
    dependencies=[AttemptLimit],
)
async def forgot_password(body: ForgotPasswordBody, request: Request, sm: SessionmakerDep) -> None:
    settings: Settings = request.app.state.settings
    fallback = (
        Sender(settings.mail_from_address, settings.mail_from_name)
        if settings.mail_from_address
        else None
    )
    await password_reset.forgot_password(
        sm,
        workspace=body.workspace,
        email=body.email,
        app_url=settings.app_url,
        fallback=fallback,
    )


@router.post(
    "/auth/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Choose a new password with the emailed link (signs out everywhere)",
    responses={
        410: {"description": "The link is invalid, used or expired"},
        422: {"description": "The new password is not allowed"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def reset_password(body: ResetWithLinkBody, sm: SessionmakerDep) -> None:
    await password_reset.reset_with_link(sm, token=body.token, new_password=body.new_password)


@router.post("/auth/sign-out", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def sign_out(caller: CallerDep, sm: SessionmakerDep) -> None:
    await auth.sign_out(sm, caller)


@router.get("/me", response_model=MeOut, summary="The signed-in member and their workspace")
async def me(caller: CallerDep, sm: SessionmakerDep) -> MeOut:
    member, workspace = await members.me(sm, caller)
    return MeOut(
        member=MemberOut.model_validate(dict(member)),
        workspace=WorkspaceOut.model_validate(dict(workspace)),
        access=AccessOut.of(caller.access),
        password_expired=caller.password_expired,
        two_factor_setup_required=caller.two_factor_setup_required,
    )


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change my password (ends my other sessions)",
    responses={
        403: {"description": "Locked after too many wrong passwords"},
        409: {"description": "The password was changed elsewhere meanwhile"},
        422: {"description": "Current password not right, or the new one is not allowed"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def change_my_password(
    body: ChangePasswordBody, caller: CallerDep, sm: SessionmakerDep
) -> None:
    await members.change_own_password(
        sm, caller, current_password=body.current_password, new_password=body.new_password
    )
