"""Automation rules (md W15, W16): Admins manage them; tickets run them.

`apply` runs inside the transaction that creates a ticket or takes a customer reply. It
changes the ticket and writes one "Rule applied" line per rule; the caller then routes the
ticket and writes the events, so a new ticket gets one `ticket.created` with its final state.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment, Status, carry_over
from cedesk.helpdesk.repo import catalog
from cedesk.helpdesk.repo import rules as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.rules import Row
from cedesk.helpdesk.service import children, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.catalog import field_defs
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk")
TAKEN = "Another rule already has that name."


@dataclass(frozen=True)
class RuleView:
    row: Row
    problems: list[str]


@dataclass(frozen=True)
class RuleRun:
    before: Mapping[Any, Any]  # the ticket as it was (lock_ticket columns)
    after: dict[str, Any]  # as saved
    team_changed: bool


def _parse(name: str, event: str, conditions: Sequence[Any], actions: Sequence[Any]) -> engine.Rule:
    if not all(isinstance(x, Mapping) for x in [*conditions, *actions]):
        raise Invalid("Each condition and action is an object with a `kind`.")
    name = " ".join(name.split())
    if not 1 <= len(name) <= engine.NAME_MAX:
        raise Invalid(f"A rule name is 1-{engine.NAME_MAX} characters.")
    try:
        return engine.parse_rule(UUID(int=0), name, event, conditions, actions)
    except engine.RuleError as exc:
        raise Invalid(str(exc)) from exc


def _from_row(row: Row) -> engine.Rule:
    return engine.parse_rule(
        row["id"], row["name"], row["event"], row["conditions"], row["actions"]
    )


async def _check_targets(s: AsyncSession, rule: engine.Rule) -> None:
    """A new or changed rule may only point at a type and team that exist now."""
    types = await repo.names(s, "hd_ticket_type")
    teams = await repo.names(s, "hd_team")
    if engine.problems(rule, types, teams):
        raise Invalid("A rule can only set a ticket type and team that exist.")


async def _views(s: AsyncSession, rows: Sequence[Row]) -> list[RuleView]:
    """With what to fix. A stored rule that no longer reads is listed, not a 500, so an
    Admin can still open the page and fix or delete it."""
    types = await repo.names(s, "hd_ticket_type")
    teams = await repo.names(s, "hd_team")
    found = []
    for row in rows:
        try:
            problems = engine.problems(_from_row(row), types, teams)
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
) -> RuleView:
    require(caller, Action.settings)
    rule = _parse(name, event, conditions, actions)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            # A soft cap: two Admins adding at the same moment can pass it by one each.
            if await repo.count(s) >= engine.MAX_RULES:
                raise Invalid(f"A workspace can have at most {engine.MAX_RULES} rules.")
            await _check_targets(s, rule)
            rule_id = await repo.insert_rule(
                s,
                caller.workspace_id,
                name=rule.name,
                enabled=enabled,
                event=rule.event.value,
                conditions=[engine.stored_condition(c) for c in rule.conditions],
                actions=[engine.stored_action(a) for a in rule.actions],
                created_by=caller.member_id,
            )
            assert rule_id is not None
            row = await repo.get_rule(s, rule_id)
            assert row is not None
            return await _view(s, row)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


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
) -> RuleView:
    """Only what is sent changes; the result is checked as a whole rule."""
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            current = await repo.get_rule(s, rule_id)
            if current is None:
                raise NotFound("Rule not found.")
            rule = _parse(
                current["name"] if name is None else name,
                current["event"] if event is None else event,
                current["conditions"] if conditions is None else conditions,
                current["actions"] if actions is None else actions,
            )
            if actions is not None:
                await _check_targets(s, rule)
            await repo.update_rule(
                s,
                rule_id,
                name=rule.name,
                enabled=enabled,
                event=rule.event.value,
                conditions=[engine.stored_condition(c) for c in rule.conditions],
                actions=[engine.stored_action(a) for a in rule.actions],
            )
            row = await repo.get_rule(s, rule_id)
            assert row is not None
            return await _view(s, row)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def delete_rule(sm: Sessionmaker, caller: Caller, rule_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.delete_rule(s, rule_id):
            raise NotFound("Rule not found.")


async def reorder_rules(
    sm: Sessionmaker, caller: Caller, rule_ids: Sequence[UUID]
) -> list[RuleView]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = set(await repo.lock_rules(s))
        if set(rule_ids) != current or len(rule_ids) != len(current):
            raise Invalid("Send every rule id exactly once, in the new order.")
        await repo.reorder_rules(s, rule_ids)
        return await _views(s, await repo.list_rules(s))


# Running on a ticket


async def _enabled(s: AsyncSession, event: engine.Event) -> list[engine.Rule]:
    found = []
    for row in await repo.enabled_rules(s, event.value):
        try:
            found.append(_from_row(row))
        except engine.RuleError:
            # Saved rules were checked; one that no longer reads is skipped, not fatal.
            logger.warning("skipping a rule that does not read: %s", row["id"])
    return found


async def any_enabled(s: AsyncSession, event: engine.Event) -> bool:
    return bool(await _enabled(s, event))


async def retyped(
    s: AsyncSession, old: UUID | None, new: UUID | None, fields: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """A rule or macro changed the type: values that don't fit the new type are dropped,
    each with a line. Neither can fill fields, so a required field stays empty until an
    agent fills it."""
    if new is None or new == old:
        return fields, []
    type_row = await catalog.get_type(s, new)
    assert type_row is not None
    kept, dropped = carry_over(field_defs(type_row["fields"]), fields)
    old_type = await catalog.get_type(s, old) if old else None
    labels = {d.key: d.label for d in field_defs(old_type["fields"])} if old_type else {}
    return kept, [
        f"{labels.get(key, key)} cleared (not in {type_row['name']})" for key in sorted(dropped)
    ]


async def apply(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    event: engine.Event,
    *,
    to_address: str | None = None,
    new_message: str | None = None,
) -> RuleRun | None:
    """Runs the enabled rules for `event` on a ticket, inside the caller's transaction,
    with the ticket row locked. Saves the result and writes the lines. None: no rule
    matched. Routing and events are the caller's job.

    Text conditions read the subject and the customer's first message on a new ticket,
    and only `new_message` on a customer reply, so a word said once does not match every
    later reply."""
    rules = await _enabled(s, event)
    if not rules:
        return None
    before = await ticket_repo.lock_ticket(s, ticket_id)
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    if before is None or detail is None:
        raise NotFound("Ticket not found.")
    types = await repo.names(s, "hd_ticket_type")
    teams = await repo.names(s, "hd_team")
    facts = engine.Facts(
        text=(
            new_message
            if event is engine.Event.customer_replied
            else f"{detail['subject']} {await repo.customer_text(s, ticket_id)}"
        )
        or "",
        channel=Channel(detail["channel"]),
        to_address=to_address or await repo.to_address(s, ticket_id),
        sentiment=Sentiment(detail["sentiment"]) if detail["sentiment"] else None,
        sla=(
            sla.state(detail, await sla.config(s)).current.state
            if engine.needs_sla(rules)
            else None
        ),
    )
    state = engine.State(
        type_id=before["type_id"],
        team_id=before["team_id"],
        priority=Priority(before["priority"]),
        status=Status(before["status"]),
        tags=tuple(before["tags"]),
    )
    final, applied = engine.run(rules, event, facts, state, types=types, teams=teams)
    if not applied:
        return None

    after: dict[str, Any] = {k: before[k] for k in ("subject", "assignee_id", "reopen_count")} | {
        "status": final.status.value,
        "priority": final.priority.value,
        "type_id": final.type_id,
        "team_id": final.team_id,
        "tags": list(final.tags),
        "fields": dict(before["fields"]),
    }
    lines = [engine.line(a) for a in applied]
    after["fields"], cleared = await retyped(s, before["type_id"], final.type_id, after["fields"])
    lines += cleared
    if before["status"] == Status.resolved.value and final.status is not Status.resolved:
        after["reopen_count"] += 1
    resolving = final.status is Status.resolved and before["status"] != Status.resolved.value
    if resolving:
        # A rule can't resolve a parent with an open child (X4): the other steps still run.
        try:
            await children.refuse_open_children(s, ticket_id)
        except Conflict as exc:
            after["status"] = before["status"]
            lines.append(f"Status not changed: {exc}")
            resolving = False

    await ticket_repo.update_ticket(s, ticket_id, after)
    if resolving:
        await children.child_resolved(s, workspace_id, before, "a rule", None)
    for body in lines:
        await ticket_repo.insert_message(
            s,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            kind="system",
            body=body,
            author_member_id=None,
            author_name=None,
        )
    return RuleRun(before=before, after=after, team_changed=final.team_id != before["team_id"])
