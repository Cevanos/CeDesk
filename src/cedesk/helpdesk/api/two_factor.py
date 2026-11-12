"""Two-step sign-in with an authenticator app (md S2)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import AttemptLimit, CallerDep, SealerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.service import two_factor

router = APIRouter(tags=["two-step sign-in"])

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=20)]


class CodeBody(Body):
    code: Code = Field(description="6 digits from the app, or (where allowed) a recovery code.")


class VerifiedOut(BaseModel):
    expires_at: datetime = Field(description="When this session now ends.")


class StatusOut(BaseModel):
    enabled: bool
    required: bool = Field(description="The workspace requires it for every member.")
    recovery_codes_left: int


class SetupOut(BaseModel):
    secret: str = Field(description="For typing into the app by hand.")
    otpauth_uri: str = Field(description="Show as a QR code for the app to scan.")


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str] = Field(
        description="10 one-time codes, shown only now. Each works once instead of the app."
    )


@router.post(
    "/auth/2fa",
    response_model=VerifiedOut,
    summary="Second step of signing in: the code from the app, or a recovery code",
    description="Called with the token sign-in gave when `two_factor_required` was true. "
    "Wrong codes count toward the 10-try lockout.",
    dependencies=[AttemptLimit],
)
async def verify(
    body: CodeBody, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep
) -> VerifiedOut:
    return VerifiedOut(expires_at=await two_factor.verify_sign_in(sm, sealer, caller, body.code))


@router.get("/me/2fa", response_model=StatusOut, summary="My two-step sign-in")
async def my_status(caller: CallerDep, sm: SessionmakerDep) -> StatusOut:
    found = await two_factor.status(sm, caller)
    return StatusOut(
        enabled=found.enabled,
        required=found.required,
        recovery_codes_left=found.recovery_codes_left,
    )


@router.post(
    "/me/2fa/setup",
    response_model=SetupOut,
    summary="Start setting up the app (a new secret each time until confirmed)",
)
async def setup(caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep) -> SetupOut:
    found = await two_factor.start_setup(sm, sealer, caller)
    return SetupOut(secret=found.secret, otpauth_uri=found.otpauth_uri)


@router.post(
    "/me/2fa/confirm",
    response_model=RecoveryCodesOut,
    summary="Confirm with the first code: 2FA is on; returns the recovery codes",
    description="Your other sessions end.",
    dependencies=[AttemptLimit],
)
async def confirm(
    body: CodeBody, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep
) -> RecoveryCodesOut:
    codes = await two_factor.confirm_setup(sm, sealer, caller, body.code)
    return RecoveryCodesOut(recovery_codes=codes)


@router.post(
    "/me/2fa/recovery-codes",
    response_model=RecoveryCodesOut,
    summary="New recovery codes (the old ones stop working); needs a code from the app",
    dependencies=[AttemptLimit],
)
async def new_codes(
    body: CodeBody, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep
) -> RecoveryCodesOut:
    codes = await two_factor.new_recovery_codes(sm, sealer, caller, body.code)
    return RecoveryCodesOut(recovery_codes=codes)


@router.post(
    "/me/2fa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Turn my two-step sign-in off (403 when the workspace requires it)",
    dependencies=[AttemptLimit],
)
async def disable(
    body: CodeBody, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep
) -> None:
    await two_factor.turn_off(sm, sealer, caller, body.code)


@router.post(
    "/members/{member_id}/2fa/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset a member's two-step sign-in (Admin; lost phone)",
    description="Turns it off and signs them out everywhere; the Admins are emailed. If the "
    "workspace requires 2FA they set it up again at their next sign-in.",
)
async def admin_reset(member_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await two_factor.admin_reset(sm, caller, member_id)
