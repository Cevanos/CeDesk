"""Running automation on a ticket (feature-gap plan phase 5): rules and flows, with the
safeguards and the run log. Rules are in domain/rules.py, flows in domain/flows.py, the
safeguards in domain/automation.py.

One ticket event (`run_event`, called through `service.rules.apply` inside the
transaction that made the event):
1. The enabled rules for the event, in order, then the enabled flows with that trigger.
2. Each rule whose conditions match, and each flow, is checked against the safeguards
   (loop depth, per-ticket cap, hourly and daily limits): a blocked one is logged and
   changes nothing.
3. A rule runs its actions; a flow walks its graph until a wait step or its end. Later
   rules and flows see what earlier ones changed. Each one that did something (or
   waits) is logged and writes a line on the ticket.
4. The ticket is saved once, then the side effects run, in the same transaction: snooze,
   followers, the customer email (queued like any reply), webhook deliveries (queued for
   the webhook sender).

A waiting flow run is resumed by the worker (`resume_due`, every worker tick) when its time
comes, or as soon as the customer replies when it waits for that. Each resume locks the
run (`FOR UPDATE SKIP LOCKED`) and does its steps, effects and run update in one
transaction, so a run is never resumed twice and nothing is sent twice. A run whose flow
was deleted or turned off, or whose ticket was resolved or merged, is cancelled when it
comes due.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import automation as guard
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain import flows as flow_rules
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain import webhooks as webhook_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.automation import RunState
from cedesk.helpdesk.domain.permissions import Action, Scope
from cedesk.helpdesk.domain.placeholders import render
from cedesk.helpdesk.domain.seeds import DEMO_SOURCE
from cedesk.helpdesk.domain.tickets import Channel, Priority, Sentiment, Status, carry_over
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import automation as repo
from cedesk.helpdesk.repo import catalog, outbox
from cedesk.helpdesk.repo import contacts as contact_repo
from cedesk.helpdesk.repo import rules as rule_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo import webhooks as webhook_repo
from cedesk.helpdesk.service import children, followers, routing, sla
from cedesk.helpdesk.service import email as email_service
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.catalog import field_defs
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk.automation")

WEB_FORM_SOURCE = "web_form"  # as service/messages.py: web form tickets are emailed
WEBHOOK_EVENT = "automation.webhook"
_ID_SPACE = uuid5(NAMESPACE_URL, "https://cedesk/automation-webhook")


@dataclass(frozen=True)
class RuleRun:
    before: Mapping[Any, Any]  # the ticket as it was (lock_ticket columns)
    after: dict[str, Any]  # as saved
    team_changed: bool


@dataclass(frozen=True)
class LoadedRule:
    rule: engine.Rule
    per_hour: int
    per_day: int


@dataclass(frozen=True)
class LoadedFlow:
    id: UUID
    name: str
    version: int
    graph: flow_rules.Graph
    per_hour: int
    per_day: int


@dataclass(frozen=True)
class Who:
    """The run an effect belongs to (for the webhook body and its delivery id)."""

    run_id: UUID
    kind: str  # rule or flow
    source_id: UUID
    name: str
    step: int  # the effect's number in the run, for a stable delivery id


@dataclass
class Pending:
    """What the runs of one event (or one resume) did, to save and carry out."""

    lines: list[str] = field(default_factory=list)
    effects: list[tuple[engine.Effect, Who]] = field(default_factory=list)


# Loading


def rule_from_row(row: Mapping[Any, Any]) -> engine.Rule:
    return engine.parse_rule(
        row["id"], row["name"], row["event"], row["conditions"], row["actions"]
    )


async def enabled_rules(s: AsyncSession, event: engine.Event) -> list[LoadedRule]:
    found = []
    for row in await rule_repo.enabled_rules(s, event.value):
        try:
            found.append(
                LoadedRule(rule_from_row(row), row["max_runs_per_hour"], row["max_runs_per_day"])
            )
        except engine.RuleError:
            # Saved rules were checked; one that no longer reads is skipped, not fatal.
            logger.warning("skipping a rule that does not read: %s", row["id"])
    return found


async def enabled_flows(s: AsyncSession, event: engine.Event) -> list[LoadedFlow]:
    found = []
    for row in await repo.enabled_flows(s, event.value):
        try:
            graph = flow_rules.parse_graph(row["graph"])
        except flow_rules.FlowError:
            logger.warning("skipping a flow that does not read: %s", row["id"])
            continue
        found.append(
            LoadedFlow(
                row["id"],
                str(row["name"]),
                int(row["version"]),
                graph,
                int(row["max_runs_per_hour"]),
                int(row["max_runs_per_day"]),
            )
        )
    return found


async def any_enabled(s: AsyncSession, event: engine.Event) -> bool:
    return bool(await rule_repo.enabled_rules(s, event.value)) or bool(
        await repo.enabled_flows(s, event.value)
    )


# Checking what a rule or flow points at, when it is saved

WEBHOOK_ADMIN = "Only an Admin can make an automation call a webhook."


async def known(s: AsyncSession, conditions: Sequence[engine.Condition],
                actions: Sequence[engine.Action]) -> engine.Known:  # fmt: skip
    """What the conditions and actions name that still exists (for their problems)."""
    members = sorted({a.member_id for a in actions if isinstance(a, engine.AddFollower)})
    hooks = sorted({a.webhook_id for a in actions if isinstance(a, engine.CallWebhook)})
    segments = sorted(engine.segments_named(conditions))
    asks_fields = any(isinstance(c, engine.ContactFieldIs) for c in conditions)
    return engine.Known(
        members=await repo.active_member_ids(s, members) if members else frozenset(),
        webhooks=await repo.ids_in(s, "hd_webhook", hooks) if hooks else frozenset(),
        segments=await repo.ids_in(s, "hd_segment", segments) if segments else frozenset(),
        fields=await repo.field_keys(s) if asks_fields else frozenset(),
    )


def may_call_webhooks(caller: Caller, actions: Sequence[engine.Action]) -> None:
    """A webhook sends ticket data out of CeDesk: as managing webhooks, Admins only, and
    never through an API key."""
    if any(isinstance(a, engine.CallWebhook) for a in actions):
        require(caller, Action.manage_access)
        if caller.api_key_id is not None:
            raise Forbidden(WEBHOOK_ADMIN)


async def check_saved(
    s: AsyncSession,
    caller: Caller,
    conditions: Sequence[engine.Condition],
    actions: Sequence[engine.Action],
) -> None:
    """A new or changed rule or flow may only name what exists now: its ticket types and
    teams, active members, webhooks, segments, contact fields (with a value of the field's
    kind). One that calls a webhook is an Admin's to save (`may_call_webhooks`)."""
    may_call_webhooks(caller, actions)
    types = await rule_repo.names(s, "hd_ticket_type")
    teams = await rule_repo.names(s, "hd_team")
    if engine.action_problems(actions, types, teams):
        raise Invalid("A rule can only set a ticket type and team that exist.")
    found = await known(s, conditions, actions)
    for problem in engine.action_problems(actions, {}, {}, found):
        if "type" not in problem and "team" not in problem:
            raise Invalid(problem.replace("was removed or deactivated", "isn't an active member")
                          .replace("was deleted", "doesn't exist"))  # fmt: skip
    for problem in engine.condition_problems(conditions, found):
        raise Invalid(problem.replace("was deleted", "doesn't exist"))
    await _check_field_values(s, conditions)


