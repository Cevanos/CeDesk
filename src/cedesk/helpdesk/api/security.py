"""Security settings (md S3-S6), Test my IP and the security score (S8). Admin only."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, client_ip
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import security as rules
from cedesk.helpdesk.domain.security import SessionLength
from cedesk.helpdesk.service import security

router = APIRouter(tags=["security"])

Network = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class SecuritySettingsBody(Body):
    password_min: int = Field(default=10, ge=8, le=64, description="Minimum password length.")
    password_expiry_days: int | None = Field(
        default=None,
        description="null (never), 90 or 180. An expired password must be changed at the next "
        "sign-in (403 `PasswordExpired` on other routes until then).",
    )
    session_length: SessionLength = Field(
        default=SessionLength.h12,
        description="Sign-out after: 1h_idle (an hour without use), 8h, 12h or 7d. A shorter "
        "setting also ends longer sessions already open.",
    )
    ip_allowlist: list[Network] = Field(
        default_factory=list,
        max_length=rules.IP_LIST_MAX,
        description="CIDR blocks or addresses; empty = every address. Checked on sign-in and "
        "every signed-in request (403 `IpNotAllowed`). Must include your own address.",
    )
    attachments_need_sign_in: bool = Field(
        default=False,
        description="Download links become GET /attachments/{id}/content, which needs the "
        "Bearer token, instead of links anyone holding them can open.",
    )
    admin_alerts: bool = Field(
        default=True,
        description="Email every Admin when a member is added, deactivated or reactivated, "
        "the IP list or the 2FA rule changes, or a member's 2FA is reset.",
    )
    require_2fa: bool = Field(
        default=False,
        description="Every member must use two-step sign-in. Members without it can only set "
        "it up (403 `TwoFactorSetupRequired` elsewhere). Turn it on for yourself first.",
    )


class SecuritySettingsOut(BaseModel):
    password_min: int
    password_expiry_days: int | None
    session_length: SessionLength
    ip_allowlist: list[str]
    attachments_need_sign_in: bool
    admin_alerts: bool
    require_2fa: bool
    updated_at: datetime | None = None
    updated_by: UUID | None = None
    updated_by_name: str | None = None

    @classmethod
    def of(cls, row: Any) -> "SecuritySettingsOut":
        if row is None:
            d = rules.DEFAULTS
            return cls(
                password_min=d.password_min,
                password_expiry_days=d.password_expiry_days,
                session_length=d.session_length,
                ip_allowlist=[],
                attachments_need_sign_in=d.attachments_need_sign_in,
                admin_alerts=d.admin_alerts,
                require_2fa=d.require_2fa,
            )
        return cls.model_validate(dict(row))


class TestIpBody(Body):
    ip_allowlist: list[Network] | None = Field(
        default=None,
        max_length=rules.IP_LIST_MAX,
        description="The list to test (before saving it); leave out for the saved list.",
    )


class TestIpOut(BaseModel):
    ip: str | None = Field(description="Your address as CeDesk sees it.")
    allowed: bool


class ScoreItemOut(BaseModel):
    key: str = Field(
        description="sso, two_factor, ip_allowlist, passwords (minimum 12+), redaction, "
        "session_length (not 7 days), attachments_sign_in, admin_alerts, portal_domains"
    )
    points: int
    on: bool
    status: str = Field(description="healthy, critical or recommended")


class ScoreOut(BaseModel):
    score: int = Field(description="0-100")
    level: str = Field(description="healthy (80+), recommended (50-79), critical (under 50)")
    items: list[ScoreItemOut]


@router.get(
    "/security-settings", response_model=SecuritySettingsOut, summary="Security settings (Admin)"
)
async def get_settings(caller: CallerDep, sm: SessionmakerDep) -> SecuritySettingsOut:
    return SecuritySettingsOut.of(await security.get_settings(sm, caller))


@router.put(
    "/security-settings",
    response_model=SecuritySettingsOut,
    summary="Save security settings (Admin; applies at once)",
    description="Only the fields sent change; a field left out keeps its saved value.",
)
async def save_settings(
    body: SecuritySettingsBody, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> SecuritySettingsOut:
    row = await security.save_settings(
        sm, caller, body.model_dump(include=body.model_fields_set), ip=client_ip(request)
    )
    return SecuritySettingsOut.of(row)


@router.post(
    "/security/test-my-ip",
    response_model=TestIpOut,
    summary="Test my IP against a list, or the saved one (Admin)",
)
async def test_my_ip(
    body: TestIpBody, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> TestIpOut:
    check = await security.test_ip(sm, caller, ip=client_ip(request), allowlist=body.ip_allowlist)
    return TestIpOut(ip=check.ip, allowed=check.allowed)


@router.get("/security/score", response_model=ScoreOut, summary="Security score (Admin)")
async def score(caller: CallerDep, sm: SessionmakerDep) -> ScoreOut:
    result = await security.security_score(sm, caller)
    return ScoreOut(
        score=result.score,
        level=result.level,
        items=[ScoreItemOut(**vars(i)) for i in result.items],
    )
