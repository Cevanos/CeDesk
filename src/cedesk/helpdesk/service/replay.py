"""Replay (differentiator plan #3): a saved or unsaved rule, or a workflow version, tried
on the workspace's newest tickets before it is switched on. Admin only.

An Admin asks (`start_rule`, `start_flow`); the worker (`run_due`) goes through the tickets
in pages of 100, newest first, and saves the counts and up to 200 examples after each
page, so the Admin sees progress. One queued or running replay per workspace.

Read-only on tickets: each ticket is tried with the same code test mode uses
(`rules.evaluate`, `flows.walk`); nothing on it changes, nothing is sent, logged or
queued. A workflow is walked up to its first wait (counted as "would wait").

Conflicts: every other enabled rule for the same event is tried on the same ticket as it
is; where one sets status, priority, type or team to something else than the tested one
does, that is a conflict. The later one in run order wins (rules in their order, a new
rule last, workflows after every rule).
"""

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import flows as flow_rules
from cedesk.helpdesk.domain import replay as rules
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import automation as flow_repo
from cedesk.helpdesk.repo import replay as repo
from cedesk.helpdesk.repo import rules as rule_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.replay import Row
from cedesk.helpdesk.service import automation, flows
from cedesk.helpdesk.service import rules as rule_service
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk")

BUSY = "A replay is already running in this workspace. Wait for it, or cancel it."
NO_REPLAY = "Replay not found."
PAGES_PER_PASS = 2  # per worker tick, so one replay never holds the worker up for long
SUBJECT_MAX = 120


def _limit(ticket_limit: int) -> int:
    try:
        return rules.check_limit(ticket_limit)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc


async def _insert(s: AsyncSession, caller: Caller, **values: Any) -> UUID:
    if await repo.active(s) is not None:
        raise Conflict(BUSY)
    return await repo.insert(s, caller.workspace_id, requested_by=caller.member_id, **values)


async def start_rule(
    sm: Sessionmaker,
    caller: Caller,
    *,
    ticket_limit: int,
    rule_id: UUID | None = None,
    draft: Mapping[str, Any] | None = None,
) -> Row:
    """A saved rule (`rule_id`, as saved now) or an unsaved one (`draft`: name, event,
    conditions, actions). 409 while another replay is queued or running."""
    require(caller, Action.settings)
    limit = _limit(ticket_limit)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            if rule_id is not None:
                row = await rule_repo.get_rule(s, rule_id)
                if row is None:
                    raise NotFound("Rule not found.")
                name = str(row["name"])
                stored = None
            else:
                assert draft is not None
                rule = rule_service.parse(
                    str(draft.get("name") or "Test"), str(draft.get("event")),
                    list(draft.get("conditions") or []), list(draft.get("actions") or []),
                )  # fmt: skip
                name = rule.name
                stored = {
                    "name": rule.name, "event": rule.event.value,
                    "conditions": [engine.stored_condition(c) for c in rule.conditions],
                    "actions": [engine.stored_action(a) for a in rule.actions],
                }  # fmt: skip
            replay_id = await _insert(
                s, caller, kind="rule", target_id=rule_id, target_version=None, name=name,
                draft=stored, ticket_limit=limit,
            )  # fmt: skip
            found = await repo.get(s, replay_id)
    except IntegrityError as exc:  # two Admins at the same moment: the unique index
        raise Conflict(BUSY) from exc
    assert found is not None
    return found


async def start_flow(
    sm: Sessionmaker,
    caller: Caller,
    flow_id: UUID,
    *,
    ticket_limit: int,
    version: int | None = None,
) -> Row:
    """A saved workflow: its current version, or an older one (`version`)."""
    require(caller, Action.settings)
    limit = _limit(ticket_limit)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            flow = await flow_repo.get_flow(s, flow_id)
            if flow is None:
                raise NotFound("Workflow not found.")
            version = int(flow["version"]) if version is None else version
            graph = await flow_repo.version_graph(s, flow_id, version)
            if graph is None:
                raise NotFound(f"The workflow has no version {version}.")
            flows.parse(graph)  # still reads
            replay_id = await _insert(
                s, caller, kind="flow", target_id=flow_id, target_version=version,
                name=str(flow["name"]), draft=None, ticket_limit=limit,
            )  # fmt: skip
            found = await repo.get(s, replay_id)
    except IntegrityError as exc:
        raise Conflict(BUSY) from exc
    assert found is not None
    return found