async def _check_field_values(s: AsyncSession, conditions: Sequence[engine.Condition]) -> None:
    from cedesk.helpdesk.domain.contact_fields import (
        ContactFieldError,
        ContactFieldKind,
        clean_value,
    )
    from cedesk.helpdesk.service import contacts as contact_service

    asked = [c for c in conditions if isinstance(c, engine.ContactFieldIs)]
    if not asked:
        return
    by_key = {f.key: f for f in await contact_service.fields(s)}
    for c in asked:
        f = by_key[c.key]
        if c.value is None:
            continue
        if c.op is engine.FieldOp.contains:
            if f.kind is not ContactFieldKind.text:
                raise Invalid(f"contains works on text fields; {f.label} is {f.kind.value}.")
            continue
        if c.op in (engine.FieldOp.more_than, engine.FieldOp.less_than) and f.kind not in (
            ContactFieldKind.number,
            ContactFieldKind.date,
        ):
            raise Invalid(f"{c.op.value} works on number and date fields; {f.label} is "
                          f"{f.kind.value}.")  # fmt: skip
        try:
            clean_value(f, c.value)
        except ContactFieldError as exc:
            raise Invalid(str(exc)) from exc


# Facts, targets and the starting state


async def segments_of(
    s: AsyncSession, contact_id: UUID, segment_ids: frozenset[UUID]
) -> frozenset[UUID]:
    """Which of these segments the contact is in now (each segment's own filters, run
    as the contact list runs them)."""
    if not segment_ids:
        return frozenset()
    # Imported here: the contacts service imports nothing of automation, but keeping the
    # import local leaves this module light for the worker.
    from cedesk.helpdesk.domain import contacts as contact_rules
    from cedesk.helpdesk.service import contacts as contact_service

    fields = await contact_service.fields(s)
    inside = set()
    for row in await repo.segment_filters(s, sorted(segment_ids)):
        try:
            f = contact_rules.filters_from_json(fields, row["filters"])
        except contact_rules.ContactError:
            continue  # a segment on a deleted field: nobody is in it
        query = contact_service.query_filters(f)
        if await contact_repo.contact_matches(s, contact_id, filters=query):
            inside.add(UUID(str(row["id"])))
    return frozenset(inside)


