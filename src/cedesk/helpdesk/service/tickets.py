"""Creating, listing, opening and changing tickets.

Every change writes a system line on the ticket ("Priority set to High by Neha").
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import tickets as rules
from cedesk.helpdesk.domain.auth import Caller, normalise_email
from cedesk.helpdesk.domain.permissions import Action, Role
from cedesk.helpdesk.domain.tickets import Channel, FieldDef, FieldError, Priority, Status
from cedesk.helpdesk.repo import catalog
from cedesk.helpdesk.repo import tickets as repo
from cedesk.helpdesk.repo.tickets import Row
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.catalog import field_defs
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

SCOPES = ("everyone", "mine", "unassigned")


@dataclass(frozen=True)
class NewTicket:
    subject: str
    contact_email: str
    contact_name: str
    description: str | None = None
    priority: Priority = Priority.medium
    channel: Channel = Channel.web
    type_id: UUID | None = None
    team_id: UUID | None = None
    assignee_id: UUID | None = None
    fields: Mapping[str, object] = field(default_factory=dict)
    tags: Sequence[str] = ()


@dataclass(frozen=True)
class TicketView:
    ticket: Row
    messages: Sequence[Row]
    other_tickets: int


@dataclass(frozen=True)
class TicketPage:
    tickets: Sequence[Row]
    counts: dict[str, int]
    next_offset: int | None


def _invalid(exc: FieldError) -> Invalid:
    return Invalid(str(exc))


async def _who(s: AsyncSession, caller: Caller) -> str:
    me = await repo.member_brief(s, caller.member_id)
    return str(me["name"]) if me else "someone"


async def _type_defs(s: AsyncSession, type_id: UUID | None) -> tuple[str | None, list[FieldDef]]:
    if type_id is None:
        return None, []
    row = await catalog.get_type(s, type_id)
    if row is None:
        raise Invalid("That ticket type does not exist.")
    return str(row["name"]), field_defs(row["fields"])


async def _team_name(s: AsyncSession, team_id: UUID | None) -> str | None:
    if team_id is None:
        return None
    row = await catalog.get_team(s, team_id)
    if row is None:
        raise Invalid("That team does not exist.")
    return str(row["name"])


async def _check_assignee(
    s: AsyncSession, caller: Caller, new: UUID | None, current: UUID | None
) -> str | None:
    """Agents may take an unassigned ticket or drop their own; moving anyone else's
    ticket, or giving one to someone else, needs `assign_others`. Returns the new name."""
    if new == current:
        return None
    if current not in (None, caller.member_id) or new not in (None, caller.member_id):
        require(caller, Action.assign_others)
    else:
        require(caller, Action.assign_self)
    if new is None:
        return None
    member = await repo.member_brief(s, new)
    if member is None or not member["is_active"] or member["role"] == Role.light_agent.value:
        raise Invalid("That person can't be assigned tickets.")
    return str(member["name"])


async def _line(s: AsyncSession, caller: Caller, ticket_id: UUID, body: str) -> None:
    await repo.insert_message(
        s,
        workspace_id=caller.workspace_id,
        ticket_id=ticket_id,
        kind="system",
        body=body,
        author_member_id=None,
        author_name=None,
    )


async def _view(s: AsyncSession, ticket_id: UUID) -> TicketView:
    ticket = await repo.ticket_detail(s, ticket_id)
    if ticket is None:
        raise NotFound("Ticket not found.")
    return TicketView(
        ticket=ticket,
        messages=await repo.messages(s, ticket_id),
        other_tickets=await repo.other_ticket_count(s, ticket["contact_id"], ticket_id),
    )


async def create_ticket(sm: Sessionmaker, caller: Caller, new: NewTicket) -> TicketView:
    require(caller, Action.create_ticket)
    try:
        tags = rules.clean_tags(new.tags)
    except FieldError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        _, defs = await _type_defs(s, new.type_id)
        try:
            values = rules.clean_values(defs, new.fields)
        except FieldError as exc:
            raise _invalid(exc) from exc
        team = await _team_name(s, new.team_id)
        assignee = await _check_assignee(s, caller, new.assignee_id, None)
        who = await _who(s, caller)
        contact_id = await repo.upsert_contact(
            s, caller.workspace_id, normalise_email(new.contact_email), new.contact_name
        )
        ticket_id = await repo.insert_ticket(
            s,
            {
                "workspace_id": caller.workspace_id,
                "number": await repo.next_number(s, caller.workspace_id),
                "subject": new.subject,
                "priority": new.priority.value,
                "type_id": new.type_id,
                "team_id": new.team_id,
                "assignee_id": new.assignee_id,
                "contact_id": contact_id,
                "channel": new.channel.value,
                "fields": values,
                "tags": tags,
                "created_by_id": caller.member_id,
            },
        )
        if new.description:
            await repo.insert_message(
                s,
                workspace_id=caller.workspace_id,
                ticket_id=ticket_id,
                kind="customer",
                body=new.description,
                author_member_id=None,
                author_name=new.contact_name,
            )
        await _line(s, caller, ticket_id, f"Ticket created by {who}")
        if team:
            await _line(s, caller, ticket_id, f"Team set to {team} by {who}")
        if assignee:
            await _line(s, caller, ticket_id, f"Assigned to {assignee} by {who}")
        return await _view(s, ticket_id)


async def get_ticket(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> TicketView:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _view(s, ticket_id)


def _like(q: str) -> str:
    escaped = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


async def list_tickets(
    sm: Sessionmaker,
    caller: Caller,
    *,
    status: Status | None,
    scope: str,
    priority: Priority | None,
    team_id: UUID | None,
    q: str | None,
    limit: int,
    offset: int,
) -> TicketPage:
    """Open and Pending: most urgent first, then oldest. Resolved: newest first.

    `counts` is per status and ignores the other filters, for the tabs.
    """
    require(caller, Action.read_ticket)
    if scope not in SCOPES:
        raise Invalid("scope is everyone, mine or unassigned.")
    q = (q or "").strip() or None
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.list_tickets(
            s,
            me=caller.member_id,
            status=status.value if status else None,
            priority=priority.value if priority else None,
            team=team_id,
            scope=scope,
            q_like=_like(q) if q else None,
            q_number=rules.number_in_query(q) if q else None,
            limit=limit + 1,
            offset=offset,
        )
        counts = await repo.counts_by_status(s)
    return TicketPage(
        tickets=rows[:limit],
        counts={st.value: counts.get(st.value, 0) for st in Status},
        next_offset=offset + limit if len(rows) > limit else None,
    )


async def change_ticket(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, changes: Mapping[str, Any]
) -> TicketView:
    """Applies only the keys present in `changes`:
    subject, status, priority, type_id, team_id, assignee_id,
    fields (a value of None clears that field), add_tags, remove_tags."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.lock_ticket(s, ticket_id)
        if current is None:
            raise NotFound("Ticket not found.")
        new = {k: current[k] for k in ("subject", "status", "priority", "type_id", "team_id")}
        new |= {"assignee_id": current["assignee_id"], "reopen_count": current["reopen_count"]}
        new["fields"] = dict(current["fields"])
        new["tags"] = list(current["tags"])
        lines: list[str] = []
        who = await _who(s, caller)

        if any(k != "assignee_id" for k in changes):
            require(caller, Action.change_fields)

        if "subject" in changes and changes["subject"] != current["subject"]:
            new["subject"] = changes["subject"]
            lines.append(f"Subject changed by {who}")

        if "status" in changes and changes["status"] != current["status"]:
            status = Status(changes["status"])
            if current["status"] == Status.resolved.value:
                new["reopen_count"] += 1
            new["status"] = status.value
            lines.append(f"Status set to {rules.title(status.value)} by {who}")

        if "priority" in changes and changes["priority"] != current["priority"]:
            new["priority"] = Priority(changes["priority"]).value
            lines.append(f"Priority set to {rules.title(new['priority'])} by {who}")

        type_changed = "type_id" in changes and changes["type_id"] != current["type_id"]
        if type_changed:
            new["type_id"] = changes["type_id"]
            type_name, _ = await _type_defs(s, new["type_id"])
            lines.append(
                f"Type set to {type_name} by {who}" if type_name else f"Type cleared by {who}"
            )

        if type_changed or "fields" in changes:
            _, defs = await _type_defs(s, new["type_id"])
            keys = {d.key for d in defs}
            merged = {k: v for k, v in new["fields"].items() if k in keys}
            merged |= dict(changes.get("fields") or {})
            try:
                cleaned = rules.clean_values(defs, merged)
            except FieldError as exc:
                raise _invalid(exc) from exc
            for d in defs:
                before, after = current["fields"].get(d.key), cleaned.get(d.key)
                if before != after:
                    lines.append(
                        f"{d.label} set to {after} by {who}"
                        if after is not None
                        else f"{d.label} cleared by {who}"
                    )
            new["fields"] = cleaned

        if "team_id" in changes and changes["team_id"] != current["team_id"]:
            new["team_id"] = changes["team_id"]
            team = await _team_name(s, new["team_id"])
            lines.append(f"Team set to {team} by {who}" if team else f"Team cleared by {who}")

        if "assignee_id" in changes and changes["assignee_id"] != current["assignee_id"]:
            new["assignee_id"] = changes["assignee_id"]
            name = await _check_assignee(s, caller, new["assignee_id"], current["assignee_id"])
            lines.append(f"Assigned to {name} by {who}" if name else f"Unassigned by {who}")

        if changes.get("add_tags") or changes.get("remove_tags"):
            try:
                add = rules.clean_tags(changes.get("add_tags") or [])
                remove = set(rules.clean_tags(changes.get("remove_tags") or []))
                tags = rules.clean_tags([t for t in new["tags"] if t not in remove] + add)
            except FieldError as exc:
                raise _invalid(exc) from exc
            lines += [f"Tag {t} added by {who}" for t in tags if t not in current["tags"]]
            lines += [f"Tag {t} removed by {who}" for t in current["tags"] if t not in tags]
            new["tags"] = tags

        if lines:
            await repo.update_ticket(s, ticket_id, new)
            for body in lines:
                await _line(s, caller, ticket_id, body)
        return await _view(s, ticket_id)
