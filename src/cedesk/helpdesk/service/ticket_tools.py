"""Ticket tools (md X1-X4, X6, X8, X9): merge, links, child tickets, approvals, tasks
and the time log.

Every change writes a grey line on the ticket, so the history says who did what.
Light agents can read all of it but change none of it (md 6.6): merging, linking and
child tickets need `change_fields`; approval requests, tasks and time need `reply`;
deciding an approval needs `approve`.

Not here yet: X7 (passing a decision to a Cevanos Governance hold) needs a Cevanos `/v1`
route; until then CeDesk records the decision only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import ticket_tools as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.ticket_tools import Decision, DueIn, ToolError
from cedesk.helpdesk.domain.tickets import Channel, Status, label
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import ticket_tools as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.ticket_tools import Row
from cedesk.helpdesk.service import children, scope, sla, tickets
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.tickets import NewTicket, TicketView

RELATED_LIMIT = 50
WAITING_LIMIT = 100
NOT_FOUND = "Ticket not found."


def _invalid(exc: ToolError) -> Invalid:
    return Invalid(str(exc))


async def _who(s: AsyncSession, caller: Caller) -> str:
    me = await ticket_repo.member_brief(s, caller.member_id)
    return str(me["name"]) if me else "someone"


async def _ticket(s: AsyncSession, ticket_id: UUID) -> Row:
    row = await repo.brief(s, ticket_id)
    if row is None:
        raise NotFound(NOT_FOUND)
    return row


async def _line(s: AsyncSession, workspace_id: UUID, ticket_id: UUID, body: str) -> None:
    await ticket_repo.insert_message(
        s,
        workspace_id=workspace_id,
        ticket_id=ticket_id,
        kind="system",
        body=body[:50_000],
        author_member_id=None,
        author_name=None,
    )
    await repo.touch(s, ticket_id)


async def _notify(
    s: AsyncSession, caller: Caller, member_id: UUID, ticket: Row, kind: str, body: str
) -> None:
    if member_id == caller.member_id:
        return
    await message_repo.notify(
        s,
        workspace_id=caller.workspace_id,
        member_ids=[member_id],
        ticket_id=ticket["id"],
        kind=kind,
        body=body[:500],
    )


async def _member_who_can(s: AsyncSession, member_id: UUID, action: Action, problem: str) -> str:
    """The member's name, when they are active and their role may do `action`."""
    member = await ticket_repo.member_brief(s, member_id)
    if member is None or not member["is_active"] or not member_access(member).can(action):
        raise Invalid(problem)
    return str(member["name"])


# --- Merge (X1) ---


async def merge(sm: Sessionmaker, caller: Caller, ticket_id: UUID, into_id: UUID) -> TicketView:
    """Merges the ticket into another open one (md 6.7): its customer, agent, AI and note
    messages are copied into the target in time order (their files move with them), its
    open tasks and waiting approvals move there, and it is resolved with `merged_into_id`
    set. No CSAT and no events come from it."""
    require(caller, Action.reply)
    require(caller, Action.change_fields)
    if ticket_id == into_id:
        raise Invalid("A ticket can't be merged into itself.")
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        await scope.check(s, caller, into_id, edit=True)
        found = await repo.lock(s, [ticket_id, into_id])
        source, target = found.get(ticket_id), found.get(into_id)
        if source is None:
            raise NotFound(NOT_FOUND)
        if target is None:
            raise Invalid("The ticket to merge into doesn't exist.")
        if source["merged_into_id"] is not None:
            raise Conflict("This ticket was already merged into another one.")
        if target["merged_into_id"] is not None or target["status"] == Status.resolved.value:
            raise Conflict("Merge into an open ticket. That one is resolved.")
        if source["source"] == "cevanos" and source["status"] != Status.resolved.value:
            raise Conflict("Resolve the live chat first, then merge it.")
        await children.refuse_open_children(s, ticket_id)
        who = await _who(s, caller)
        copied = await repo.copy_messages(s, ticket_id, into_id)
        tasks_moved, approvals_moved = await repo.move_open_work(s, ticket_id, into_id)
        await repo.mark_merged(s, ticket_id, into_id)
        await children.child_resolved(s, caller.workspace_id, source, who, caller.member_id)
        await _line(
            s, caller.workspace_id, ticket_id, f"Merged into {label(target['number'])} by {who}"
        )
        word = "message" if copied == 1 else "messages"
        await _line(
            s,
            caller.workspace_id,
            into_id,
            f"{label(source['number'])} merged into this ticket by {who} · {copied} {word} copied",
        )
        moved = rules.moved_work(tasks_moved, approvals_moved)
        if moved:
            await _line(
                s, caller.workspace_id, into_id, f"Moved from {label(source['number'])}: {moved}"
            )
        await sla.refresh(s, ticket_ids=[ticket_id, into_id])
    return await tickets.get_ticket(sm, caller, into_id)


