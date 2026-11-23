"""Workflows (feature-gap plan 5.2, 5.3): the visual builder's flows, managed like rules
(the `settings` right; one that calls a webhook is an Admin's), and their test mode.
The graph rules are in domain/flows.py; running is service/automation.py.

- A flow is saved only when its graph checks out (`domain.flows.parse_graph`) and what it
  names exists (types, teams, members, webhooks, segments, contact fields). It starts
  turned off unless `enabled` is sent.
- Each change of the graph is a new version (kept in `hd_flow_version`); runs already
  waiting go on with the version they started on.
- Turning a flow off or deleting it cancels its waiting runs.
- Create, change (name, graph, on/off, limits) and delete are audited (area `flows`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import automation as guard
from cedesk.helpdesk.domain import flows as rules
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.sla import SlaState
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment, Status
from cedesk.helpdesk.repo import automation as repo
from cedesk.helpdesk.repo import rules as rule_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, automation, scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "Workflow not found."
TAKEN = "Another workflow already has that name."
Row = Any


@dataclass(frozen=True)
class FlowView:
    row: Row
    problems: list[str]


def catalog(caller: Caller) -> dict[str, Any]:
    """The builder's palette: triggers, conditions, actions and node types."""
    require(caller, Action.settings)
    return rules.catalog(
        channels=[c.value for c in Channel],
        statuses=[s.value for s in Status],
        sentiments=[s.value for s in Sentiment],
        sla_states=[s.value for s in SlaState],
        priorities=[p.value for p in Priority],
    )


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= engine.NAME_MAX:
        raise Invalid(f"A workflow name is 1-{engine.NAME_MAX} characters.")
    return name


def parse(raw: Any) -> rules.Graph:
    try:
        return rules.parse_graph(raw)
    except rules.FlowError as exc:
        raise Invalid(str(exc)) from exc


def _limits(per_hour: int, per_day: int) -> tuple[int, int]:
    try:
        return guard.check_limits(per_hour, per_day)
    except guard.LimitError as exc:
        raise Invalid(str(exc)) from exc


async def _views(s: AsyncSession, rows: Sequence[Row]) -> list[FlowView]:
    types = await rule_repo.names(s, "hd_ticket_type")
    teams = await rule_repo.names(s, "hd_team")
    found = []
    for row in rows:
        try:
            graph = rules.parse_graph(row["graph"])
        except rules.FlowError:
            found.append(FlowView(row, ["Its graph no longer reads; save it again."]))
            continue
        conditions, actions = graph.conditions(), graph.actions()
        known = await automation.known(s, conditions, actions)
        problems = engine.action_problems(actions, types, teams, known)
        problems += engine.condition_problems(conditions, known)
        found.append(FlowView(row, list(dict.fromkeys(problems))))
    return found


async def _get(s: AsyncSession, flow_id: UUID, *, lock: bool = False) -> Row:
    row = await repo.get_flow(s, flow_id, lock=lock)
    if row is None:
        raise NotFound(NOT_FOUND)
    return row


async def list_flows(sm: Sessionmaker, caller: Caller) -> list[FlowView]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _views(s, await repo.all_flows(s))


async def get_flow(sm: Sessionmaker, caller: Caller, flow_id: UUID) -> FlowView:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return (await _views(s, [await _get(s, flow_id)]))[0]


def validate(caller: Caller, graph: Any) -> tuple[str | None, str | None]:
    """The builder's live check, before saving: (trigger, None) or (None, the problem).
    Only the graph's shape; what it names is checked on save."""
    require(caller, Action.settings)
    try:
        return rules.parse_graph(graph).event.value, None
    except rules.FlowError as exc:
        return None, str(exc)


def _audited(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": row["name"], "enabled": row["enabled"], "trigger": row["trigger"],
        "version": row["version"], "max_runs_per_hour": row["max_runs_per_hour"],
        "max_runs_per_day": row["max_runs_per_day"],
        "nodes": len(row["graph"].get("nodes", [])),
    }  # fmt: skip


