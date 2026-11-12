"""Request and response bodies for sign-in and members."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cedesk.helpdesk.domain.permissions import Access, Action, Role, Scope

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
]
Password = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignInBody(Body):
    workspace: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
    email: Email
    password: Password


class MemberOut(BaseModel):
    id: UUID
    email: str
    name: str
    role: Role
    role_id: UUID | None = Field(default=None, description="Set when `role` is custom.")
    role_name: str | None = Field(default=None, description="The custom role's name.")
    is_active: bool
    two_factor: bool = Field(default=False, description="Uses two-step sign-in.")
    created_at: datetime


class AccessOut(BaseModel):
    """What the signed-in member may do: hide what they can't."""

    role: Role
    role_name: str = Field(description="For display: Admin, Agent, or the custom role's name.")
    actions: list[Action]
    scope: Scope = Field(
        description="all; team_edit (edits its team's tickets, views the rest); team_only "
        "(sees its team's tickets only)."
    )
    team_id: UUID | None
    team_name: str | None

    @classmethod
    def of(cls, access: Access) -> "AccessOut":
        return cls(
            role=access.role,
            role_name=access.label,
            actions=sorted(access.actions),
            scope=access.scope,
            team_id=access.team_id,
            team_name=access.team_name,
        )


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str | None


class SignedInOut(BaseModel):
    token: str = Field(description="Send as `Authorization: Bearer <token>`. Shown only once.")
    expires_at: datetime
    member_id: UUID
    role: Role
    password_expired: bool = Field(
        default=False,
        description="True: show the change-password screen. Until the password is changed, "
        "every route except GET /me, POST /me/password and sign-out answers 403 "
        "`PasswordExpired`.",
    )
    two_factor_required: bool = Field(
        default=False,
        description="True: ask for the code from the authenticator app and send it to "
        "POST /auth/2fa with this token within 10 minutes. Until then every other route "
        "answers 403 `TwoFactorRequired`.",
    )


class MeOut(BaseModel):
    member: MemberOut
    workspace: WorkspaceOut
    access: AccessOut
    password_expired: bool = Field(
        default=False, description="True: only POST /me/password and sign-out work until then."
    )
    two_factor_setup_required: bool = Field(
        default=False,
        description="True: the workspace requires two-step sign-in and you haven't set it up; "
        "only /me, /me/2fa, /me/2fa/setup, /me/2fa/confirm and sign-out work until then.",
    )


class MemberPage(BaseModel):
    members: list[MemberOut]
    next_after: UUID | None = Field(description="Pass as `after` for the next page.")


class AddMemberBody(Body):
    email: Email
    name: Name
    role: Role
    role_id: UUID | None = Field(default=None, description="With role custom: which role.")
    password: Password = Field(description="A first password the member changes later.")


class ChangePasswordBody(Body):
    current_password: Password
    new_password: Password = Field(description="10 to 128 characters, not the current one.")


class ForgotPasswordBody(Body):
    workspace: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
    email: Email


class ResetWithLinkBody(Body):
    token: Annotated[str, StringConstraints(min_length=1, max_length=200)] = Field(
        description="From the link in the email: the part after `#token=`."
    )
    new_password: Password = Field(description="10 to 128 characters.")


class ResetPasswordBody(Body):
    new_password: Password = Field(
        description="10 to 128 characters. Give it to the member; they can change it later."
    )


class ChangeMemberBody(Body):
    name: Name | None = None
    role: Role | None = None
    role_id: UUID | None = Field(default=None, description="With role custom: which role.")
    is_active: bool | None = None
