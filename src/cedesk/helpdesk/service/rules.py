"""Automation rules (md W15, W16; feature-gap plan 5.1, 5.5): Admins manage them; tickets
run them.

`apply` runs inside the transaction that creates a ticket or takes a customer reply (or
the worker's, for SLA breaches and wake-ups). It runs the rules, then the workflows
(service/automation.py, with the safeguards and the run log), changes the ticket and
writes one "Rule applied" line per rule; the caller then routes the ticket and writes the
events, so a new ticket gets one `ticket.created` with its final state.

Test mode (`test_rule`) runs a saved or unsaved rule against one ticket and changes
nothing: which conditions matched, what each action would do, and whether a safeguard
would stop it.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import automation as guard
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import rules as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.rules import Row
from cedesk.helpdesk.service import audit, automation, idempotency, scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.automation import RuleRun
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk")
TAKEN = "Another rule already has that name."
NO_TICKET = "Ticket not found."

__all__ = ["RuleRun", "apply", "retyped"]

retyped = automation.retyped  # macros change the type the same way


@dataclass(frozen=True)
class RuleView:
    row: Row
    problems: list[str]


_AUDITED = (
    "name", "enabled", "event", "conditions", "actions", "max_runs_per_hour", "max_runs_per_day"
)  # fmt: skip


def parse(name: str, event: str, conditions: Sequence[Any], actions: Sequence[Any]) -> engine.Rule:
    """A rule as an Admin sent it, checked (also used by the sandbox)."""
    if not all(isinstance(x, Mapping) for x in [*conditions, *actions]):
        raise Invalid("Each condition and action is an object with a `kind`.")
    name = " ".join(name.split())
    if not 1 <= len(name) <= engine.NAME_MAX:
        raise Invalid(f"A rule name is 1-{engine.NAME_MAX} characters.")
    try:
        return engine.parse_rule(UUID(int=0), name, event, conditions, actions)
    except engine.RuleError as exc:
        raise Invalid(str(exc)) from exc


def _limits(per_hour: int, per_day: int) -> tuple[int, int]:
    try:
        return guard.check_limits(per_hour, per_day)
    except guard.LimitError as exc:
        raise Invalid(str(exc)) from exc


def _from_row(row: Row) -> engine.Rule:
    return automation.rule_from_row(row)


async def _views(s: AsyncSession, rows: Sequence[Row]) -> list[RuleView]:
    """With what to fix. A stored rule that no longer reads is listed, not a 500, so an
    Admin can still open the page and fix or delete it."""
    types = await repo.names(s, "hd_ticket_type")
    teams = await repo.names(s, "hd_team")
    found = []
    for row in rows:
        try:
            rule = _from_row(row)
            known = await automation.known(s, rule.conditions, rule.actions)
            problems = engine.problems(rule, types, teams, known)
        except engine.RuleError:
            problems = ["Its conditions or actions no longer read; save it again."]
        found.append(RuleView(row, problems))
    return found


async def _view(s: AsyncSession, row: Row) -> RuleView:
    return (await _views(s, [row]))[0]


# Settings (Admin)


async def list_rules(sm: Sessionmaker, caller: Caller) -> list[RuleView]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _views(s, await repo.list_rules(s))


async def create_rule(
    sm: Sessionmaker,
    caller: Caller,
    *,
    name: str,
    enabled: bool,
    event: str,
    conditions: Sequence[Any],
    actions: Sequence[Any],
    max_runs_per_hour: int = guard.DEFAULT_PER_HOUR,
    max_runs_per_day: int = guard.DEFAULT_PER_DAY,
    idem: Idempotency | None = None,
) -> RuleView:
    """`idem`: a repeat with the same key returns the rule the first one made."""
    require(caller, Action.settings)
    rule = parse(name, event, conditions, actions)
    limits = _limits(max_runs_per_hour, max_runs_per_day)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            earlier = await idempotency.begin(s, caller, idem)
            if earlier is not None:
                found = await repo.get_rule(s, earlier)
                if found is None:
                    raise NotFound("The rule this key made has since been deleted.")
                return await _view(s, found)
            row = await create_in(s, caller, rule, enabled, limits)
            await idempotency.remember(s, caller, idem, row["id"])
            return await _view(s, row)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def create_in(
    s: AsyncSession,
    caller: Caller,
    rule: engine.Rule,
    enabled: bool,
    limits: tuple[int, int] = (guard.DEFAULT_PER_HOUR, guard.DEFAULT_PER_DAY),
) -> Row:
    """Adds a checked rule (last in order) and its audit entry, in the caller's
    transaction."""
    # A soft cap: two Admins adding at the same moment can pass it by one each.
    if await repo.count(s) >= engine.MAX_RULES:
        raise Invalid(f"A workspace can have at most {engine.MAX_RULES} rules.")
    await automation.check_saved(s, caller, rule.conditions, rule.actions)
    rule_id = await repo.insert_rule(
        s,
        caller.workspace_id,
        name=rule.name,
        enabled=enabled,
        event=rule.event.value,
        conditions=[engine.stored_condition(c) for c in rule.conditions],
        actions=[engine.stored_action(a) for a in rule.actions],
        created_by=caller.member_id,
        max_runs_per_hour=limits[0],
        max_runs_per_day=limits[1],
    )
    assert rule_id is not None
    row = await repo.get_rule(s, rule_id)
    assert row is not None
    await audit.record(
        s,
        caller,
        Area.rules,
        "created",
        f"Rule {rule.name}",
        target_id=rule_id,
        detail=audit_rules.snapshot(row, _AUDITED[1:]),
    )
    return row


async def change_rule(
    sm: Sessionmaker,
    caller: Caller,
    rule_id: UUID,
    *,
    name: str | None,
    enabled: bool | None,
    event: str | None,
    conditions: Sequence[Any] | None,
    actions: Sequence[Any] | None,
    max_runs_per_hour: int | None = None,
    max_runs_per_day: int | None = None,
) -> RuleView:
    """Only what is sent changes; the result is checked as a whole rule."""
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            current = await repo.get_rule(s, rule_id)
            if current is None:
                raise NotFound("Rule not found.")
            rule = parse(
                current["name"] if name is None else name,
                current["event"] if event is None else event,
                current["conditions"] if conditions is None else conditions,
                current["actions"] if actions is None else actions,
            )
            limits = None
            if max_runs_per_hour is not None or max_runs_per_day is not None:
                limits = _limits(
                    current["max_runs_per_hour"]
                    if max_runs_per_hour is None
                    else max_runs_per_hour,
                    current["max_runs_per_day"] if max_runs_per_day is None else max_runs_per_day,
                )
            row = await change_in(
                s, caller, current, rule, enabled,
                check_targets=actions is not None or conditions is not None, limits=limits,
            )  # fmt: skip
            return await _view(s, row)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def change_in(
    s: AsyncSession,
    caller: Caller,
    current: Row,
    rule: engine.Rule,
    enabled: bool | None,
    *,
    check_targets: bool = True,
    limits: tuple[int, int] | None = None,
) -> Row:
    """Saves a checked rule over `current` and its audit entry, in the caller's
    transaction. `enabled` None keeps it."""
    if check_targets:
        await automation.check_saved(s, caller, rule.conditions, rule.actions)
    else:
        automation.may_call_webhooks(caller, rule.actions)  # a webhook rule is an Admin's
    await repo.update_rule(
        s,
        current["id"],
        name=rule.name,
        enabled=enabled,
        event=rule.event.value,
        conditions=[engine.stored_condition(c) for c in rule.conditions],
        actions=[engine.stored_action(a) for a in rule.actions],
        max_runs_per_hour=limits[0] if limits else None,
        max_runs_per_day=limits[1] if limits else None,
    )
    row = await repo.get_rule(s, current["id"])
    assert row is not None
    await audit.record(
        s,
        caller,
        Area.rules,
        "changed",
        f"Rule {rule.name}",
        target_id=current["id"],
        detail={"changes": audit_rules.changes(current, row, fields=_AUDITED)},
    )
    return row


async def delete_rule(sm: Sessionmaker, caller: Caller, rule_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.get_rule(s, rule_id)
        if current is None:
            raise NotFound("Rule not found.")
        await delete_in(s, caller, current)


async def delete_in(s: AsyncSession, caller: Caller, current: Row) -> None:
    if not await repo.delete_rule(s, current["id"]):
        raise NotFound("Rule not found.")
    await audit.record(
        s,
        caller,
        Area.rules,
        "deleted",
        f"Rule {current['name']}",
        target_id=current["id"],
        detail=audit_rules.snapshot(current, _AUDITED[1:]),
    )


async def reorder_rules(
    sm: Sessionmaker, caller: Caller, rule_ids: Sequence[UUID]
) -> list[RuleView]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = set(await repo.lock_rules(s))
        if set(rule_ids) != current or len(rule_ids) != len(current):
            raise Invalid("Send every rule id exactly once, in the new order.")
        await repo.reorder_rules(s, rule_ids)
        await audit.record(s, caller, Area.rules, "reordered", "Rule order")
        return await _views(s, await repo.list_rules(s))


# Test mode (plan 5.5)


@dataclass(frozen=True)
class RuleTest:
    rule_name: str
    event: str
    matched: bool
    conditions: list[dict[str, Any]]  # each: {condition, matched}
    actions: list[dict[str, Any]]  # each: {action, would}; would None: no change
    blocked: str | None  # what a safeguard would say now
    ticket_after: dict[str, Any]  # status, priority, type_id, team_id, tags, snoozed


async def test_rule(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    rule_id: UUID | None = None,
    draft: Mapping[str, Any] | None = None,
) -> RuleTest:
    """A saved rule (`rule_id`) or an unsaved one (`draft`: name, event, conditions,
    actions) against one ticket, as if its event happened now. Read-only: nothing is
    written, sent or logged."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if rule_id is not None:
            row = await repo.get_rule(s, rule_id)
            if row is None:
                raise NotFound("Rule not found.")
            try:
                rule = _from_row(row)
            except engine.RuleError as exc:
                raise Invalid(str(exc)) from exc
            per_hour, per_day = row["max_runs_per_hour"], row["max_runs_per_day"]
        else:
            assert draft is not None
            rule = parse(
                str(draft.get("name") or "Test"), str(draft.get("event")),
                list(draft.get("conditions") or []), list(draft.get("actions") or []),
            )  # fmt: skip
            per_hour, per_day = guard.DEFAULT_PER_HOUR, guard.DEFAULT_PER_DAY
        await scope.check(s, caller, ticket_id, edit=False)
        detail = await ticket_repo.ticket_detail(s, ticket_id)  # read only: no lock
        if detail is None:
            raise NotFound(NO_TICKET)
        now = datetime.now(UTC)
        facts = await automation.build_facts(s, detail, rule.event, rule.conditions, now=now)
        targets = await automation.build_targets(s, rule.actions)
        state = await automation.start_state(s, detail, rule.actions, rule.conditions)
        explained = engine.explain(rule, facts, state, targets)
        reason = None
        if explained.matched:
            depth = await automation.event_depth(s, ticket_id, rule.event, now)
            reason = await automation.blocked(
                s, ticket_id, rule_id=rule_id, per_hour=per_hour, per_day=per_day, depth=depth,
                now=now,
            ) if rule_id is not None else None  # fmt: skip
    final = explained.state
    return RuleTest(
        rule_name=rule.name,
        event=rule.event.value,
        matched=explained.matched,
        conditions=[{"condition": c, "matched": m} for c, m in explained.conditions],
        actions=[{"action": a, "would": said} for a, said in explained.actions],
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


# Running on a ticket


async def any_enabled(s: AsyncSession, event: engine.Event) -> bool:
    return await automation.any_enabled(s, event)


async def apply(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    event: engine.Event,
    *,
    to_address: str | None = None,
    new_message: str | None = None,
) -> RuleRun | None:
    """Runs the enabled rules and workflows for `event` on a ticket, inside the caller's
    transaction, with the ticket row locked. Saves the result and writes the lines. None:
    nothing ran. Routing and events are the caller's job."""
    return await automation.run_event(
        s, workspace_id, ticket_id, event, to_address=to_address, new_message=new_message
    )