async def get(sm: Sessionmaker, caller: Caller, replay_id: UUID) -> Row:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        found = await repo.get(s, replay_id)
    if found is None:
        raise NotFound(NO_REPLAY)
    return found


async def cancel(sm: Sessionmaker, caller: Caller, replay_id: UUID) -> Row:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await repo.get(s, replay_id) is None:
            raise NotFound(NO_REPLAY)
        if not await repo.cancel(s, replay_id):
            raise Conflict("This replay has already finished.")
        found = await repo.get(s, replay_id)
    assert found is not None
    return found


# The worker


@dataclass(frozen=True)
class Tested:
    """What a replay tries, read again at each page (a rule may change meanwhile)."""

    event: engine.Event
    rule: engine.Rule | None
    graph: flow_rules.Graph | None
    order: float  # its place in run order
    conditions: list[engine.Condition]
    actions: list[engine.Action]


class Gone(Exception):
    """What the replay tries no longer exists or no longer reads."""


async def _tested(s: AsyncSession, row: Row) -> Tested:
    order_of = {r["id"]: i for i, r in enumerate(await rule_repo.list_rules(s))}
    if row["kind"] == "flow":
        if row["draft"] is not None:
            raw = row["draft"].get("graph")
        else:
            raw = await flow_repo.version_graph(s, row["target_id"], row["target_version"])
        if raw is None:
            raise Gone("The workflow was deleted.")
        try:
            graph = flow_rules.parse_graph(raw)
        except flow_rules.FlowError as exc:
            raise Gone("The workflow no longer reads.") from exc
        return Tested(graph.event, None, graph, math.inf, graph.conditions(), graph.actions())
    if row["draft"] is not None:
        draft = row["draft"]
        raw_rule = {"id": UUID(int=0), **draft}
        order = len(order_of) + 0.5  # a new rule is added last
    else:
        found = await rule_repo.get_rule(s, row["target_id"])
        if found is None:
            raise Gone("The rule was deleted.")
        raw_rule = dict(found)
        order = order_of.get(found["id"], len(order_of))
    try:
        rule = automation.rule_from_row(raw_rule)
    except engine.RuleError as exc:
        raise Gone("The rule no longer reads.") from exc
    return Tested(rule.event, rule, None, order, list(rule.conditions), list(rule.actions))


@dataclass
class Tally:
    checked: int
    matched: int
    would_change: int
    would_wait: int
    conflicts: int
    samples: list[dict[str, Any]]
    clashes: list[dict[str, Any]]

    @classmethod
    def of(cls, row: Row) -> "Tally":
        return cls(
            row["checked"], row["matched"], row["would_change"], row["would_wait"],
            row["conflicts"], list(row["samples"]), list(row["conflict_samples"]),
        )  # fmt: skip

    def counts(self) -> dict[str, int]:
        return {
            "checked": self.checked, "matched": self.matched,
            "would_change": self.would_change, "would_wait": self.would_wait,
            "conflicts": self.conflicts,
        }  # fmt: skip


async def _try_ticket(
    s: AsyncSession,
    tested: Tested,
    others: Sequence[automation.LoadedRule],
    order_of: Mapping[UUID, int],
    targets: engine.Targets,
    ticket_id: UUID,
    now: datetime,
    tally: Tally,
) -> None:
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    if detail is None:  # deleted meanwhile
        return
    conditions = tested.conditions + [c for o in others for c in o.rule.conditions]
    actions = tested.actions + [a for o in others for a in o.rule.actions]
    facts = await automation.build_facts(s, detail, tested.event, conditions, now=now)
    before = await automation.start_state(s, detail, actions, conditions)
    names = {"types": targets.types, "teams": targets.teams}
    waits = False
    if tested.rule is not None:
        _, explained = await rule_service.evaluate(
            s, tested.rule, detail, now=now, targets=targets, facts=facts, state=before
        )
        matched, after = explained.matched, explained.state
        said = [x for _, x in explained.actions if x]
    else:
        assert tested.graph is not None
        walked = flow_rules.walk(tested.graph, facts, before, targets)
        after, said, waits = walked.state, list(walked.done), walked.wait_at is not None
        matched = bool(said) or waits
    tally.checked += 1
    tally.matched += int(matched)
    tally.would_wait += int(waits)
    if not said:
        return
    tally.would_change += 1
    found = rules.changes(before, after, names)
    if len(tally.samples) < rules.SAMPLES_MAX:
        tally.samples.append({
            "ticket_id": str(ticket_id), "number": detail["number"],
            "subject": str(detail["subject"])[:SUBJECT_MAX],
            "changes": [c.stored() for c in found], "said": said,
        })  # fmt: skip
    if not found:
        return
    mine = []
    for loaded in others:
        _, theirs = await rule_service.evaluate(
            s, loaded.rule, detail, now=now, targets=targets, facts=facts, state=before
        )
        if theirs.matched:
            order = order_of.get(loaded.rule.id, 0)
            mine.append(rules.Other(loaded.rule.id, loaded.rule.name, order, theirs.state))
    for clash in rules.clashes(before, after, tested.order, mine, names):
        tally.conflicts += 1
        if len(tally.clashes) < rules.SAMPLES_MAX:
            tally.clashes.append({"ticket_id": str(ticket_id), "number": detail["number"],
                                  **clash.stored()})  # fmt: skip