async def build_facts(
    s: AsyncSession,
    detail: Mapping[Any, Any],
    event: engine.Event,
    conditions: Sequence[engine.Condition],
    *,
    now: datetime,
    to_address: str | None = None,
    new_message: str | None = None,
) -> engine.Facts:
    """What conditions look at. The costly facts are read only when a condition asks.
    Text conditions read the subject and every customer message, except on a customer
    reply: only `new_message`, so a word said once does not match every later reply."""
    ticket_id = detail["id"]
    kinds = {type(c) for c in conditions}
    if event is engine.Event.customer_replied and new_message is not None:
        text = new_message
    elif engine.TextContainsAny in kinds:
        text = f"{detail['subject']} {await rule_repo.customer_text(s, ticket_id)}"
    else:
        text = str(detail["subject"])
    attributes: dict[str, Any] = {}
    if engine.ContactFieldIs in kinds:
        attributes, _erased = await repo.contact_state(s, detail["contact_id"])
    last_customer = last_agent = None
    if engine.TimeSince in kinds:
        last_customer, last_agent = await repo.reply_times(s, ticket_id)
    needs_sla = engine.SlaIn in kinds
    return engine.Facts(
        text=text or "",
        channel=Channel(detail["channel"]),
        to_address=(
            to_address or await rule_repo.to_address(s, ticket_id)
            if engine.ToAddressContains in kinds
            else to_address
        ),
        sentiment=Sentiment(detail["sentiment"]) if detail["sentiment"] else None,
        sla=sla.state(detail, await sla.config(s)).current.state if needs_sla else None,
        contact_vip=bool(detail["contact_vip"]),
        attributes=attributes,
        segments=await segments_of(s, detail["contact_id"], engine.segments_named(conditions)),
        now=now,
        created_at=detail["created_at"],
        last_customer_at=last_customer,
        last_agent_at=last_agent,
    )


