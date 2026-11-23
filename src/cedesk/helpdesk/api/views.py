"""Saved views (feature-gap plan 1.2): named ticket-list filters, personal or shared."""

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import saved_views as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.tickets import FILTER_KEYS
from cedesk.helpdesk.service import saved_views

router = APIRouter(prefix="/views", tags=["views"])

ViewName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=rules.NAME_MAX)
]
FILTERS_HELP = (
    "The ticket list's own query parameters, as an object: "
    + ", ".join(FILTER_KEYS)
    + ' (for example {"status": "pending", "scope": "mine", "sort": "sla"}). Any other key, '
    "or a value `GET /tickets` would refuse, is 422. Open the view by sending these to "
    "`GET /tickets`."
)


class ViewIn(Body):
    name: ViewName
    filters: dict[str, Any] = Field(max_length=len(FILTER_KEYS), description=FILTERS_HELP)
    shared: bool = Field(
        default=False,
        description="Shared with everyone in the workspace (Admins and Supervisors). "
        "false: only you see it.",
    )


class ViewPatch(Body):
    """Send only what changes."""

    name: ViewName | None = None
    filters: dict[str, Any] | None = Field(
        default=None, max_length=len(FILTER_KEYS), description="The whole new set."
    )
    shared: bool | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class ViewOut(BaseModel):
    id: UUID
    name: str
    filters: dict[str, Any] = Field(description="Only the filters that are set.")
    shared: bool
    owner_id: UUID
    owner_name: str
    can_edit: bool = Field(description="Whether you may change or delete it.")
    created_at: datetime
    updated_at: datetime


def _out(caller: Caller, row: Any) -> ViewOut:
    return ViewOut.model_validate(dict(row) | {"can_edit": saved_views.can_change(caller, row)})


@router.get(
    "",
    response_model=list[ViewOut],
    summary="Your personal views and the workspace's shared ones (shared first, by name)",
)
async def list_views(caller: CallerDep, sm: SessionmakerDep) -> list[ViewOut]:
    return [_out(caller, r) for r in await saved_views.list_views(sm, caller)]


@router.post(
    "",
    response_model=ViewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a view (audited)",
    description=f"At most {rules.MAX_PERSONAL} personal views per person and "
    f"{rules.MAX_SHARED} shared views per workspace.",
    responses={
        403: {"description": "shared: true without the right to share views"},
        409: {"description": "You (or the shared views) already have one with that name"},
        422: {"description": "A filter the ticket list doesn't take, or a bad value"},
    },
)
async def create_view(body: ViewIn, caller: CallerDep, sm: SessionmakerDep) -> ViewOut:
    row = await saved_views.create_view(
        sm, caller, name=body.name, filters=body.filters, shared=body.shared
    )
    return _out(caller, row)


@router.get("/{view_id}", response_model=ViewOut, summary="One view")
async def get_view(view_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ViewOut:
    return _out(caller, await saved_views.get_view(sm, caller, view_id))


@router.patch(
    "/{view_id}",
    response_model=ViewOut,
    summary="Rename a view, change its filters, or share or unshare it (audited)",
    description="Your own personal views; shared views need the right to share views. "
    "Unsharing makes it its owner's personal view again.",
    responses={404: {"description": "No such view, or someone else's personal view"}},
)
async def change_view(
    view_id: UUID, body: ViewPatch, caller: CallerDep, sm: SessionmakerDep
) -> ViewOut:
    row = await saved_views.change_view(
        sm, caller, view_id, name=body.name, filters=body.filters, shared=body.shared
    )
    return _out(caller, row)


@router.delete(
    "/{view_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a view (audited)",
    responses={404: {"description": "No such view, or someone else's personal view"}},
)
async def delete_view(view_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await saved_views.delete_view(sm, caller, view_id)