async def _page(s: AsyncSession, replay_id: UUID) -> bool:
    """One page of tickets, in the caller's transaction. True when the replay is over
    (done, or cancelled meanwhile)."""
    row = await repo.lock_running(s, replay_id)
    if row is None:  # cancelled
        return True
    tested = await _tested(s, row)
    tested_id = row["target_id"] if row["kind"] == "rule" else None
    others = [r for r in await automation.enabled_rules(s, tested.event)
              if r.rule.id != tested_id]  # fmt: skip
    order_of = {r["id"]: i for i, r in enumerate(await rule_repo.list_rules(s))}
    targets = await automation.build_targets(
        s, tested.actions + [a for o in others for a in o.rule.actions]
    )
    tally = Tally.of(row)
    left = row["ticket_limit"] - tally.checked
    tickets = await repo.ticket_page(
        s, row["cursor_at"], row["cursor_id"], min(rules.PAGE, max(left, 0))
    )
    now = datetime.now(UTC)
    for ticket in tickets:
        await _try_ticket(s, tested, others, order_of, targets, ticket["id"], now, tally)
    last = tickets[-1] if tickets else None
    done = not tickets or len(tickets) >= left or len(tickets) < rules.PAGE
    await repo.save_page(
        s, replay_id, counts=tally.counts(), samples=tally.samples,
        conflict_samples=tally.clashes,
        cursor_at=last["created_at"] if last else row["cursor_at"],
        cursor_id=last["id"] if last else row["cursor_id"],
        lease_seconds=rules.LEASE_SECONDS, done=done,
    )  # fmt: skip
    return done


async def run_workspace(sm: Sessionmaker, workspace_id: UUID) -> int:
    """Up to `PAGES_PER_PASS` pages of the workspace's replay. Returns the pages done."""
    async with workspace_session(sm, workspace_id) as s:
        claimed = await repo.claim(s, rules.LEASE_SECONDS)
    if claimed is None:
        return 0
    replay_id = claimed["id"]
    pages = 0
    try:
        for _ in range(PAGES_PER_PASS):
            async with workspace_session(sm, workspace_id) as s:
                over = await _page(s, replay_id)
            pages += 1
            if over:
                return pages
    except Gone as exc:
        async with workspace_session(sm, workspace_id) as s:
            await repo.fail(s, replay_id, str(exc))
        return pages
    except Exception as exc:
        logger.exception("a replay failed", extra={"workspace_id": workspace_id})
        async with workspace_session(sm, workspace_id) as s:
            await repo.fail(s, replay_id, f"Failed: {type(exc).__name__}")
        return pages
    async with workspace_session(sm, workspace_id) as s:
        await repo.release(s, replay_id)
    return pages


async def run_due(sm: Sessionmaker) -> int:
    """Every workspace (the worker, every tick). Returns the pages done."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    pages = 0
    for workspace_id in workspace_ids:
        try:
            pages += await run_workspace(sm, workspace_id)
        except Exception:  # one workspace's problem never stops the others
            logger.exception("replays failed", extra={"workspace_id": workspace_id})
    return pages


async def prune_everywhere(sm: Sessionmaker) -> int:
    """The worker's sweep: finished replays are kept `KEEP_DAYS` days."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    gone = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            gone += await repo.prune(s, rules.KEEP_DAYS)
    return gone