async def build_targets(s: AsyncSession, actions: Sequence[engine.Action]) -> engine.Targets:
    member_ids = sorted({a.member_id for a in actions if isinstance(a, engine.AddFollower)})
    hook_ids = sorted({a.webhook_id for a in actions if isinstance(a, engine.CallWebhook)})
    members: dict[UUID, engine.Member] = {}
    for member_id in member_ids:
        row = await ticket_repo.member_brief(s, member_id)
        if row is None or not row["is_active"]:
            continue
        access = member_access(row)
        only = access.team_id if access.scope is Scope.team_only else None
        members[member_id] = engine.Member(str(row["name"]), only)
    hooks = {
        UUID(str(r["id"])): webhook_rules.host_of(str(r["url"]))
        for r in (await repo.webhooks(s, hook_ids) if hook_ids else ())
        if r["enabled"]
    }
    return engine.Targets(
        types=await rule_repo.names(s, "hd_ticket_type"),
        teams=await rule_repo.names(s, "hd_team"),
        members=members,
        webhooks=hooks,
    )


async def start_state(
    s: AsyncSession, before: Mapping[Any, Any], actions: Sequence[engine.Action],
    conditions: Sequence[engine.Condition], *, always_followers: bool = False,
) -> engine.State:  # fmt: skip
    """The ticket's fields before the actions run. Followers cost a query, so a live run
    loads them only when a step looks at them; a test passes `always_followers` so its
    "ticket after" shows the real followers."""
    wants_followers = (
        always_followers
        or any(isinstance(a, engine.AddFollower) for a in actions)
        or any(isinstance(c, engine.HasFollowers) for c in conditions)
    )
    return engine.State(
        type_id=before["type_id"],
        team_id=before["team_id"],
        priority=Priority(before["priority"]),
        status=Status(before["status"]),
        tags=tuple(before["tags"]),
        snoozed=before["snoozed_until"] is not None,
        followers=await repo.followers(s, before["id"]) if wants_followers else frozenset(),
    )


# Safeguards


async def event_depth(s: AsyncSession, ticket_id: UUID, event: engine.Event, now: datetime) -> int:
    chained = event in (engine.Event.customer_replied, engine.Event.snooze_ended)
    deepest = await repo.chain_depth(s, ticket_id, guard.chain_since(now)) if chained else 0
    return guard.depth(chained and deepest > 0, deepest)


async def blocked(
    s: AsyncSession,
    ticket_id: UUID,
    *,
    rule_id: UUID | None = None,
    flow_id: UUID | None = None,
    per_hour: int,
    per_day: int,
    depth: int,
    now: datetime,
) -> str | None:
    if depth > guard.MAX_DEPTH:
        return guard.blocked_reason(
            at_depth=depth, counts=guard.Counts(0, 0, 0), per_hour=per_hour, per_day=per_day
        )
    day = now - timedelta(days=1)
    counts = guard.Counts(
        on_ticket=await repo.recent_on_ticket(
            s, ticket_id, rule_id=rule_id, flow_id=flow_id, since=day
        ),
        last_hour=await repo.recent_runs(
            s, rule_id=rule_id, flow_id=flow_id, since=now - timedelta(hours=1)
        ),
        last_day=await repo.recent_runs(s, rule_id=rule_id, flow_id=flow_id, since=day),
    )
    return guard.blocked_reason(at_depth=depth, counts=counts, per_hour=per_hour, per_day=per_day)


def _outward(effects: Sequence[engine.Effect]) -> bool:
    return any(isinstance(e, engine.OUTWARD) for e in effects)


# One event


