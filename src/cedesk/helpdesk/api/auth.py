"""Sign-in, sign-out and the signed-in member."""

from fastapi import APIRouter, status

from cedesk.helpdesk.api.deps import AttemptLimit, CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import (
    ChangePasswordBody,
    MemberOut,
    MeOut,
    SignedInOut,
    SignInBody,
    WorkspaceOut,
)
from cedesk.helpdesk.service import auth, members

router = APIRouter(tags=["sign-in"])


@router.post(
    "/auth/sign-in",
    response_model=SignedInOut,
    summary="Sign in with workspace address, email and password",
    responses={
        401: {"description": "The details did not match (same answer for every reason)"},
        429: {"description": "Too many attempts from this address; see Retry-After"},
    },
    dependencies=[AttemptLimit],
)
async def sign_in(body: SignInBody, sm: SessionmakerDep) -> SignedInOut:
    signed = await auth.sign_in(
        sm, workspace=body.workspace, email=body.email, password=body.password
    )
    return SignedInOut(
        token=signed.token,
        expires_at=signed.expires_at,
        member_id=signed.caller.member_id,
        role=signed.caller.role,
    )


@router.post("/auth/sign-out", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def sign_out(caller: CallerDep, sm: SessionmakerDep) -> None:
    await auth.sign_out(sm, caller)


@router.get("/me", response_model=MeOut, summary="The signed-in member and their workspace")
async def me(caller: CallerDep, sm: SessionmakerDep) -> MeOut:
    member, workspace = await members.me(sm, caller)
    return MeOut(
        member=MemberOut.model_validate(dict(member)),
        workspace=WorkspaceOut.model_validate(dict(workspace)),
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
