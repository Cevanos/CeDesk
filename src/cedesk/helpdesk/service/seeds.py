"""Puts the defaults from the mock into a workspace. (Imported by `service.auth`, so it
does not import it back.)"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import macros as macro_engine
from cedesk.helpdesk.domain import rules as engine
from cedesk.helpdesk.domain import seeds
from cedesk.helpdesk.domain.saved_replies import normalise_shortcut
from cedesk.helpdesk.domain.tickets import check_definitions
from cedesk.helpdesk.repo import catalog, defaults
from cedesk.helpdesk.repo import macros as macro_repo
from cedesk.helpdesk.repo import rules as rule_repo
from cedesk.helpdesk.repo import saved_replies as replies
from cedesk.helpdesk.repo.auth import workspace_for_slug
from cedesk.helpdesk.service.errors import NotFound


async def apply(s: AsyncSession, workspace_id: UUID) -> None:
    """Runs inside the transaction that creates the workspace, so a workspace never
    exists half-seeded."""
    for team in seeds.TEAMS:
        await catalog.insert_team(
            s, workspace_id, team.name, team.auto_assign, team.max_open_per_agent
        )
    for ticket_type in seeds.TICKET_TYPES:
        check_definitions(ticket_type.fields)
        fields = [
            {
                "key": f.key,
                "label": f.label,
                "kind": f.kind.value,
                "options": list(f.options),
                "required": f.required,
            }
            for f in ticket_type.fields
        ]
        await catalog.insert_type(s, workspace_id, ticket_type.name, fields, None)
    await apply_workflow(s, workspace_id)


async def apply_workflow(s: AsyncSession, workspace_id: UUID) -> int:
    """The Phase 2 defaults: saved replies, rules and macros. Safe to run again: each
    default is given once, so one an Admin deleted or renamed never comes back, and an
    Admin's own reply, rule or macro with the same shortcut or name is kept. A default
    whose team or type does not exist yet is left out, and added on a later run once it
    does. Returns how many were added."""
    given = await defaults.given(s)
    added = 0
    for reply in seeds.SAVED_REPLIES:
        shortcut = normalise_shortcut(reply.shortcut)
        if ("saved_reply", shortcut) in given:
            continue
        new_id = await replies.insert_reply(
            s,
            workspace_id,
            shortcut=shortcut,
            body=reply.body,
            source="default",
            created_by=None,
            skip_existing=True,
        )
        await defaults.mark_given(s, workspace_id, "saved_reply", shortcut)
        added += new_id is not None
    added += await _rules(s, workspace_id, given)
    added += await _macros(s, workspace_id, given)
    return added


async def _rules(s: AsyncSession, workspace_id: UUID, given: set[tuple[str, str]]) -> int:
    types = await rule_repo.name_ids(s, "hd_ticket_type")
    teams = await rule_repo.name_ids(s, "hd_team")
    added = 0
    for seed in seeds.RULES:
        if ("rule", seed.name) in given:
            continue
        actions: list[dict[str, Any]] = []
        for raw in seed.actions:
            action = dict(raw)
            if "type" in action:
                action["type_id"] = types.get(action.pop("type"))
            if "team" in action:
                action["team_id"] = teams.get(action.pop("team"))
            actions.append(action)
        if any(a.get("type_id", 0) is None or a.get("team_id", 0) is None for a in actions):
            continue  # its team or type was renamed or deleted: leave this one out
        rule = engine.parse_rule(UUID(int=0), seed.name, seed.event, seed.conditions, actions)
        new_id = await rule_repo.insert_rule(
            s,
            workspace_id,
            name=rule.name,
            enabled=True,
            event=rule.event.value,
            conditions=[engine.stored_condition(c) for c in rule.conditions],
            actions=[engine.stored_action(a) for a in rule.actions],
            created_by=None,
            skip_existing=True,
        )
        await defaults.mark_given(s, workspace_id, "rule", seed.name)
        added += new_id is not None
    return added


async def add_workflow_defaults(sm: async_sessionmaker[AsyncSession], slug: str) -> int:
    """For a workspace made before these defaults existed (`cedesk seed-workflow`)."""
    async with sm() as s, s.begin():
        workspace_id = await workspace_for_slug(s, slug.strip().lower())
    if workspace_id is None:
        raise NotFound("No workspace with that address.")
    async with workspace_session(sm, workspace_id) as s:
        return await apply_workflow(s, workspace_id)


async def _macros(s: AsyncSession, workspace_id: UUID, given: set[tuple[str, str]]) -> int:
    teams = await rule_repo.name_ids(s, "hd_team")
    added = 0
    for seed in seeds.MACROS:
        if ("macro", seed.name) in given:
            continue
        steps: list[dict[str, Any]] = []
        for raw in seed.steps:
            step = dict(raw)
            if "team" in step:
                step["team_id"] = teams.get(step.pop("team"))
            steps.append(step)
        if any(st.get("team_id", 0) is None for st in steps):
            continue  # its team was renamed or deleted: leave this one out
        parsed = macro_engine.parse_steps(steps)
        new_id = await macro_repo.insert_macro(
            s,
            workspace_id,
            name=seed.name,
            steps=[macro_engine.stored_step(x) for x in parsed],
            created_by=None,
            skip_existing=True,
        )
        await defaults.mark_given(s, workspace_id, "macro", seed.name)
        added += new_id is not None
    return added