async def run_event(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    event: engine.Event,
    *,
    to_address: str | None = None,
    new_message: str | None = None,
) -> RuleRun | None:
    """Runs the enabled rules, then the flows, for `event` on a ticket, inside the
    caller's transaction, with the ticket row locked. Saves the result, writes the lines
    and carries out the side effects. None: nothing changed. Routing and events are the
    caller's job."""
    rules = await enabled_rules(s, event)
    flows = await enabled_flows(s, event)
    if not rules and not flows:
        return None
    before = await ticket_repo.lock_ticket(s, ticket_id)
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    if before is None or detail is None:
        raise NotFound("Ticket not found.")
    now = datetime.now(UTC)
    conditions = [c for r in rules for c in r.rule.conditions] + [
        c for f in flows for c in f.graph.conditions()
    ]
    actions = [a for r in rules for a in r.rule.actions] + [
        a for f in flows for a in f.graph.actions()
    ]
    facts = await build_facts(
        s, detail, event, conditions, now=now, to_address=to_address, new_message=new_message
    )
    targets = await build_targets(s, actions)
    state = await start_state(s, before, actions, conditions)
    depth: int | None = None  # worked out once, when something first matches
    pending = Pending()

    for loaded in rules:
        rule = loaded.rule
        if not engine.matched(rule, facts, state):
            continue
        if depth is None:
            depth = await event_depth(s, ticket_id, event, now)
        reason = await blocked(
            s, ticket_id, rule_id=rule.id, per_hour=loaded.per_hour, per_day=loaded.per_day,
            depth=depth, now=now,
        )  # fmt: skip
        if reason:
            await _log_blocked(s, workspace_id, ticket_id, event, depth, reason, rule=rule)
            continue
        first = len(state.effects)
        state, done = engine.apply_actions(rule.actions, state, targets)
        if not done:  # a rule that matched but found nothing to change: no line, no log
            continue
        new_effects = state.effects[first:]
        run_id = await repo.insert_run(
            s, workspace_id, rule_id=rule.id, flow_id=None, flow_version=None, name=rule.name,
            ticket_id=ticket_id, trigger=event.value, state=RunState.done.value,
            result=guard.summary(done),
            steps=[{"type": "action", "outcome": "done", "said": d} for d in done],
            depth=depth, outward=_outward(new_effects),
        )  # fmt: skip
        pending.lines.append(engine.line(engine.Applied(rule, tuple(done))))
        pending.effects += [
            (e, Who(run_id, "rule", rule.id, rule.name, i)) for i, e in enumerate(new_effects)
        ]

    for flow in flows:
        if depth is None:
            depth = await event_depth(s, ticket_id, event, now)
        first = len(state.effects)
        walked = flow_rules.walk(flow.graph, facts, state, targets)
        if not walked.done and walked.wait_at is None and walked.stopped is None:
            continue  # its conditions sent it nowhere: like a rule that did not match
        reason = await blocked(
            s, ticket_id, flow_id=flow.id, per_hour=flow.per_hour, per_day=flow.per_day,
            depth=depth, now=now,
        )  # fmt: skip
        if reason:
            await _log_blocked(s, workspace_id, ticket_id, event, depth, reason, flow=flow)
            continue
        state = walked.state
        new_effects = state.effects[first:]
        run_id = await _log_walk(
            s, workspace_id, ticket_id, event.value, depth, flow, walked, new_effects, now
        )
        if walked.done:
            pending.lines.append(flow_rules.line(flow.name, walked.done))
        pending.effects += [
            (e, Who(run_id, "flow", flow.id, flow.name, i)) for i, e in enumerate(new_effects)
        ]

    if not pending.lines:
        return None
    return await save(s, workspace_id, before, detail, state, pending, now)


async def _log_blocked(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    event: engine.Event,
    depth: int,
    reason: str,
    *,
    rule: engine.Rule | None = None,
    flow: LoadedFlow | None = None,
) -> None:
    logger.info("automation blocked", extra={"event": "automation", "workspace_id": workspace_id})
    await repo.insert_run(
        s, workspace_id, rule_id=rule.id if rule else None, flow_id=flow.id if flow else None,
        flow_version=flow.version if flow else None,
        name=rule.name if rule else flow.name if flow else "?", ticket_id=ticket_id,
        trigger=event.value, state=RunState.blocked.value, result=reason, steps=[],
        depth=depth, outward=False,
    )  # fmt: skip


async def _log_walk(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    trigger: str,
    depth: int,
    flow: LoadedFlow,
    walked: flow_rules.Walk,
    new_effects: Sequence[engine.Effect],
    now: datetime,
) -> UUID:
    waiting = walked.wait_at is not None
    return await repo.insert_run(
        s, workspace_id, rule_id=None, flow_id=flow.id, flow_version=flow.version,
        name=flow.name, ticket_id=ticket_id, trigger=trigger,
        state=_walk_state(walked).value, result=_walk_result(walked),
        steps=[x.stored() for x in walked.steps], depth=depth,
        outward=_outward(new_effects), error=walked.stopped,
        node_id=walked.wait_at, waits=1 if waiting else 0, until_reply=walked.until_reply,
        resume_at=now + timedelta(minutes=walked.wait_minutes) if waiting else None,
    )  # fmt: skip


