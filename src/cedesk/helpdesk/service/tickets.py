"""Creating, listing, opening and changing tickets.

Every change writes a system line on the ticket ("Priority set to High by Neha").
A ticket that lands in a team with nobody assigned is auto-assigned (W8): on create,
and when it moves to another team without a chosen assignee.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain import tickets as rules
from cedesk.helpdesk.domain.auth import Caller, normalise_email
from cedesk.helpdesk.domain.paging import CursorError
from cedesk.helpdesk.domain.permissions import Action, Role
from cedesk.helpdesk.domain.tickets import (
    Channel,
    FieldDef,
    FieldError,
    Priority,
    Sort,
    Status,
)
from cedesk.helpdesk.repo import catalog
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import tickets as repo
from cedesk.helpdesk.repo.tickets import Row
from cedesk.helpdesk.service import routing
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.catalog import field_defs
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
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
    messages: Sequence[Row]  # the latest page, oldest to newest
    other_tickets: int
    older_messages_cursor: str | None = None


@dataclass(frozen=True)
class TicketPage:
    tickets: Sequence[Row]
    counts: dict[str, int]
    next_cursor: str | None


@dataclass(frozen=True)
class ContactTickets:
    contact: Row
    tickets: Sequence[Row]
    next_cursor: str | None


CHANGED_BY_SOMEONE_ELSE = "This ticket was changed by someone else. Reload it and try again."


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


async def _tell_assignee(
    s: AsyncSession, caller: Caller, ticket_id: UUID, member_id: UUID, number: int, subject: str
) -> None:
    if member_id == caller.member_id:
        return
    await message_repo.notify(
        s,
        workspace_id=caller.workspace_id,
        member_ids=[member_id],
        ticket_id=ticket_id,
        kind="assigned",
        body=f"You were auto-assigned {rules.label(number)}: {subject}"[:500],
    )


async def _view(s: AsyncSession, ticket_id: UUID) -> TicketView:
    ticket = await repo.ticket_detail(s, ticket_id)
    if ticket is None:
        raise NotFound("Ticket not found.")
    newest = await repo.messages(s, ticket_id, limit=paging.DEFAULT_LIMIT + 1)
    page, older = paging.time_page(newest, paging.DEFAULT_LIMIT, "message")
    return TicketView(
        ticket=ticket,
        messages=list(reversed(page)),
        other_tickets=await repo.other_ticket_count(s, ticket["contact_id"], ticket_id),
        older_messages_cursor=older,
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
        if new.team_id and new.assignee_id is None:
            pick = await routing.route(
                s,
                ticket_id=ticket_id,
                team_id=new.team_id,
                channel=new.channel.value,
                type_id=new.type_id,
            )
            await _line(s, caller, ticket_id, pick.line)
            if pick.agent:
                await repo.set_assignee(s, ticket_id, pick.agent.member_id)
                view = await _view(s, ticket_id)
                await _tell_assignee(
                    s, caller, ticket_id, pick.agent.member_id, view.ticket["number"], new.subject
                )
        return await _view(s, ticket_id)


async def get_ticket(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> TicketView:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _view(s, ticket_id)


def _like(q: str) -> str:
    escaped = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


async def _page(
    s: AsyncSession,
    caller: Caller,
    *,
    status: Status | None,
    scope: str,
    priority: Priority | None,
    team_id: UUID | None,
    contact_id: UUID | None,
    q: str | None,
    sort: Sort,
    cursor: str | None,
    limit: int,
) -> tuple[Sequence[Row], str | None]:
    try:
        after = rules.decode_cursor(cursor, sort) if cursor else None
    except CursorError as exc:
        raise Invalid(str(exc)) from exc
    rows = await repo.list_tickets(
        s,
        me=caller.member_id,
        status=status.value if status else None,
        priority=priority.value if priority else None,
        team=team_id,
        contact=contact_id,
        scope=scope,
        q_like=_like(q) if q else None,
        q_number=rules.number_in_query(q) if q else None,
        sort=sort.value,
        after=after,
        limit=limit + 1,
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        keys = (int(last["k1"]), int(last["k2"]), str(last["k3"]), int(last["k4"]))
        next_cursor = rules.encode_cursor(sort, keys)
    return page, next_cursor


async def list_tickets(
    sm: Sessionmaker,
    caller: Caller,
    *,
    status: Status | None,
    scope: str,
    priority: Priority | None,
    team_id: UUID | None,
    q: str | None,
    sort: Sort = Sort.priority,
    cursor: str | None = None,
    limit: int = 50,
) -> TicketPage:
    """A page of tickets in `sort` order; pass `next_cursor` back as `cursor` for the
    next page (the same filters and sort must be sent again).

    `counts` is per status and ignores the other filters, for the tabs.
    """
    require(caller, Action.read_ticket)
    if scope not in SCOPES:
        raise Invalid("scope is everyone, mine or unassigned.")
    q = (q or "").strip() or None
    async with workspace_session(sm, caller.workspace_id) as s:
        rows, next_cursor = await _page(
            s,
            caller,
            status=status,
            scope=scope,
            priority=priority,
            team_id=team_id,
            contact_id=None,
            q=q,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )
        counts = await repo.counts_by_status(s)
    return TicketPage(
        tickets=rows,
        counts={st.value: counts.get(st.value, 0) for st in Status},
        next_cursor=next_cursor,
    )


async def contact_tickets(
    sm: Sessionmaker,
    caller: Caller,
    contact_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> ContactTickets:
    """The customer card and every ticket of that customer, newest first (T19)."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        contact = await repo.get_contact(s, contact_id)
        if contact is None:
            raise NotFound("Customer not found.")
        rows, next_cursor = await _page(
            s,
            caller,
            status=None,
            scope="everyone",
            priority=None,
            team_id=None,
            contact_id=contact_id,
            q=None,
            sort=Sort.newest,
            cursor=cursor,
            limit=limit,
        )
    return ContactTickets(contact=contact, tickets=rows, next_cursor=next_cursor)


