"""Members of the workspace: everyone can list, only Admins add or change."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import AddMemberBody, ChangeMemberBody, MemberOut, MemberPage
from cedesk.helpdesk.service import members

router = APIRouter(tags=["members"])


@router.get("/members", response_model=MemberPage, summary="Members, 50 per page by default")
async def list_members(
    caller: CallerDep,
    sm: SessionmakerDep,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MemberPage:
    rows = await members.list_members(sm, caller, after=after, limit=limit + 1)
    page = [MemberOut.model_validate(dict(r)) for r in rows[:limit]]
    return MemberPage(members=page, next_after=page[-1].id if len(rows) > limit else None)


@router.post(
    "/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member (Admin)",
)
async def add_member(body: AddMemberBody, caller: CallerDep, sm: SessionmakerDep) -> MemberOut:
    row = await members.add_member(
        sm, caller, email=body.email, name=body.name, role=body.role, password=body.password
    )
    return MemberOut.model_validate(dict(row))


@router.patch(
    "/members/{member_id}",
    response_model=MemberOut,
    summary="Change a member's name, role or active state (Admin)",
)
async def change_member(
    member_id: UUID, body: ChangeMemberBody, caller: CallerDep, sm: SessionmakerDep
) -> MemberOut:
    row = await members.change_member(
        sm, caller, member_id, name=body.name, role=body.role, is_active=body.is_active
    )
    return MemberOut.model_validate(dict(row))