# --- Links (X2) and the Related card ---


@dataclass(frozen=True)
class Related:
    parent: Row | None
    merged_into: Row | None
    children: Sequence[Row]
    links: Sequence[Row]
    merged_from: Sequence[Row]


async def related(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> Related:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=False)
        ticket = await _ticket(s, ticket_id)
        vt = scope.visible_team(caller)
        lists = await repo.related(s, ticket_id, RELATED_LIMIT, vt)
        return Related(
            parent=await repo.related_one(s, ticket["parent_id"], vt),
            merged_into=await repo.related_one(s, ticket["merged_into_id"], vt),
            children=lists["children"],
            links=lists["links"],
            merged_from=lists["merged_from"],
        )


async def link(sm: Sessionmaker, caller: Caller, ticket_id: UUID, other_id: UUID) -> Related:
    """Links two tickets both ways (X2). Linking them again changes nothing."""
    require(caller, Action.change_fields)
    try:
        a, b = rules.link_pair(ticket_id, other_id)
    except ToolError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        await scope.check(s, caller, other_id, edit=False)
        ticket = await _ticket(s, ticket_id)
        other = await repo.brief(s, other_id)
        if other is None:
            raise Invalid("The ticket to link doesn't exist.")
        if await repo.add_link(s, caller.workspace_id, a, b, caller.member_id):
            who = await _who(s, caller)
            await _line(
                s, caller.workspace_id, ticket_id, f"Linked to {label(other['number'])} by {who}"
            )
            await _line(
                s, caller.workspace_id, other_id, f"Linked to {label(ticket['number'])} by {who}"
            )
    return await related(sm, caller, ticket_id)


async def unlink(sm: Sessionmaker, caller: Caller, ticket_id: UUID, other_id: UUID) -> None:
    require(caller, Action.change_fields)
    try:
        a, b = rules.link_pair(ticket_id, other_id)
    except ToolError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        ticket = await _ticket(s, ticket_id)
        other = await repo.brief(s, other_id)
        if other is None or not await repo.remove_link(s, a, b):
            raise NotFound("These tickets aren't linked.")
        who = await _who(s, caller)
        await _line(
            s, caller.workspace_id, ticket_id, f"Unlinked from {label(other['number'])} by {who}"
        )
        await _line(
            s, caller.workspace_id, other_id, f"Unlinked from {label(ticket['number'])} by {who}"
        )


# --- Child tickets (X3) ---


async def create_child(
    sm: Sessionmaker,
    caller: Caller,
    parent_id: UUID,
    *,
    team_id: UUID,
    subject: str | None,
    note: str | None,
    assignee_id: UUID | None,
    idem: Idempotency | None = None,
) -> TicketView:
    """A ticket for another team, made from this one (md 6.7): it copies the customer,
    the type and the fields; its channel is email if the parent's is, otherwise portal.
    Rules and routing run as on any new ticket, and the parent gets a line."""
    require(caller, Action.create_ticket)
    require(caller, Action.change_fields)
    try:
        text_note = rules.clean_text(note, "note", required=False)
    except ToolError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, parent_id, edit=True)
        parent = await _ticket(s, parent_id)
    if parent["merged_into_id"] is not None:
        raise Conflict("This ticket was merged. Make the child ticket from the one it went into.")
    title = (subject or "").strip() or str(parent["subject"])
    default_note = f"Opened from {label(parent['number'])}: {parent['subject']}"
    channel = Channel.email if parent["channel"] == Channel.email.value else Channel.portal
    new = NewTicket(
        subject=title,
        contact_email=str(parent["contact_email"]),
        contact_name=str(parent["contact_name"]),
        channel=channel,
        type_id=parent["type_id"],
        team_id=team_id,
        assignee_id=assignee_id,
        fields=dict(parent["fields"]),
        parent_id=parent_id,
        note=text_note or default_note[: rules.TEXT_MAX],
    )
    return await tickets.create_ticket(sm, caller, new, idem=idem, handoff=True)


# --- Approvals (X6) ---


