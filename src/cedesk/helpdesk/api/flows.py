"""Workflows (feature-gap plan 5.2, 5.3): the visual builder's flows, its palette, test
mode and the automation run log. Settings > Automation, for the people who manage rules
(the `settings` right); a workflow that calls a webhook is an Admin's."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.workflow_schemas import PerDay, PerHour, RuleName
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.service import flows, run_log
from cedesk.helpdesk.service.errors import Invalid
from cedesk.helpdesk.service.flows import FlowTest, FlowView

router = APIRouter(tags=["automation"])

GRAPH_HELP = (
    'The builder\'s graph: {"nodes": [...], "edges": [...]}. Node types: trigger '
    '({"id", "type": "trigger", "event"}), condition ({"conditions": [...]}, leaves by yes or '
    'no), action ({"action": {...}}, one rule action), wait ({"minutes": 1-43200, "until": '
    '"customer_replied"?}: next; with until: replied or timeout). Edges: {"from", "to", '
    '"when"}. One trigger, every node reachable, at most one edge per way out, no loop '
    "without a wait, at most 50 nodes and 100 edges. A node may carry position and label "
    "for the canvas. GET /automation/catalog lists every kind with its parameters."
)
Graph = Annotated[dict[str, Any], Field(description=GRAPH_HELP)]


class FlowIn(Body):
    name: RuleName
    graph: Graph
    enabled: bool = Field(default=False, description="Workflows start turned off.")
    max_runs_per_hour: PerHour = 1000
    max_runs_per_day: PerDay = 10000


class FlowPatch(Body):
    """Send only what changes. A new graph is a new version; turning it off cancels its
    waiting runs."""

    name: RuleName | None = None
    graph: Graph | None = None
    enabled: bool | None = None
    max_runs_per_hour: PerHour | None = None
    max_runs_per_day: PerDay | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class FlowOut(BaseModel):
    id: UUID
    name: str
    enabled: bool
    trigger: str = Field(description="The trigger node's event.")
    version: int = Field(description="Goes up by one each time the graph changes.")
    graph: dict[str, Any] = Field(description="As stored: checked, edges always with `when`.")
    max_runs_per_hour: int
    max_runs_per_day: int
    waiting_runs: int = Field(description="Runs of this workflow waiting on a wait step now.")
    problems: list[str] = Field(description="What to fix, e.g. its team was deleted.")
    created_by: UUID | None
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime


class FlowVersionOut(BaseModel):
    version: int
    graph: dict[str, Any]
    created_at: datetime
    created_by_name: str | None


class ValidateBody(Body):
    graph: Graph


class ValidateOut(BaseModel):
    valid: bool
    trigger: str | None
    error: str | None = Field(description="The first problem found, for the canvas to show.")


class FlowTestBody(Body):
    ticket_id: UUID = Field(description="The ticket to try it on. Nothing on it changes.")
    graph: Graph | None = Field(
        default=None, description="An unsaved graph (POST /flows/test only)."
    )


class StepOut(BaseModel):
    node: str
    type: str
    outcome: str = Field(
        description="condition: yes or no; action: done or no change; wait: waiting (in the "
        "run log also next, replied or timeout when it went on)."
    )
    said: str | None = None


class WaitOut(BaseModel):
    node: str
    minutes: int
    until_reply: bool
    resume_at: datetime


class FlowTestOut(BaseModel):
    trigger: str
    steps: list[StepOut] = Field(description="The path it would take, until its first wait.")
    would_wait: WaitOut | None
    stopped: str | None = Field(description="Too many steps or waits; null when fine.")
    blocked: str | None = Field(description="A saved workflow only: the safeguard that would "
                                "stop it now, else null.")  # fmt: skip
    ticket_after: dict[str, Any]


def _out(view: FlowView) -> FlowOut:
    return FlowOut.model_validate({**dict(view.row), "problems": view.problems})


def _test_out(t: FlowTest) -> FlowTestOut:
    return FlowTestOut.model_validate(t.__dict__)


@router.get(
    "/automation/catalog",
    summary="The builder's palette: triggers, conditions, actions, node types (Admin)",
)
async def catalog(caller: CallerDep) -> dict[str, Any]:
    return flows.catalog(caller)


@router.get("/flows", response_model=list[FlowOut], summary="Every workflow, by name (Admin)")
async def list_flows(caller: CallerDep, sm: SessionmakerDep) -> list[FlowOut]:
    return [_out(v) for v in await flows.list_flows(sm, caller)]


@router.post(
    "/flows",
    response_model=FlowOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a new workflow; it starts turned off unless enabled is sent (Admin)",
)
async def create_flow(body: FlowIn, caller: CallerDep, sm: SessionmakerDep) -> FlowOut:
    view = await flows.create_flow(
        sm, caller, name=body.name, graph=body.graph, enabled=body.enabled,
        max_runs_per_hour=body.max_runs_per_hour, max_runs_per_day=body.max_runs_per_day,
    )  # fmt: skip
    return _out(view)


@router.post(
    "/flows/validate",
    response_model=ValidateOut,
    summary="Check a graph while it is drawn (shape only; nothing saved) (Admin)",
)
async def validate(body: ValidateBody, caller: CallerDep) -> ValidateOut:
    trigger, error = flows.validate(caller, body.graph)
    return ValidateOut(valid=error is None, trigger=trigger, error=error)


@router.post(
    "/flows/test",
    response_model=FlowTestOut,
    summary="Test mode: try an unsaved graph on a ticket; changes and sends nothing (Admin)",
)
async def test_draft(body: FlowTestBody, caller: CallerDep, sm: SessionmakerDep) -> FlowTestOut:
    if body.graph is None:
        raise Invalid("Send the graph to test (or use POST /flows/{id}/test).")
    return _test_out(await flows.test_flow(sm, caller, body.ticket_id, graph=body.graph))


@router.get("/flows/{flow_id}", response_model=FlowOut, summary="One workflow (Admin)")
async def get_flow(flow_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> FlowOut:
    return _out(await flows.get_flow(sm, caller, flow_id))


@router.patch(
    "/flows/{flow_id}",
    response_model=FlowOut,
    summary="Rename, turn on or off, change the graph (a new version) or the limits (Admin)",
)
async def change_flow(
    flow_id: UUID, body: FlowPatch, caller: CallerDep, sm: SessionmakerDep
) -> FlowOut:
    view = await flows.change_flow(
        sm, caller, flow_id, name=body.name, graph=body.graph, enabled=body.enabled,
        max_runs_per_hour=body.max_runs_per_hour, max_runs_per_day=body.max_runs_per_day,
    )  # fmt: skip
    return _out(view)


@router.delete(
    "/flows/{flow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workflow; its waiting runs are cancelled (Admin)",
)
async def delete_flow(flow_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await flows.delete_flow(sm, caller, flow_id)


@router.get(
    "/flows/{flow_id}/versions",
    response_model=list[FlowVersionOut],
    summary="Every graph the workflow has had, newest first (Admin)",
)
async def versions(flow_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> list[FlowVersionOut]:
    return [
        FlowVersionOut.model_validate(dict(r)) for r in await flows.versions(sm, caller, flow_id)
    ]


@router.post(
    "/flows/{flow_id}/test",
    response_model=FlowTestOut,
    summary="Test mode: try a saved workflow on a ticket; changes and sends nothing (Admin)",
)
async def test_saved(
    flow_id: UUID, body: FlowTestBody, caller: CallerDep, sm: SessionmakerDep
) -> FlowTestOut:
    return _test_out(await flows.test_flow(sm, caller, body.ticket_id, flow_id=flow_id))


# The run log


RunState = Literal["done", "waiting", "cancelled", "blocked", "failed"]


class RunOut(BaseModel):
    id: UUID
    kind: str = Field(description="rule or flow.")
    rule_id: UUID | None = Field(description="Null for a flow, or once the rule was deleted.")
    flow_id: UUID | None
    flow_version: int | None
    name: str = Field(description="The rule's or workflow's name when it ran.")
    ticket_id: UUID
    ticket_number: int
    trigger: str
    state: str = Field(description="done, waiting, cancelled, blocked or failed.")
    result: str = Field(description="What it did, why it was blocked or stopped.")
    steps: list[StepOut]
    error: str | None
    depth: int = Field(description="Loop depth: 1, or more when an automation's email or "
                       "snooze led to this event.")  # fmt: skip
    node_id: str | None = Field(description="Waiting runs: the wait node.")
    until_reply: bool
    resume_at: datetime | None = Field(description="Waiting runs: when it goes on at the latest.")
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class RunPageOut(BaseModel):
    runs: list[RunOut]
    next_cursor: str | None


def _run(row: Any) -> RunOut:
    data = dict(row)
    data["kind"] = "flow" if data["flow_version"] is not None else "rule"
    data["steps"] = [
        {"node": x.get("node", ""), "type": x.get("type", "action"),
         "outcome": x.get("outcome", ""), "said": x.get("said")}
        for x in data["steps"]
    ]  # fmt: skip
    return RunOut.model_validate(data)


@router.get(
    "/automation/runs",
    response_model=RunPageOut,
    summary="The run log, newest first; kept 30 days (Admin)",
)
async def list_runs(
    caller: CallerDep,
    sm: SessionmakerDep,
    rule_id: UUID | None = None,
    flow_id: UUID | None = None,
    ticket_id: UUID | None = None,
    state: RunState | None = None,
    trigger: Annotated[str | None, Query(max_length=40)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=paging.MAX_LIMIT)] = paging.DEFAULT_LIMIT,
) -> RunPageOut:
    page = await run_log.list_runs(
        sm, caller, rule_id=rule_id, flow_id=flow_id, ticket_id=ticket_id, state=state,
        trigger=trigger, cursor=cursor, limit=limit,
    )  # fmt: skip
    return RunPageOut(runs=[_run(r) for r in page.runs], next_cursor=page.next_cursor)


@router.get("/automation/runs/{run_id}", response_model=RunOut, summary="One run (Admin)")
async def get_run(run_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> RunOut:
    return _run(await run_log.get_run(sm, caller, run_id))


@router.post(
    "/automation/runs/{run_id}/cancel",
    response_model=RunOut,
    summary="Stop a waiting workflow run (Admin)",
)
async def cancel_run(run_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> RunOut:
    return _run(await run_log.cancel_run(sm, caller, run_id))