def _walk_state(walked: flow_rules.Walk) -> RunState:
    if walked.stopped:
        return RunState.failed
    return RunState.waiting if walked.wait_at is not None else RunState.done


def _walk_result(walked: flow_rules.Walk) -> str:
    said = guard.summary(walked.done)
    if walked.stopped:
        return walked.stopped
    if walked.wait_at is not None:
        what = " or until the customer replies" if walked.until_reply else ""
        return f"{said} · waiting {engine.minutes_text(walked.wait_minutes)}{what}"
    return said


# Saving and side effects


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


async def save(
    s: AsyncSession,
    workspace_id: UUID,
    before: Mapping[Any, Any],
    detail: Mapping[Any, Any],
    final: engine.State,
    pending: Pending,
    now: datetime,
) -> RuleRun:
    """Saves the ticket's new fields, writes the lines, then does the side effects."""
    ticket_id = before["id"]
    after: dict[str, Any] = {k: before[k] for k in ("subject", "assignee_id", "reopen_count")} | {
        "status": final.status.value,
        "priority": final.priority.value,
        "type_id": final.type_id,
        "team_id": final.team_id,
        "tags": list(final.tags),
        "fields": dict(before["fields"]),
    }
    lines = list(pending.lines)
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
            s, workspace_id=workspace_id, ticket_id=ticket_id, kind="system", body=body,
            author_member_id=None, author_name=None,
        )  # fmt: skip
    if pending.effects:
        await carry_out(s, workspace_id, ticket_id, after, pending.effects, now)
    return RuleRun(before=before, after=after, team_changed=final.team_id != before["team_id"])


async def carry_out(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    after: Mapping[str, Any],
    effects: Sequence[tuple[engine.Effect, Who]],
    now: datetime,
) -> None:
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    assert detail is not None
    snooze = [e for e, _ in effects if isinstance(e, engine.Snooze)]
    # A later action may have moved the status on: a snooze only holds a Pending ticket.
    if snooze and after["status"] == Status.pending.value:
        await ticket_repo.set_snooze(s, ticket_id, now + timedelta(minutes=snooze[-1].minutes))
    follow = [e.member_id for e, _ in effects if isinstance(e, engine.AddFollower)]
    if follow:
        await followers.follow(s, workspace_id, ticket_id, follow)
    demo = detail["source"] == DEMO_SOURCE
    for effect, who in effects:
        if isinstance(effect, engine.SendEmail):
            await _send_email(s, workspace_id, detail, effect.body)
        elif isinstance(effect, engine.CallWebhook) and not demo:
            await _queue_webhook(s, workspace_id, detail, effect.webhook_id, who, now)


async def _send_email(
    s: AsyncSession, workspace_id: UUID, detail: Mapping[Any, Any], body: str
) -> None:
    _attributes, erased = await repo.contact_state(s, detail["contact_id"])
    if erased:  # nobody to write to (privacy erase)
        await ticket_repo.insert_message(
            s, workspace_id=workspace_id, ticket_id=detail["id"], kind="system",
            body="Automatic email not sent: the customer's data was erased.",
            author_member_id=None, author_name=None,
        )  # fmt: skip
        return
    workspace = await auth_repo.workspace_row(s)
    sender = str(workspace["name"]) if workspace and workspace["name"] else guard.SENDER
    await post_workspace_reply(s, workspace_id, detail, body, sender)


