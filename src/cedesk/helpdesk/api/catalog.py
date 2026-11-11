"""Settings: ticket types (with their fields) and teams."""

from uuid import UUID

from fastapi import APIRouter, status

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.ticket_schemas import (
    ReorderBody,
    TeamIn,
    TeamOut,
    TeamPatch,
    TicketTypeIn,
    TicketTypeOut,
    TicketTypePatch,
)
from cedesk.helpdesk.service import catalog

router = APIRouter()


@router.get(
    "/ticket-types", response_model=list[TicketTypeOut], tags=["ticket types"], summary="In order"
)
async def list_types(caller: CallerDep, sm: SessionmakerDep) -> list[TicketTypeOut]:
    return [TicketTypeOut.model_validate(dict(r)) for r in await catalog.list_types(sm, caller)]


@router.post(
    "/ticket-types",
    response_model=TicketTypeOut,
    status_code=status.HTTP_201_CREATED,
    tags=["ticket types"],
    summary="Add a ticket type with its fields (Admin)",
)
async def create_type(body: TicketTypeIn, caller: CallerDep, sm: SessionmakerDep) -> TicketTypeOut:
    row = await catalog.create_type(
        sm,
        caller,
        name=body.name,
        fields=[f.model_dump() for f in body.fields],
        required_skill=body.required_skill,
        for_customers=body.for_customers,
    )
    return TicketTypeOut.model_validate(dict(row))


@router.put(
    "/ticket-types/order",
    response_model=list[TicketTypeOut],
    tags=["ticket types"],
    summary="Reorder ticket types (Admin)",
)
async def reorder_types(
    body: ReorderBody, caller: CallerDep, sm: SessionmakerDep
) -> list[TicketTypeOut]:
    rows = await catalog.reorder_types(sm, caller, body.ids)
    return [TicketTypeOut.model_validate(dict(r)) for r in rows]


@router.patch(
    "/ticket-types/{type_id}",
    response_model=TicketTypeOut,
    tags=["ticket types"],
    summary="Rename, change fields or skill, show or hide for customers (Admin)",
)
async def change_type(
    type_id: UUID, body: TicketTypePatch, caller: CallerDep, sm: SessionmakerDep
) -> TicketTypeOut:
    row = await catalog.change_type(
        sm,
        caller,
        type_id,
        name=body.name,
        fields=None if body.fields is None else [f.model_dump() for f in body.fields],
        required_skill=body.required_skill,
        clear_skill="required_skill" in body.model_fields_set and body.required_skill is None,
        for_customers=body.for_customers,
    )
    return TicketTypeOut.model_validate(dict(row))


@router.delete(
    "/ticket-types/{type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["ticket types"],
    summary="Delete a ticket type; its tickets keep everything else (Admin)",
)
async def delete_type(type_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await catalog.delete_type(sm, caller, type_id)


@router.get("/teams", response_model=list[TeamOut], tags=["teams"], summary="All teams")
async def list_teams(caller: CallerDep, sm: SessionmakerDep) -> list[TeamOut]:
    return [TeamOut.model_validate(dict(r)) for r in await catalog.list_teams(sm, caller)]


@router.post(
    "/teams",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
    tags=["teams"],
    summary="Add a team (Admin)",
)
async def create_team(body: TeamIn, caller: CallerDep, sm: SessionmakerDep) -> TeamOut:
    row = await catalog.create_team(
        sm,
        caller,
        name=body.name,
        auto_assign=body.auto_assign,
        max_open=body.max_open_per_agent,
        member_ids=body.member_ids,
    )
    return TeamOut.model_validate(dict(row))


@router.patch(
    "/teams/{team_id}",
    response_model=TeamOut,
    tags=["teams"],
    summary="Rename, change settings or replace members (Admin)",
)
async def change_team(
    team_id: UUID, body: TeamPatch, caller: CallerDep, sm: SessionmakerDep
) -> TeamOut:
    row = await catalog.change_team(
        sm,
        caller,
        team_id,
        name=body.name,
        auto_assign=body.auto_assign,
        max_open=body.max_open_per_agent,
        member_ids=body.member_ids,
    )
    return TeamOut.model_validate(dict(row))


@router.delete(
    "/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["teams"],
    summary="Delete a team; its tickets become team-less (Admin)",
)
async def delete_team(team_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await catalog.delete_team(sm, caller, team_id)
