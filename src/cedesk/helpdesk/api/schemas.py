"""Request and response bodies for sign-in and members."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cedesk.helpdesk.domain.permissions import Role

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
    is_active: bool
    created_at: datetime


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str | None


class SignedInOut(BaseModel):
    token: str = Field(description="Send as `Authorization: Bearer <token>`. Shown only once.")
    expires_at: datetime
    member_id: UUID
    role: Role


class MeOut(BaseModel):
    member: MemberOut
    workspace: WorkspaceOut


class MemberPage(BaseModel):
    members: list[MemberOut]
    next_after: UUID | None = Field(description="Pass as `after` for the next page.")


class AddMemberBody(Body):
    email: Email
    name: Name
    role: Role
    password: Password = Field(description="A first password the member changes later.")


class ChangePasswordBody(Body):
    current_password: Password
    new_password: Password = Field(description="10 to 128 characters, not the current one.")


class ResetPasswordBody(Body):
    new_password: Password = Field(
        description="10 to 128 characters. Give it to the member; they can change it later."
    )


class ChangeMemberBody(Body):
    name: Name | None = None
    role: Role | None = None
    is_active: bool | None = None
