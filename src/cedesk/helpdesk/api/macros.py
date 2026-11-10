"""Macros (md W18): Settings > Macros (Admin), the Macros menu and running one on a
ticket (agents, supervisors, Admins)."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.ticket_schemas import TicketOut
from cedesk.helpdesk.api.tickets import IfMatch, detail_response
from cedesk.helpdesk.api.workflow_schemas import MacroIn, MacroOrderBody, MacroOut, MacroPatch
from cedesk.helpdesk.service import macros
from cedesk.helpdesk.service.macros import MacroView

router = APIRouter(tags=["macros"])


def _out(view: MacroView) -> MacroOut:
    return MacroOut.model_validate({**dict(view.row), "problems": view.problems})


@router.get("/macros", response_model=list[MacroOut], summary="All macros in menu order")
async def list_macros(caller: CallerDep, sm: SessionmakerDep) -> list[MacroOut]:
    return [_out(v) for v in await macros.list_macros(sm, caller)]


@router.post(
    "/macros",
    response_model=MacroOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a macro; it goes last in the menu (Admin)",
)
async def create_macro(body: MacroIn, caller: CallerDep, sm: SessionmakerDep) -> MacroOut:
    return _out(await macros.create_macro(sm, caller, name=body.name, steps=body.steps))


@router.put(
    "/macros/order",
    response_model=list[MacroOut],
    summary="Set the menu order: every macro id once (Admin)",
)
async def reorder_macros(
    body: MacroOrderBody, caller: CallerDep, sm: SessionmakerDep
) -> list[MacroOut]:
    return [_out(v) for v in await macros.reorder_macros(sm, caller, body.ids)]


@router.patch(
    "/macros/{macro_id}", response_model=MacroOut, summary="Rename or change the steps (Admin)"
)
async def change_macro(
    macro_id: UUID, body: MacroPatch, caller: CallerDep, sm: SessionmakerDep
) -> MacroOut:
    view = await macros.change_macro(sm, caller, macro_id, name=body.name, steps=body.steps)
    return _out(view)


@router.delete(
    "/macros/{macro_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a macro (Admin)"
)
async def delete_macro(macro_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await macros.delete_macro(sm, caller, macro_id)


@router.post(
    "/tickets/{ticket_id}/macros/{macro_id}",
    response_model=TicketOut,
    summary="Run a macro on a ticket: all its steps in one go",
    description="Steps run in order in one transaction; if one is refused, nothing is "
    "saved. Light agents get 403; an unaccepted handover gets 409.",
)
async def run_macro(
    ticket_id: UUID,
    macro_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    if_match: IfMatch = None,
) -> TicketOut:
    view = await macros.run_macro(sm, caller, ticket_id, macro_id, if_match=if_match)
    return detail_response(view, response)
