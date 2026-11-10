"""Macros (md W18): Admins set them up; agents run them on a ticket in one click.

Running a macro is one transaction: its steps run in order, and if any step is refused
nothing is saved. It needs the right to reply (light agents can't run macros), and 409
until a handover is accepted (md C7). A team step re-routes the ticket like a team change
does (W8), unless the ticket ends up resolved.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain import macros as engine
from cedesk.helpdesk.domain import rules as rule_engine
from cedesk.helpdesk.domain import tickets as ticket_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import FieldError, Priority, Status
from cedesk.helpdesk.repo import macros as repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.repo import rules as rule_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.macros import Row
from cedesk.helpdesk.service import idempotency, routing, sla, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.messages import MessageKind, add_message
from cedesk.helpdesk.service.tickets import CHANGED_BY_SOMEONE_ELSE, TicketView

TAKEN = "Another macro already has that name."
NOT_ACCEPTED = "Accept the handover first."


@dataclass(frozen=True)
class MacroView:
    row: Row
    problems: list[str]


def _parse(name: str, steps: Sequence[Any]) -> tuple[str, tuple[engine.Step, ...]]:
    name = " ".join(name.split())
    if not 1 <= len(name) <= engine.NAME_MAX:
        raise Invalid(f"A macro name is 1-{engine.NAME_MAX} characters.")
    try:
        return name, engine.parse_steps(steps)
    except engine.MacroError as exc:
        raise Invalid(str(exc)) from exc


async def _views(s: AsyncSession, rows: Sequence[Row]) -> list[MacroView]:
    teams = await rule_repo.names(s, "hd_team")
    found = []
    for row in rows:
        try:
            problems = engine.problems(engine.parse_steps(row["steps"]), teams)
        except engine.MacroError:
            problems = ["Its steps no longer read; save it again."]
        found.append(MacroView(row, problems))
    return found


async def _check_team(s: AsyncSession, steps: Sequence[engine.Step]) -> None:
    if engine.problems(steps, await rule_repo.names(s, "hd_team")):
        raise Invalid("A macro can only set a team that exists.")


# Settings (Admin); the list is for everyone's Macros menu


async def list_macros(sm: Sessionmaker, caller: Caller) -> list[MacroView]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _views(s, await repo.list_macros(s))


async def create_macro(
    sm: Sessionmaker, caller: Caller, *, name: str, steps: Sequence[Any]
) -> MacroView:
    require(caller, Action.settings)
    name, parsed = _parse(name, steps)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            # A soft cap: two Admins adding at the same moment can pass it by one each.
            if await repo.count(s) >= engine.MAX_MACROS:
                raise Invalid(f"A workspace can have at most {engine.MAX_MACROS} macros.")
            await _check_team(s, parsed)
            macro_id = await repo.insert_macro(
                s,
                caller.workspace_id,
                name=name,
                steps=[engine.stored_step(x) for x in parsed],
                created_by=caller.member_id,
            )
            assert macro_id is not None
            row = await repo.get_macro(s, macro_id)
            assert row is not None
            return (await _views(s, [row]))[0]
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def change_macro(
    sm: Sessionmaker,
    caller: Caller,
    macro_id: UUID,
    *,
    name: str | None,
    steps: Sequence[Any] | None,
) -> MacroView:
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            current = await repo.get_macro(s, macro_id)
            if current is None:
                raise NotFound("Macro not found.")
            new_name, parsed = _parse(
                current["name"] if name is None else name,
                current["steps"] if steps is None else steps,
            )
            if steps is not None:
                await _check_team(s, parsed)
            await repo.update_macro(
                s, macro_id, name=new_name, steps=[engine.stored_step(x) for x in parsed]
            )
            row = await repo.get_macro(s, macro_id)
            assert row is not None
            return (await _views(s, [row]))[0]
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def delete_macro(sm: Sessionmaker, caller: Caller, macro_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.delete_macro(s, macro_id):
            raise NotFound("Macro not found.")


async def reorder_macros(
    sm: Sessionmaker, caller: Caller, macro_ids: Sequence[UUID]
) -> list[MacroView]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = set(await repo.lock_macros(s))
        if set(macro_ids) != current or len(macro_ids) != len(current):
            raise Invalid("Send every macro id exactly once, in the new order.")
        await repo.reorder_macros(s, macro_ids)
        return await _views(s, await repo.list_macros(s))


# Running one


async def run_macro(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    macro_id: UUID,
    *,
    if_match: str | None = None,
    idem: Idempotency | None = None,
) -> TicketView:
    """`idem`: with an Idempotency-Key, a repeat runs nothing again (a macro can send a
    reply) and returns the ticket as it is now."""
    require(caller, Action.reply)
    try:
        expected = ticket_rules.parse_if_match(if_match) if if_match is not None else None
    except FieldError as exc:
        raise Invalid(str(exc)) from exc

    async with workspace_session(sm, caller.workspace_id) as s:
        if await idempotency.begin(s, caller, idem) is not None:
            return await tickets.get_ticket(sm, caller, ticket_id)
        macro = await repo.get_macro(s, macro_id)
        if macro is None:
            raise NotFound("Macro not found.")
        try:
            steps = engine.parse_steps(macro["steps"])
        except engine.MacroError as exc:
            raise Invalid("This macro needs fixing in Settings first.") from exc
        if engine.changes_ticket(steps):
            require(caller, Action.change_fields)

        before = await ticket_repo.lock_ticket(s, ticket_id)
        detail = await ticket_repo.ticket_detail(s, ticket_id)
        if before is None or detail is None:
            raise NotFound("Ticket not found.")
        if expected is not None and before["updated_at"] != expected:
            raise Conflict(CHANGED_BY_SOMEONE_ELSE)
        if not detail["accepted"]:
            raise Conflict(NOT_ACCEPTED)

        me = await ticket_repo.member_brief(s, caller.member_id)
        who = str(me["name"]) if me else "someone"
        teams = await rule_repo.names(s, "hd_team")
        state = rule_engine.State(
            type_id=before["type_id"],
            team_id=before["team_id"],
            priority=Priority(before["priority"]),
            status=Status(before["status"]),
            tags=tuple(before["tags"]),
        )
        done: list[str] = []
        for step in steps:
            if isinstance(step, engine.Reply | engine.Note):
                kind = MessageKind.reply if isinstance(step, engine.Reply) else MessageKind.note
                await add_message(s, caller, detail, kind=kind, body=step.text, who=who)
                done.append("Reply sent" if kind is MessageKind.reply else "Note added")
            else:
                state, said = rule_engine.act(step, state, {}, teams)
                if said:
                    done.append(said)

        after: dict[str, Any] = {
            k: before[k] for k in ("subject", "type_id", "assignee_id", "reopen_count")
        } | {
            "status": state.status.value,
            "priority": state.priority.value,
            "team_id": state.team_id,
            "tags": list(state.tags),
            "fields": dict(before["fields"]),
        }
        if before["status"] == Status.resolved.value and state.status is not Status.resolved:
            after["reopen_count"] += 1

        routed_line: str | None = None
        routed_to: UUID | None = None
        moved = state.team_id != before["team_id"]
        if moved and state.team_id is not None and state.status is not Status.resolved:
            # Re-routing replaces the assignee: taking a ticket from someone else needs the
            # same right as reassigning it (as in change_ticket).
            if before["assignee_id"] not in (None, caller.member_id):
                require(caller, Action.assign_others)
            pick = await routing.route(
                s,
                ticket_id=ticket_id,
                team_id=state.team_id,
                channel=str(before["channel"]),
                type_id=state.type_id,
            )
            routed_to = pick.agent.member_id if pick.agent else None
            after["assignee_id"] = routed_to
            routed_line = pick.line

        await ticket_repo.update_ticket(s, ticket_id, after)
        await sla.refresh(s, ticket_ids=[ticket_id])
        lines = [engine.line(str(macro["name"]), who, done)]
        lines += [routed_line] if routed_line else []
        for body in lines:
            await ticket_repo.insert_message(
                s,
                workspace_id=caller.workspace_id,
                ticket_id=ticket_id,
                kind="system",
                body=body,
                author_member_id=None,
                author_name=None,
            )
        if routed_to is not None and routed_to != caller.member_id:
            await message_repo.notify(
                s,
                workspace_id=caller.workspace_id,
                member_ids=[routed_to],
                ticket_id=ticket_id,
                kind="assigned",
                body=f"You were auto-assigned {ticket_rules.label(before['number'])}: "
                f"{after['subject']}"[:500],
            )
        final = await ticket_repo.ticket_detail(s, ticket_id)
        assert final is not None
        actor = None if routed_line else caller.member_id
        for event, extra in events.after_change(before, after, actor):
            await outbox.add(
                s,
                caller.workspace_id,
                event.value,
                ticket_id,
                events.payload(event, final, extra),
            )
        await idempotency.remember(s, caller, idem, ticket_id)
    return await tickets.get_ticket(sm, caller, ticket_id)