async def create_flow(
    sm: Sessionmaker,
    caller: Caller,
    *,
    name: str,
    graph: Any,
    enabled: bool = False,
    max_runs_per_hour: int = guard.DEFAULT_PER_HOUR,
    max_runs_per_day: int = guard.DEFAULT_PER_DAY,
) -> FlowView:
    require(caller, Action.settings)
    clean = _name(name)
    parsed = parse(graph)
    per_hour, per_day = _limits(max_runs_per_hour, max_runs_per_day)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            if await repo.count_flows(s) >= rules.MAX_FLOWS:
                raise Invalid(f"A workspace can have at most {rules.MAX_FLOWS} workflows.")
            await automation.check_saved(s, caller, parsed.conditions(), parsed.actions())
            flow_id = await repo.insert_flow(
                s, caller.workspace_id, name=clean, enabled=enabled,
                trigger=parsed.event.value, graph=rules.stored_graph(parsed),
                max_runs_per_hour=per_hour, max_runs_per_day=per_day,
                created_by=caller.member_id,
            )  # fmt: skip
            row = await _get(s, flow_id)
            await audit.record(
                s, caller, Area.flows, "created", f"Workflow {clean}", target_id=flow_id,
                detail=_audited(row),
            )  # fmt: skip
            return (await _views(s, [row]))[0]
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def change_flow(
    sm: Sessionmaker,
    caller: Caller,
    flow_id: UUID,
    *,
    name: str | None = None,
    graph: Any = None,
    enabled: bool | None = None,
    max_runs_per_hour: int | None = None,
    max_runs_per_day: int | None = None,
) -> FlowView:
    """Only what is sent changes. A new graph is a new version."""
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            before = await _get(s, flow_id, lock=True)
            values: dict[str, Any] = {}
            if name is not None:
                values["name"] = _name(name)
            if graph is not None:
                parsed = parse(graph)
                await automation.check_saved(s, caller, parsed.conditions(), parsed.actions())
                stored = rules.stored_graph(parsed)
                if stored != before["graph"]:
                    version = int(before["version"]) + 1
                    values |= {"graph": stored, "trigger": parsed.event.value, "version": version}
                    await repo.add_version(
                        s, caller.workspace_id, flow_id, version, stored, caller.member_id
                    )
            else:
                automation.may_call_webhooks(caller, parse(before["graph"]).actions())
            if enabled is not None:
                values["enabled"] = enabled
            if max_runs_per_hour is not None or max_runs_per_day is not None:
                values["max_runs_per_hour"], values["max_runs_per_day"] = _limits(
                    before["max_runs_per_hour"] if max_runs_per_hour is None else max_runs_per_hour,
                    before["max_runs_per_day"] if max_runs_per_day is None else max_runs_per_day,
                )
            await repo.update_flow(s, flow_id, values)
            if enabled is False and before["enabled"]:
                await repo.cancel_waiting(s, flow_id=flow_id, reason="The workflow was turned off.")
            row = await _get(s, flow_id)
            old, new = _audited(before), _audited(row)
            changes = {k: [old[k], new[k]] for k in old if old[k] != new[k]}
            if changes:
                action = "changed"
                if set(changes) == {"enabled"}:
                    action = "enabled" if row["enabled"] else "disabled"
                await audit.record(
                    s, caller, Area.flows, action, f"Workflow {row['name']}", target_id=flow_id,
                    detail={"changes": changes},
                )  # fmt: skip
            return (await _views(s, [row]))[0]
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def delete_flow(sm: Sessionmaker, caller: Caller, flow_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _get(s, flow_id, lock=True)
        cancelled = await repo.cancel_waiting(
            s, flow_id=flow_id, reason="The workflow was deleted."
        )
        await repo.delete_flow(s, flow_id)
        await audit.record(
            s, caller, Area.flows, "deleted", f"Workflow {before['name']}", target_id=flow_id,
            detail=_audited(before) | {"waiting_runs_cancelled": cancelled},
        )  # fmt: skip


async def versions(sm: Sessionmaker, caller: Caller, flow_id: UUID) -> Sequence[Row]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        await _get(s, flow_id)
        return await repo.versions(s, flow_id)


# Test mode (plan 5.5)


@dataclass(frozen=True)
class FlowTest:
    trigger: str
    steps: list[dict[str, Any]]  # node, type, outcome, said, reason
    would_wait: dict[str, Any] | None  # node, minutes, until_reply, resume_at
    stopped: str | None
    blocked: str | None
    ticket_after: dict[str, Any]


async def test_flow(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    flow_id: UUID | None = None,
    graph: Any = None,
) -> FlowTest:
    """A saved flow (`flow_id`, its current version) or an unsaved graph against one
    ticket, as if its trigger happened now: the path it would take until its first wait
    or its end. Read-only: nothing is written, sent or logged."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        limits: tuple[int, int] | None = None
        if flow_id is not None:
            row = await _get(s, flow_id)
            parsed = parse(row["graph"])
            limits = (row["max_runs_per_hour"], row["max_runs_per_day"])
        else:
            parsed = parse(graph)
        await scope.check(s, caller, ticket_id, edit=False)
        detail = await ticket_repo.ticket_detail(s, ticket_id)
        if detail is None:
            raise NotFound("Ticket not found.")
        now = datetime.now(UTC)
        conditions, actions = parsed.conditions(), parsed.actions()
        facts = await automation.build_facts(s, detail, parsed.event, conditions, now=now)
        targets = await automation.build_targets(s, actions)
        state = await automation.start_state(s, detail, actions, conditions, always_followers=True)
        walked = rules.walk(parsed, facts, state, targets, reasons=True)
        reason = None
        if flow_id is not None and limits is not None:
            depth = await automation.event_depth(s, ticket_id, parsed.event, now)
            reason = await automation.blocked(
                s, ticket_id, flow_id=flow_id, per_hour=limits[0], per_day=limits[1],
                depth=depth, now=now,
            )  # fmt: skip
    final = walked.state
    wait = None
    if walked.wait_at is not None:
        wait = {
            "node": walked.wait_at, "minutes": walked.wait_minutes,
            "until_reply": walked.until_reply,
            "resume_at": now + timedelta(minutes=walked.wait_minutes),
        }  # fmt: skip
    return FlowTest(
        trigger=parsed.event.value,
        steps=[x.stored() | {"said": x.said, "reason": x.reason} for x in walked.steps],
        would_wait=wait,
        stopped=walked.stopped,
        blocked=reason,
        ticket_after={
            "status": final.status.value,
            "priority": final.priority.value,
            "type_id": final.type_id,
            "team_id": final.team_id,
            "tags": list(final.tags),
            "snoozed": final.snoozed,
            "followers": sorted(str(m) for m in final.followers),
        },
    )