async def change_contact(
    sm: Sessionmaker,
    caller: Caller,
    contact_id: UUID,
    *,
    name: str | None = None,
    vip: bool | None = None,
) -> Row:
    """Edits the customer card: name and the VIP flag (T19)."""
    require(caller, Action.change_fields)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.update_contact(s, contact_id, name=name, vip=vip):
            raise NotFound("Customer not found.")
        contact = await repo.get_contact(s, contact_id)
    if contact is None:
        raise NotFound("Customer not found.")
    return contact


async def resolve_ticket(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, *, if_match: str | None = None
) -> TicketView:
    """Resolves the ticket (T15), the same flow as setting the status to Resolved (T16).
    Resolving a ticket that is already resolved changes nothing.

    CSAT (Phase 2) and the `ticket.resolved` event (outbox) hook in here."""
    require(caller, Action.reply)
    return await change_ticket(
        sm, caller, ticket_id, {"status": Status.resolved.value}, if_match=if_match
    )


async def change_ticket(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    changes: Mapping[str, Any],
    *,
    if_match: str | None = None,
) -> TicketView:
    """Applies only the keys present in `changes`:
    subject, status, priority, type_id, team_id, assignee_id,
    fields (a value of None clears that field), add_tags, remove_tags.

    With `if_match` (the ETag from reading the ticket), the change is refused with
    Conflict if anyone changed the ticket since (md 2B)."""
    require(caller, Action.read_ticket)
    try:
        expected = rules.parse_if_match(if_match) if if_match is not None else None
    except FieldError as exc:
        raise _invalid(exc) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.lock_ticket(s, ticket_id)
        if current is None:
            raise NotFound("Ticket not found.")
        # Checked under the row lock, so two agents saving at once can't both pass.
        if expected is not None and current["updated_at"] != expected:
            raise Conflict(CHANGED_BY_SOMEONE_ELSE)
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
            new_type, defs = await _type_defs(s, new["type_id"])
            values: dict[str, Any] = dict(current["fields"])
            dropped: dict[str, str] = {}
            try:
                if type_changed:
                    # Values that don't fit the new type are dropped, each with a line.
                    values, dropped = rules.carry_over(defs, values)
                # Only the values sent are checked, so old values never block an edit.
                for key, value in rules.clean_changes(defs, changes.get("fields") or {}).items():
                    if value is None:
                        values.pop(key, None)
                    else:
                        values[key] = value
            except FieldError as exc:
                raise _invalid(exc) from exc
            if type_changed:
                missing = rules.first_missing_required(defs, values)
                if missing:
                    raise Invalid(f"{missing.label} is required.")
                _, old_defs = await _type_defs(s, current["type_id"])
                old_labels = {d.key: d.label for d in old_defs}
                why = {
                    "missing": f"not in {new_type}" if new_type else "type cleared",
                    "invalid": f"not valid for {new_type}",
                }
                order = [d.key for d in old_defs] + sorted(set(dropped) - set(old_labels))
                for key in (k for k in order if k in dropped):
                    label = old_labels.get(key, key)
                    lines.append(f"{label} cleared by {who} ({why[dropped[key]]})")
            for d in defs:
                before, after = current["fields"].get(d.key), values.get(d.key)
                if d.key not in dropped and before != after:
                    lines.append(
                        f"{d.label} set to {after} by {who}"
                        if after is not None
                        else f"{d.label} cleared by {who}"
                    )
            new["fields"] = values

        if "team_id" in changes and changes["team_id"] != current["team_id"]:
            new["team_id"] = changes["team_id"]
            team = await _team_name(s, new["team_id"])
            lines.append(f"Team set to {team} by {who}" if team else f"Team cleared by {who}")

        if "assignee_id" in changes and changes["assignee_id"] != current["assignee_id"]:
            new["assignee_id"] = changes["assignee_id"]
            name = await _check_assignee(s, caller, new["assignee_id"], current["assignee_id"])
            lines.append(f"Assigned to {name} by {who}" if name else f"Unassigned by {who}")

        routed_to: UUID | None = None
        moved_team = "team_id" in changes and changes["team_id"] != current["team_id"]
        if moved_team and new["team_id"] is not None and "assignee_id" not in changes:
            # Re-routing replaces the assignee, so taking a ticket away from someone else
            # needs the same right as reassigning it directly (B1). Routing an unassigned
            # ticket, or the caller's own, is the system's choice, as on create.
            if current["assignee_id"] not in (None, caller.member_id):
                require(caller, Action.assign_others)
            pick = await routing.route(
                s,
                ticket_id=ticket_id,
                team_id=new["team_id"],
                channel=current["channel"],
                type_id=new["type_id"],
            )
            routed_to = pick.agent.member_id if pick.agent else None
            new["assignee_id"] = routed_to
            lines.append(pick.line)

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
        if routed_to is not None:
            await _tell_assignee(s, caller, ticket_id, routed_to, current["number"], new["subject"])
        return await _view(s, ticket_id)
