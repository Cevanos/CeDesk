"""Automation rules (md W15, W16; feature-gap plan 5.1, 5.5): Settings > Rules, Admin only
(the `settings` right), with test mode."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.workflow_schemas import (
    Actions,
    Conditions,
    RuleEvent,
    RuleIn,
    RuleName,
    RuleOrderBody,
    RuleOut,
    RulePatch,
)
from cedesk.helpdesk.service import rules
from cedesk.helpdesk.service.errors import Invalid
from cedesk.helpdesk.service.rules import RuleTest, RuleView

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
        max_runs_per_hour=body.max_runs_per_hour,
        max_runs_per_day=body.max_runs_per_day,
    )
    return _out(view)


class RuleDraft(Body):
    name: RuleName = "Test"
    event: RuleEvent
    conditions: Conditions
    actions: Actions


class RuleTestBody(Body):
    ticket_id: UUID = Field(description="The ticket to try it on. Nothing on it changes.")
    rule: RuleDraft | None = Field(
        default=None,
        description="An unsaved rule (POST /rules/test only): what the editor holds now.",
    )


class ConditionCheck(BaseModel):
    condition: dict[str, Any]
    matched: bool


class ActionCheck(BaseModel):
    action: dict[str, Any]
    would: str | None = Field(
        description='What it would write, e.g. "Tag vip"; null: it would change nothing.'
    )


class RuleTestOut(BaseModel):
    rule_name: str
    event: str
    matched: bool = Field(description="Every condition matched, so the actions would run.")
    conditions: list[ConditionCheck]
    actions: list[ActionCheck] = Field(description="Empty when it did not match.")
    blocked: str | None = Field(
        description="A saved rule only: the safeguard that would stop it now (loop, per "
        "ticket, hourly or daily limit), else null."
    )
    ticket_after: dict[str, Any] = Field(
        description="The ticket as the actions would leave it: status, priority, type_id, "
        "team_id, tags, snoozed, followers."
    )


def _test_out(t: RuleTest) -> RuleTestOut:
    return RuleTestOut.model_validate(t.__dict__)


@router.post(
    "/rules/test",
    response_model=RuleTestOut,
    summary="Test mode: try an unsaved rule on a ticket; changes and sends nothing (Admin)",
)
async def test_draft(body: RuleTestBody, caller: CallerDep, sm: SessionmakerDep) -> RuleTestOut:
    if body.rule is None:
        raise Invalid("Send the rule to test (or use POST /rules/{id}/test).")
    return _test_out(
        await rules.test_rule(sm, caller, body.ticket_id, draft=body.rule.model_dump())
    )


@router.post(
    "/rules/{rule_id}/test",
    response_model=RuleTestOut,
    summary="Test mode: try a saved rule on a ticket; changes and sends nothing (Admin)",
)
async def test_saved(
    rule_id: UUID, body: RuleTestBody, caller: CallerDep, sm: SessionmakerDep
) -> RuleTestOut:
    return _test_out(await rules.test_rule(sm, caller, body.ticket_id, rule_id=rule_id))


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
        max_runs_per_hour=body.max_runs_per_hour,
        max_runs_per_day=body.max_runs_per_day,
    )
    return _out(view)


@router.delete(
    "/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a rule (Admin)"
)
async def delete_rule(rule_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await rules.delete_rule(sm, caller, rule_id)