async def post_workspace_reply(
    s: AsyncSession, workspace_id: UUID, detail: Mapping[Any, Any], text: str, sender_name: str
) -> UUID:
    """A reply from the workspace (an automation, auto-close's notice), sent the way an
    agent's reply is: emailed on email and web-form tickets (never demo ones), and through
    the outbox to the portal and the Cevanos chat. It does not stop the first-reply SLA
    clock: no person answered."""
    body = render(
        text,
        customer_name=str(detail["contact_name"]),
        agent_name=sender_name,
        fields=detail["fields"],
    )
    message_id = await ticket_repo.insert_message(
        s, workspace_id=workspace_id, ticket_id=detail["id"], kind="agent", body=body,
        author_member_id=None, author_name=sender_name[:120],
    )  # fmt: skip
    emailed = (detail["channel"] == "email" or detail["source"] == WEB_FORM_SOURCE) and detail[
        "source"
    ] != DEMO_SOURCE
    if emailed:
        await email_service.queue_reply(s, workspace_id, detail, message_id, body, sender_name)
    await outbox.add(
        s,
        workspace_id,
        events.Event.message_created.value,
        detail["id"],
        events.payload(
            events.Event.message_created, detail, events.message(message_id, "agent", None)
        ),
    )
    return message_id


async def _queue_webhook(
    s: AsyncSession,
    workspace_id: UUID,
    detail: Mapping[Any, Any],
    webhook_id: UUID,
    who: Who,
    now: datetime,
) -> None:
    """One delivery to the workspace's webhook, sent by the webhook worker: signed with
    its secret, the egress guard before each try, retried and logged as its others. The
    delivery id is fixed per run step, so a repeat adds nothing."""
    event_id = uuid5(_ID_SPACE, f"{who.run_id}:{who.step}")
    body = {
        "event": WEBHOOK_EVENT,
        "id": str(event_id),
        "occurred_at": now.isoformat(),
        "automation": {
            "kind": who.kind,
            "id": str(who.source_id),
            "name": who.name,
            "run_id": str(who.run_id),
        },
        "ticket": events.ticket_brief(detail),
    }
    await webhook_repo.add_delivery(
        s, workspace_id, webhook_id=webhook_id, event_id=event_id, event=WEBHOOK_EVENT, body=body
    )


async def settle(s: AsyncSession, workspace_id: UUID, ticket_id: UUID, run: RuleRun) -> None:
    """After a run the caller did not set off itself (a wake-up, a resumed flow): routes
    the ticket again if its team changed, refreshes its SLA and writes the events."""
    after = run.after
    if run.team_changed and after["team_id"] is not None and after["status"] != "resolved":
        pick = await routing.route(
            s,
            ticket_id=ticket_id,
            team_id=after["team_id"],
            channel=str(run.before["channel"]),
            type_id=after["type_id"],
        )
        after["assignee_id"] = pick.agent.member_id if pick.agent else None
        await ticket_repo.set_assignee(s, ticket_id, after["assignee_id"])
        await ticket_repo.insert_message(
            s, workspace_id=workspace_id, ticket_id=ticket_id, kind="system", body=pick.line,
            author_member_id=None, author_name=None,
        )  # fmt: skip
    await sla.refresh(s, ticket_ids=[ticket_id])
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    assert detail is not None
    for event, extra in events.after_change(run.before, after, None):
        await outbox.add(
            s, workspace_id, event.value, ticket_id, events.payload(event, detail, extra)
        )


async def run_and_settle(
    s: AsyncSession, workspace_id: UUID, ticket_id: UUID, event: engine.Event
) -> RuleRun | None:
    run = await run_event(s, workspace_id, ticket_id, event)
    if run is not None:
        await settle(s, workspace_id, ticket_id, run)
    return run


# Waiting flow runs


async def customer_replied(s: AsyncSession, ticket_id: UUID) -> None:
    """In the reply's transaction: runs waiting for the customer's reply on this ticket
    are due now; the worker moves them on by their `replied` edge."""
    await repo.customer_replied(s, ticket_id, datetime.now(UTC))


