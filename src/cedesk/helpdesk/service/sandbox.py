"""Sandbox (md S10): copy the rules, macros and saved replies, edit the copy, compare it
with the live ones, try the rules on sample tickets, then deploy it or delete it.

Deploying runs in one transaction through the same code as the settings pages (each
added, changed or removed item gets its own audit entry), so either every change is
saved or none is. The settings right (`settings`) is needed for all of it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import macros as macro_engine
from cedesk.helpdesk.domain import rules as rule_engine
from cedesk.helpdesk.domain import sandbox as rules
from cedesk.helpdesk.domain import saved_replies as reply_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.sandbox import Diff, Section
from cedesk.helpdesk.repo import macros as macro_repo
from cedesk.helpdesk.repo import rules as rule_repo
from cedesk.helpdesk.repo import sandbox as repo
from cedesk.helpdesk.repo import saved_replies as reply_repo
from cedesk.helpdesk.service import audit, macros, saved_replies
from cedesk.helpdesk.service import rules as rule_service
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_SANDBOX = "There is no sandbox. Make one first (it copies the live settings)."
MAX_ITEMS: Mapping[Section, int] = {
    Section.rules: rule_engine.MAX_RULES,
    Section.macros: macro_engine.MAX_MACROS,
    Section.saved_replies: reply_rules.MAX_PER_WORKSPACE,
}


async def _live(s: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    return {
        Section.rules: [rules.item(Section.rules, r) for r in await rule_repo.list_rules(s)],
        Section.macros: [rules.item(Section.macros, r) for r in await macro_repo.list_macros(s)],
        Section.saved_replies: [
            rules.item(Section.saved_replies, r) for r in await reply_repo.list_replies(s, None)
        ],
    }


async def get(sm: Sessionmaker, caller: Caller) -> RowMapping:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s)
    if row is None:
        raise NotFound(NO_SANDBOX)
    return row


async def create(sm: Sessionmaker, caller: Caller) -> RowMapping:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        live = await _live(s)
        if not await repo.insert(s, caller.workspace_id, live, caller.member_id):
            raise Conflict("The workspace already has a sandbox. Deploy or delete it first.")
        await audit.record(
            s, caller, Area.sandbox, "created", "Sandbox",
            detail={str(k): len(v) for k, v in live.items()},
        )  # fmt: skip
        row = await repo.get(s)
    assert row is not None
    return row


def _id(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        return str(UUID(str(raw)))
    except ValueError as exc:
        raise Invalid(f"{raw!r} is not an id.") from exc


def _checked(section: Section, raw: Mapping[str, Any]) -> dict[str, Any]:
    """One item as sent, checked and in its stored form."""
    if section is Section.rules:
        rule = rule_service.parse(
            str(raw.get("name", "")), str(raw.get("event", "")),
            raw.get("conditions") or [], raw.get("actions") or [],
        )  # fmt: skip
        return {
            "id": _id(raw.get("id")),
            "name": rule.name,
            "enabled": bool(raw.get("enabled", True)),
            "event": rule.event.value,
            "conditions": [rule_engine.stored_condition(c) for c in rule.conditions],
            "actions": [rule_engine.stored_action(a) for a in rule.actions],
        }
    if section is Section.macros:
        name, steps = macros.parse(str(raw.get("name", "")), raw.get("steps") or [])
        return {
            "id": _id(raw.get("id")),
            "name": name,
            "steps": [macro_engine.stored_step(x) for x in steps],
        }
    return {
        "id": _id(raw.get("id")),
        "shortcut": saved_replies.clean_shortcut(str(raw.get("shortcut", ""))),
        "body": saved_replies.clean_body(str(raw.get("body", ""))),
    }


async def set_section(
    sm: Sessionmaker, caller: Caller, section: Section, items: Sequence[Mapping[str, Any]]
) -> RowMapping:
    """Replaces one section of the sandbox with `items`, in order. Keep an item's `id` to
    change that live item; leave it out to add one; drop an item to remove it."""
    require(caller, Action.settings)
    if len(items) > MAX_ITEMS[section]:
        raise Invalid(f"At most {MAX_ITEMS[section]} {section.value.replace('_', ' ')}.")
    checked = []
    for n, raw in enumerate(items, start=1):
        try:
            checked.append(_checked(section, raw))
        except Invalid as exc:
            raise Invalid(f"Item {n}: {exc}") from exc
    problem = rules.check_unique(section, checked)
    if problem:
        raise Invalid(problem)
    async with workspace_session(sm, caller.workspace_id) as s:
        box = await repo.get(s, lock=True)
        if box is None:
            raise NotFound(NO_SANDBOX)
        config = dict(box["config"]) | {section.value: checked}
        await repo.save(s, config, caller.member_id)
        await audit.record(
            s, caller, Area.sandbox, "changed", f"Sandbox {section.value.replace('_', ' ')}",
            detail={"items": len(checked)},
        )  # fmt: skip
        row = await repo.get(s)
    assert row is not None
    return row


async def delete(sm: Sessionmaker, caller: Caller) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.delete(s):
            raise NotFound(NO_SANDBOX)
        await audit.record(s, caller, Area.sandbox, "deleted", "Sandbox")


def _diffs(live: Mapping[str, Any], config: Mapping[str, Any]) -> dict[Section, Diff]:
    return {sec: rules.diff(sec, live[sec], config.get(sec.value, [])) for sec in Section}


async def compare(sm: Sessionmaker, caller: Caller) -> dict[Section, Diff]:
    """What deploying would change, per section."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        box = await repo.get(s)
        if box is None:
            raise NotFound(NO_SANDBOX)
        return _diffs(await _live(s), box["config"])