async def request_approval(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    approver_id: UUID,
    what: str | None,
    amount: Decimal | None,
    currency: str | None,
) -> Row:
    """Asks a Supervisor, an Admin (or later a custom role with `approve`) to approve
    something on the ticket. Never yourself, never a light agent."""
    require(caller, Action.reply)
    try:
        value, code = rules.check_amount(amount, currency)
        text_what = rules.clean_text(what, "request", required=False)
    except ToolError as exc:
        raise _invalid(exc) from exc
    if approver_id == caller.member_id:
        raise Invalid("Ask someone else to approve it.")
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        ticket = await _ticket(s, ticket_id)
        text_what = text_what or rules.default_what(value, code, ticket["fields"].get("order_id"))
        if text_what is None:
            raise Invalid("Write what needs approving.")
        approver = await _member_who_can(
            s, approver_id, Action.approve, "That person can't approve requests."
        )
        approval_id = await repo.insert_approval(
            s,
            caller.workspace_id,
            ticket_id=ticket_id,
            requester_id=caller.member_id,
            approver_id=approver_id,
            what=text_what,
            amount=value,
            currency=code,
        )
        who = await _who(s, caller)
        await _line(
            s,
            caller.workspace_id,
            ticket_id,
            f"Approval asked from {approver} by {who}: {text_what}",
        )
        await _notify(
            s,
            caller,
            approver_id,
            ticket,
            "approval",
            f"{who} asks you to approve on {label(ticket['number'])}: {text_what}",
        )
        row = await repo.get_approval(s, approval_id)
        assert row is not None
        return row


async def decide_approval(
    sm: Sessionmaker, caller: Caller, approval_id: UUID, *, decision: Decision, note: str | None
) -> Row:
    """Approve or reject (from the bell or the ticket). The named approver decides, or an
    Admin; never the person who asked. A decision is final."""
    require(caller, Action.approve)
    try:
        text_note = rules.clean_text(note, "note", required=False)
    except ToolError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        approval = await repo.get_approval(s, approval_id, lock=True)
        if approval is None:
            raise NotFound("Approval request not found.")
        if approval["requester_id"] == caller.member_id:
            raise Forbidden("You can't decide your own request.")
        if approval["approver_id"] != caller.member_id and not caller.can(Action.settings):
            raise Forbidden(f"Only {approval['approver_name']} or an Admin can decide this.")
        if approval["state"] != "pending":
            raise Conflict(
                f"This request was already {approval['state']} "
                f"by {approval['decided_by_name'] or 'someone'}."
            )
        state = "approved" if decision is Decision.approve else "rejected"
        await repo.decide(s, approval_id, state=state, note=text_note, by=caller.member_id)
        who = await _who(s, caller)
        ticket = await _ticket(s, approval["ticket_id"])
        said = f"{state.capitalize()} by {who}: {approval['what']}"
        await _line(
            s, caller.workspace_id, ticket["id"], said + (f" · {text_note}" if text_note else "")
        )
        await _notify(
            s,
            caller,
            approval["requester_id"],
            ticket,
            "approval",
            f"{said} (on {label(ticket['number'])})",
        )
        row = await repo.get_approval(s, approval_id)
        assert row is not None
        return row


async def ticket_approvals(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=False)
        await _ticket(s, ticket_id)
        return await repo.ticket_approvals(s, ticket_id)