async def resume_due(sm: Sessionmaker, now: datetime | None = None) -> int:
    """Every workspace (the worker, every tick). Returns how many runs moved on."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    moved = 0
    for workspace_id in workspace_ids:
        try:
            moved += await resume_workspace(sm, workspace_id, now)
        except Exception:  # one workspace's problem never stops the others
            logger.exception("resuming flows failed", extra={"workspace_id": workspace_id})
    return moved


async def resume_workspace(sm: Sessionmaker, workspace_id: UUID, now: datetime) -> int:
    async with workspace_session(sm, workspace_id) as s:
        due = await repo.due_runs(s, now, guard.RESUME_BATCH)
    moved = 0
    for run_id in due:
        try:
            async with workspace_session(sm, workspace_id) as s:
                moved += await resume_one(s, workspace_id, run_id, now)
        except Exception as exc:
            logger.exception("resuming a flow run failed", extra={"workspace_id": workspace_id})
            async with workspace_session(sm, workspace_id) as s:
                await repo.cancel_waiting(
                    s, run_id=run_id, reason=f"Failed: {type(exc).__name__}", state="failed"
                )
    return moved


async def resume_one(s: AsyncSession, workspace_id: UUID, run_id: UUID, now: datetime) -> int:
    """One due run, in the caller's transaction. 0 when another worker has it or it no
    longer waits."""
    run = await repo.claim_due(s, run_id, now)
    if run is None:
        return 0
    flow = await repo.flow_state(s, run["flow_id"]) if run["flow_id"] else None
    if flow is None or not flow["enabled"]:
        await repo.cancel_waiting(
            s, run_id=run_id, reason="The workflow was turned off or deleted."
        )
        return 1
    before = await ticket_repo.lock_ticket(s, run["ticket_id"])
    detail = await ticket_repo.ticket_detail(s, run["ticket_id"])
    assert before is not None and detail is not None  # the run goes with its ticket
    if before["merged_into_id"] is not None:
        await repo.cancel_waiting(s, run_id=run_id, reason="The ticket was merged.")
        return 1
    if before["status"] == Status.resolved.value and not run["replied"]:
        await repo.cancel_waiting(s, run_id=run_id, reason="The ticket was resolved.")
        return 1
    raw = await repo.version_graph(s, run["flow_id"], run["flow_version"])
    graph = flow_rules.parse_graph(raw)
    way = (
        flow_rules.Way.replied
        if run["replied"]
        else flow_rules.Way.timeout
        if run["until_reply"]
        else flow_rules.Way.next
    )
    conditions = graph.conditions()
    actions = graph.actions()
    facts = await build_facts(s, detail, engine.Event(run["trigger"]), conditions, now=now)
    targets = await build_targets(s, actions)
    state = await start_state(s, before, actions, conditions)
    earlier = list(run["steps"])
    walked = flow_rules.walk(
        graph, facts, state, targets, at=run["node_id"], way=way,
        steps_before=len(earlier), waits_before=int(run["waits"]),
    )  # fmt: skip
    steps = [*earlier, {"node": run["node_id"], "type": "wait", "outcome": way.value}]
    steps += [x.stored() for x in walked.steps]
    new_effects = walked.state.effects
    waiting = walked.wait_at is not None
    await repo.update_run(
        s, run_id, state=_walk_state(walked).value, result=_walk_result(walked), steps=steps,
        outward=_outward(new_effects), error=walked.stopped, node_id=walked.wait_at,
        waits=int(run["waits"]) + (1 if waiting else 0), until_reply=walked.until_reply,
        resume_at=now + timedelta(minutes=walked.wait_minutes) if waiting else None,
    )  # fmt: skip
    if not walked.done:
        return 1
    pending = Pending(
        lines=[flow_rules.line(str(run["name"]), walked.done)],
        effects=[
            (e, Who(run_id, "flow", run["flow_id"], str(run["name"]), len(steps) + i))
            for i, e in enumerate(new_effects)
        ],
    )
    saved = await save(s, workspace_id, before, detail, walked.state, pending, now)
    await settle(s, workspace_id, run["ticket_id"], saved)
    return 1


async def prune_everywhere(sm: Sessionmaker) -> int:
    """The worker's sweep: each workspace's run log keeps `KEEP_DAYS` days of finished
    runs (waiting ones stay until they finish)."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    gone = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            gone += await repo.prune(s, guard.KEEP_DAYS)
    return gone
