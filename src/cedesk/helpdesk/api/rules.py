"""Automation rules (md W15, W16): Settings > Rules, Admin only."""

from uuid import UUID

from fastapi import APIRouter, status

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.workflow_schemas import RuleIn, RuleOrderBody, RuleOut, RulePatch
from cedesk.helpdesk.service import rules
from cedesk.helpdesk.service.rules import RuleView

router = APIRouter(tags=["rules"])


def _out(view: RuleView) -> RuleOut:
    return RuleOut.model_validate({**dict(view.row), "problems": view.problems})


@router.get("/rules", response_model=list[RuleOut], summary="All rules in run order (Admin)")
async def list_rules(caller: CallerDep, sm: SessionmakerDep) -> list[RuleOut]:
    return [_out(v) for v in await rules.list_rules(sm, caller)]


@router.post(
    "/rules",
    response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a rule; it runs after the others (Admin)",
)
async def create_rule(body: RuleIn, caller: CallerDep, sm: SessionmakerDep) -> RuleOut:
    view = await rules.create_rule(
        sm,
        caller,
        name=body.name,
        enabled=body.enabled,
        event=body.event,
        conditions=body.conditions,
        actions=body.actions,
    )
    return _out(view)


@router.put(
    "/rules/order",
    response_model=list[RuleOut],
    summary="Set the run order: every rule id once (Admin)",
)
async def reorder_rules(
    body: RuleOrderBody, caller: CallerDep, sm: SessionmakerDep
) -> list[RuleOut]:
    return [_out(v) for v in await rules.reorder_rules(sm, caller, body.ids)]


@router.patch(
    "/rules/{rule_id}",
    response_model=RuleOut,
    summary="Rename, switch on or off, or change what it checks and does (Admin)",
)
async def change_rule(
    rule_id: UUID, body: RulePatch, caller: CallerDep, sm: SessionmakerDep
) -> RuleOut:
    view = await rules.change_rule(
        sm,
        caller,
        rule_id,
        name=body.name,
        enabled=body.enabled,
        event=body.event,
        conditions=body.conditions,
        actions=body.actions,
    )
    return _out(view)


@router.delete(
    "/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a rule (Admin)"
)
async def delete_rule(rule_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await rules.delete_rule(sm, caller, rule_id)