async def waiting_approvals(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    """The requests waiting for the caller's decision, oldest first."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.waiting_for(s, caller.member_id, WAITING_LIMIT)


# --- Tasks (X8) ---


def _due(due_in: DueIn | None, due_at: datetime | None, timezone: str) -> datetime | None:
    if due_in is not None and due_at is not None:
        raise Invalid("Send due_in or due_at, not both.")
    if due_at is not None:
        if due_at.tzinfo is None:
            raise Invalid("due_at needs a time zone, e.g. 2026-11-13T09:00:00+05:30.")
        return due_at
    if due_in is not None:
        return rules.due_at(due_in, datetime.now(UTC), timezone)
    return None


async def add_task(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    text: str,
    owner_id: UUID | None,
    due_in: DueIn | None,
    due_at: datetime | None,
) -> Row:
    """A to-do on the ticket. The owner (the caller unless given) is told."""
    require(caller, Action.reply)
    try:
        body = rules.clean_text(text, "task", required=True)
    except ToolError as exc:
        raise _invalid(exc) from exc
    assert body is not None
    owner = owner_id or caller.member_id
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        ticket = await _ticket(s, ticket_id)
        owner_name = await _member_who_can(s, owner, Action.reply, "That person can't own tasks.")
        when = _due(due_in, due_at, (await sla.config(s)).calendar.timezone)
        task_id = await repo.insert_task(
            s,
            caller.workspace_id,
            ticket_id=ticket_id,
            owner_id=owner,
            text_=body,
            due_at=when,
            by=caller.member_id,
        )
        who = await _who(s, caller)
        await _line(
            s, caller.workspace_id, ticket_id, f"Task for {owner_name} added by {who}: {body}"
        )
        await _notify(
            s,
            caller,
            owner,
            ticket,
            "task",
            f"{who} gave you a task on {label(ticket['number'])}: {body}",
        )
        row = await repo.get_task(s, task_id)
        assert row is not None
        return row


async def change_task(
    sm: Sessionmaker, caller: Caller, task_id: UUID, changes: Mapping[str, Any]
) -> Row:
    """Applies the keys present in `changes`: text, owner_id, due_in or due_at (None
    clears it), done. The owner, the person who added it, or someone who may assign
    others' tickets can change a task."""
    require(caller, Action.reply)
    async with workspace_session(sm, caller.workspace_id) as s:
        task = await repo.get_task(s, task_id, lock=True)
        if task is None:
            raise NotFound("Task not found.")
        mine = caller.member_id in (task["owner_id"], task["created_by"])
        if not mine and not caller.can(Action.assign_others):
            raise Forbidden("Only the task's owner or the person who added it can change it.")
        ticket = await _ticket(s, task["ticket_id"])
        who = await _who(s, caller)
        lines: list[str] = []
        body = str(task["text"])
        if "text" in changes:
            try:
                cleaned = rules.clean_text(changes["text"], "task", required=True)
            except ToolError as exc:
                raise _invalid(exc) from exc
            assert cleaned is not None
            body = cleaned
        owner = task["owner_id"]
        if changes.get("owner_id") not in (None, owner):
            owner = changes["owner_id"]
            name = await _member_who_can(s, owner, Action.reply, "That person can't own tasks.")
            lines.append(f"Task given to {name} by {who}: {body}")
            await _notify(
                s,
                caller,
                owner,
                ticket,
                "task",
                f"{who} gave you a task on {label(ticket['number'])}: {body}",
            )
        due = task["due_at"]
        if "due_in" in changes or "due_at" in changes:
            due_in = changes.get("due_in")
            due_at = changes.get("due_at")
            due = _due(due_in, due_at, (await sla.config(s)).calendar.timezone)
        done_at, done_by = task["done_at"], task["done_by"]
        if "done" in changes and bool(changes["done"]) != (done_at is not None):
            if changes["done"]:
                done_at, done_by = datetime.now(UTC), caller.member_id
                lines.append(f"Task done by {who}: {body}")
            else:
                done_at, done_by = None, None
                lines.append(f"Task reopened by {who}: {body}")
        await repo.update_task(
            s, task_id, text_=body, owner_id=owner, due_at=due, done_at=done_at, done_by=done_by
        )
        for line in lines:
            await _line(s, caller.workspace_id, ticket["id"], line)
        row = await repo.get_task(s, task_id)
        assert row is not None
        return row


async def ticket_tasks(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=False)
        await _ticket(s, ticket_id)
        return await repo.ticket_tasks(s, ticket_id)


async def my_tasks(sm: Sessionmaker, caller: Caller, limit: int) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.open_tasks_of(s, caller.member_id, limit)


# --- Time log (X9) ---


@dataclass(frozen=True)
class TimeLog:
    entries: Sequence[Row]
    total_minutes: int
    billable_minutes: int


async def log_time(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    minutes: int,
    note: str | None,
    billable: bool,
) -> Row:
    require(caller, Action.reply)
    try:
        rules.check_minutes(minutes)
        text_note = rules.clean_text(note, "note", required=False)
    except ToolError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        await _ticket(s, ticket_id)
        entry_id = await repo.insert_time(
            s,
            caller.workspace_id,
            ticket_id=ticket_id,
            member_id=caller.member_id,
            minutes=minutes,
            note=text_note,
            billable=billable,
        )
        row = await repo.get_time(s, entry_id)
        assert row is not None
        return row


async def time_log(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> TimeLog:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=False)
        await _ticket(s, ticket_id)
        total, billable = await repo.time_totals(s, ticket_id)
        return TimeLog(await repo.ticket_time(s, ticket_id), total, billable)


async def delete_time(sm: Sessionmaker, caller: Caller, entry_id: UUID) -> None:
    """Your own entry; an Admin can remove anyone's."""
    require(caller, Action.reply)
    async with workspace_session(sm, caller.workspace_id) as s:
        entry = await repo.get_time(s, entry_id)
        if entry is None:
            raise NotFound("Time entry not found.")
        if entry["member_id"] != caller.member_id and not caller.can(Action.settings):
            raise Forbidden("You can remove only your own time entries.")
        await repo.delete_time(s, entry_id)