# Trying the rules


@dataclass(frozen=True)
class Tried:
    sample: rules.Sample
    live: rules.Outcome
    sandbox: rules.Outcome


def _enabled(items: Sequence[Mapping[str, Any]]) -> list[rule_engine.Rule]:
    found = []
    for x in items:
        if not x["enabled"]:
            continue
        try:
            found.append(
                rule_engine.parse_rule(UUID(int=0), x["name"], x["event"], x["conditions"],
                                       x["actions"])
            )  # fmt: skip
        except rule_engine.RuleError:
            continue  # a stored rule that no longer reads doesn't run live either
    return found


@dataclass(frozen=True)
class Trial:
    results: list[Tried]
    types: dict[UUID, str]
    teams: dict[UUID, str]


async def try_rules(sm: Sessionmaker, caller: Caller) -> Trial:
    """Runs the live rules and the sandbox rules on the sample tickets, side by side.
    Nothing is saved."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        box = await repo.get(s)
        if box is None:
            raise NotFound(NO_SANDBOX)
        live = (await _live(s))[Section.rules]
        types = await rule_repo.names(s, "hd_ticket_type")
        teams = await rule_repo.names(s, "hd_team")
    live_rules = _enabled(live)
    box_rules = _enabled(box["config"].get(Section.rules.value, []))
    results = [
        Tried(
            sample,
            rules.try_rules(live_rules, sample, types=types, teams=teams),
            rules.try_rules(box_rules, sample, types=types, teams=teams),
        )
        for sample in rules.SAMPLES
    ]
    return Trial(results, types, teams)


# Deploying


async def _deploy_rules(s: AsyncSession, caller: Caller, box: Sequence[Any]) -> None:
    rows = {str(r["id"]): r for r in await rule_repo.list_rules(s)}
    keep = {str(x["id"]) for x in box if x.get("id") in rows}
    for row_id, old in rows.items():
        if row_id not in keep:
            await rule_service.delete_in(s, caller, old)
    order: list[UUID] = []
    for x in box:
        rule = rule_service.parse(x["name"], x["event"], x["conditions"], x["actions"])
        row = rows.get(str(x.get("id")))
        if row is None:
            order.append((await rule_service.create_in(s, caller, rule, x["enabled"]))["id"])
            continue
        if any(row[f] != x[f] for f in rules.FIELDS[Section.rules]):
            await rule_service.change_in(s, caller, row, rule, x["enabled"])
        order.append(row["id"])
    await _reorder(s, caller, Area.rules, "Rule order", order, rule_repo.reorder_rules,
                   [r["id"] for r in await rule_repo.list_rules(s)])  # fmt: skip


async def _deploy_macros(s: AsyncSession, caller: Caller, box: Sequence[Any]) -> None:
    rows = {str(r["id"]): r for r in await macro_repo.list_macros(s)}
    keep = {str(x["id"]) for x in box if x.get("id") in rows}
    for row_id, old in rows.items():
        if row_id not in keep:
            await macros.delete_in(s, caller, old)
    order: list[UUID] = []
    for x in box:
        name, steps = macros.parse(x["name"], x["steps"])
        row = rows.get(str(x.get("id")))
        if row is None:
            order.append((await macros.create_in(s, caller, name, steps))["id"])
            continue
        if any(row[f] != x[f] for f in rules.FIELDS[Section.macros]):
            await macros.change_in(s, caller, row, name, steps)
        order.append(row["id"])
    await _reorder(s, caller, Area.macros, "Macro order", order, macro_repo.reorder_macros,
                   [r["id"] for r in await macro_repo.list_macros(s)])  # fmt: skip


async def _deploy_replies(s: AsyncSession, caller: Caller, box: Sequence[Any]) -> None:
    rows = {str(r["id"]): r for r in await reply_repo.list_replies(s, None)}
    keep = {str(x["id"]) for x in box if x.get("id") in rows}
    for row_id, old in rows.items():
        if row_id not in keep:
            await saved_replies.delete_in(s, caller, old)
    for x in box:
        row = rows.get(str(x.get("id")))
        if row is None:
            await saved_replies.create_in(s, caller, x["shortcut"], x["body"])
        elif row["shortcut"] != x["shortcut"] or row["body"] != x["body"]:
            await saved_replies.change_in(s, caller, row, x["shortcut"], x["body"])


async def _reorder(
    s: AsyncSession,
    caller: Caller,
    area: Area,
    target: str,
    wanted: list[UUID],
    reorder: Any,
    now: list[UUID],
) -> None:
    if wanted != now:
        await reorder(s, wanted)
        await audit.record(s, caller, area, "reordered", target)


async def deploy(sm: Sessionmaker, caller: Caller) -> dict[Section, Diff]:
    """Makes the live rules, macros and saved replies match the sandbox; the sandbox is
    then a fresh copy of the live settings. Returns what changed."""
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            box = await repo.get(s, lock=True)
            if box is None:
                raise NotFound(NO_SANDBOX)
            # Same locks as Reorder on the settings pages, so a save there waits for us.
            await rule_repo.lock_rules(s)
            await macro_repo.lock_macros(s)
            live = await _live(s)
            config = box["config"]
            done = _diffs(live, config)
            if all(d.empty for d in done.values()):
                raise Invalid("The sandbox is the same as the live settings: nothing to deploy.")
            current = ""
            try:
                current = "Rules"
                await _deploy_rules(s, caller, config.get("rules", []))
                current = "Macros"
                await _deploy_macros(s, caller, config.get("macros", []))
                current = "Saved replies"
                await _deploy_replies(s, caller, config.get("saved_replies", []))
            except (Invalid, NotFound) as exc:
                raise Invalid(f"Nothing was deployed. {current}: {exc}") from exc
            await repo.save(s, await _live(s), caller.member_id)
            await audit.record(
                s, caller, Area.sandbox, "deployed", "Sandbox",
                detail={sec.value: {"added": len(d.added), "changed": len(d.changed),
                                    "removed": len(d.removed)} for sec, d in done.items()},
            )  # fmt: skip
    except IntegrityError as exc:
        raise Conflict(
            "Nothing was deployed: two items would have the same name while saving (for "
            "example two names swapped). Rename one in the sandbox, deploy, then rename it back."
        ) from exc
    return done
