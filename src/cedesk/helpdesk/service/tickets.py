"""Creating, listing, opening and changing tickets.

Every change writes a system line on the ticket ("Priority set to High by Neha").
A new ticket first runs the automation rules (W15), which may set its type, team,
priority, status and tags. A ticket that lands in a team with nobody assigned is then
auto-assigned (W8): on create, and when it moves to another team without a chosen assignee.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events, paging
from cedesk.helpdesk.domain import tickets as rules
from cedesk.helpdesk.domain.auth import Caller, normalise_email
from cedesk.helpdesk.domain.paging import CursorError
from cedesk.helpdesk.domain.permissions import Action, Role
from cedesk.helpdesk.domain.rules import Event as RuleEvent
from cedesk.helpdesk.domain.sla import TicketSla
from cedesk.helpdesk.domain.tickets import (
    Channel,
    FieldDef,
    FieldError,
    Priority,
    Sentiment,
    Sort,
    Status,
)
from cedesk.helpdesk.repo import catalog, outbox
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import tickets as repo
from cedesk.helpdesk.repo.tickets import Row
from cedesk.helpdesk.service import idempotency, routing, sla
from cedesk.helpdesk.service import rules as automation
from cedesk.helpdesk.service.attachments import IncomingFiles, save_incoming
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.catalog import field_defs
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.idempotency import Idempotency
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
    # Filled by the Cevanos handover, triage and imports (md 4.1), not by the agent API.
    conversation_id: str | None = None
    accepted: bool = True  # a handover waits for an agent to accept it
    handover: Mapping[str, Any] | None = None  # the AI summary and reason
    topic: str | None = None
    sentiment: Sentiment | None = None
    language: str | None = None
    source: str | None = None  # where it came from: "cevanos", "intercom", "email", ...
    source_id: str | None = None  # that source's id; one ticket per (source, source_id)
    to_address: str | None = None  # email: the address the customer wrote to (for rules)
    # Email in: the first message's headers (`hd_message.email`) and its Message-ID.
    first_email: Mapping[str, Any] | None = None
    first_channel_message_id: str | None = None


def _check_origin(new: NewTicket) -> None:
    if new.language is not None and not rules.LANGUAGE.match(new.language):
        raise Invalid("language is a code such as hi, en or en-IN.")
    if new.source is not None and not rules.SOURCE.match(new.source):
        raise Invalid("source uses a-z, 0-9, - or _ (max 40).")
    if new.source_id is not None and (new.source is None or not 1 <= len(new.source_id) <= 200):
        raise Invalid("source_id needs a source and 1-200 characters.")
    if new.topic is not None and not 1 <= len(new.topic) <= 60:
        raise Invalid("topic is 1-60 characters.")
    if new.conversation_id is not None and not 1 <= len(new.conversation_id) <= 100:
        raise Invalid("conversation_id is 1-100 characters.")
    if new.to_address is not None and not 3 <= len(new.to_address) <= 254:
        raise Invalid("to_address is 3-254 characters.")


@dataclass(frozen=True)
class TicketView:
    ticket: Row
    messages: Sequence[Row]  # the latest page, oldest to newest
    other_tickets: int
    older_messages_cursor: str | None = None
    sla: TicketSla | None = None


@dataclass(frozen=True)
class TicketPage:
    tickets: Sequence[Row]
    counts: dict[str, int]
    next_cursor: str | None
    sla: Mapping[UUID, TicketSla] = field(default_factory=dict)


@dataclass(frozen=True)
class ContactTickets:
    contact: Row
    tickets: Sequence[Row]
    next_cursor: str | None
    sla: Mapping[UUID, TicketSla] = field(default_factory=dict)


CHANGED_BY_SOMEONE_ELSE = "This ticket was changed by someone else. Reload it and try again."


class Origin(Protocol):
    """Who a change comes from: a signed-in member (`Caller`) or a channel (no member)."""

    @property
    def workspace_id(self) -> UUID: ...

    @property
    def member_id(self) -> UUID | None: ...


@dataclass(frozen=True)
class ChannelOrigin:
    workspace_id: UUID
    member_id: None = None


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


async def _line(s: AsyncSession, caller: Origin, ticket_id: UUID, body: str) -> None:
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
    s: AsyncSession, caller: Origin, ticket_id: UUID, member_id: UUID, number: int, subject: str
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


async def _emit(
    s: AsyncSession,
    caller: Origin,
    ticket: Mapping[Any, Any],
    event: events.Event,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Writes the event in the caller's transaction (md 3.2 step 5)."""
    await outbox.add(
        s,
        caller.workspace_id,
        event.value,
        ticket["id"],
        events.payload(event, ticket, extra or {}),
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
        sla=sla.state(ticket, await sla.config(s)),
    )


async def create_ticket(
    sm: Sessionmaker, caller: Caller, new: NewTicket, *, idem: Idempotency | None = None
) -> TicketView:
    """`idem`: with an Idempotency-Key, a repeat returns the ticket the first one made."""
    require(caller, Action.create_ticket)
    try:
        tags = rules.clean_tags(new.tags)
    except FieldError as exc:
        raise _invalid(exc) from exc
    _check_origin(new)
    try:
        return await _create(sm, caller, caller, new, tags, idem=idem)
    except IntegrityError as exc:
        if "hd_ticket_source" in str(exc.orig):
            raise Conflict(f"A ticket already exists for {new.source} {new.source_id}.") from exc
        raise


async def create_from_channel(
    sm: Sessionmaker, workspace_id: UUID, new: NewTicket, *, files: IncomingFiles | None = None
) -> TicketView:
    """A ticket that arrived by itself (email in; later chat): no member, no permission
    check, no assignee given. Rules and auto-assignment run as for any new ticket.
    `files`: the ones that came with the first message (an email's attachments)."""
    if new.assignee_id is not None:
        raise Invalid("A channel can't choose the assignee.")
    try:
        tags = rules.clean_tags(new.tags)
    except FieldError as exc:
        raise _invalid(exc) from exc
    _check_origin(new)
    try:
        return await _create(sm, ChannelOrigin(workspace_id), None, new, tags, files)
    except IntegrityError as exc:
        if "hd_ticket_source" in str(exc.orig):
            raise Conflict(f"A ticket already exists for {new.source} {new.source_id}.") from exc
        raise


async def _create(
    sm: Sessionmaker,
    origin: Origin,
    caller: Caller | None,
    new: NewTicket,
    tags: list[str],
    files: IncomingFiles | None = None,
    *,
    idem: Idempotency | None = None,
) -> TicketView:
    """`caller` is the signed-in member, or None when a channel creates the ticket."""
    async with workspace_session(sm, origin.workspace_id) as s:
        if caller is not None:
            made = await idempotency.begin(s, caller, idem)
            if made is not None:
                return await _view(s, made)
        _, defs = await _type_defs(s, new.type_id)
        try:
            values = rules.clean_values(defs, new.fields)
        except FieldError as exc:
            raise _invalid(exc) from exc
        team = await _team_name(s, new.team_id)
        assignee = None
        if caller is not None:
            assignee = await _check_assignee(s, caller, new.assignee_id, None)
            who = await _who(s, caller)
        else:
            who = f"{new.contact_name} ({new.channel.value})"
        contact_id = await repo.upsert_contact(
            s, origin.workspace_id, normalise_email(new.contact_email), new.contact_name
        )
        ticket_id = await repo.insert_ticket(
            s,
            {
                "workspace_id": origin.workspace_id,
                "number": await repo.next_number(s, origin.workspace_id),
                "subject": new.subject,
                "priority": new.priority.value,
                "type_id": new.type_id,
                "team_id": new.team_id,
                "assignee_id": new.assignee_id,
                "contact_id": contact_id,
                "channel": new.channel.value,
                "fields": values,
                "tags": tags,
                "created_by_id": origin.member_id,
                "conversation_id": new.conversation_id,
                "accepted": new.accepted,
                "handover": dict(new.handover) if new.handover is not None else None,
                "topic": new.topic,
                "sentiment": new.sentiment.value if new.sentiment else None,
                "language": new.language,
                "source": new.source,
                "source_id": new.source_id,
            },
        )
        first_message: UUID | None = None
        if new.description:
            first_message = await repo.insert_message(
                s,
                workspace_id=origin.workspace_id,
                ticket_id=ticket_id,
                kind="customer",
                body=new.description,
                author_member_id=None,
                author_name=new.contact_name,
                email=dict(new.first_email) if new.first_email is not None else None,
                channel_message_id=new.first_channel_message_id,
            )
            await save_incoming(
                s, files, workspace_id=origin.workspace_id, ticket_id=ticket_id,
                message_id=first_message,
            )  # fmt: skip
        await _line(s, origin, ticket_id, f"Ticket created by {who}")
        if team:
            await _line(s, origin, ticket_id, f"Team set to {team} by {who}")
        if assignee:
            await _line(s, origin, ticket_id, f"Assigned to {assignee} by {who}")
        run = await automation.apply(
            s, origin.workspace_id, ticket_id, RuleEvent.created, to_address=new.to_address
        )
        team_id = run.after["team_id"] if run else new.team_id
        type_id = run.after["type_id"] if run else new.type_id
        status = run.after["status"] if run else Status.open.value
        if team_id and new.assignee_id is None and status != Status.resolved.value:
            pick = await routing.route(
                s,
                ticket_id=ticket_id,
                team_id=team_id,
                channel=new.channel.value,
                type_id=type_id,
            )
            await _line(s, origin, ticket_id, pick.line)
            if pick.agent:
                await repo.set_assignee(s, ticket_id, pick.agent.member_id)
                view = await _view(s, ticket_id)
                await _tell_assignee(
                    s, origin, ticket_id, pick.agent.member_id, view.ticket["number"], new.subject
                )
        await sla.refresh(s, ticket_ids=[ticket_id])
        view = await _view(s, ticket_id)
        await _emit(s, origin, view.ticket, events.Event.ticket_created)
        if first_message is not None:
            await _emit(s, origin, view.ticket, events.Event.message_created,
                        events.message(first_message, "customer", None))  # fmt: skip
        if view.ticket["assignee_id"] is not None:
            by = origin.member_id if new.assignee_id is not None else None  # None: routing
            await _emit(s, origin, view.ticket, events.Event.assignment_changed,
                        events.assignment(None, view.ticket["assignee_id"], by))  # fmt: skip
        if caller is not None:
            await idempotency.remember(s, caller, idem, ticket_id)
        return view


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
        shape = rules.CURSOR_SHAPE[sort]
        keys: list[rules.CursorKey] = [
            int(last[f"k{i}"]) if kind == "int" else str(last[f"k{i}"])
            for i, kind in enumerate(shape, 1)
        ]
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
        cfg = await sla.config(s)
    return TicketPage(
        tickets=rows,
        counts={st.value: counts.get(st.value, 0) for st in Status},
        next_cursor=next_cursor,
        sla={r["id"]: sla.state(r, cfg) for r in rows},
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
        cfg = await sla.config(s)
    return ContactTickets(
        contact=contact,
        tickets=rows,
        next_cursor=next_cursor,
        sla={r["id"]: sla.state(r, cfg) for r in rows},
    )


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
        before = await repo.get_contact(s, contact_id)
        if before is None or not await repo.update_contact(s, contact_id, name=name, vip=vip):
            raise NotFound("Customer not found.")
        contact = await repo.get_contact(s, contact_id)
        if contact is not None:
            if before["vip"] != contact["vip"]:  # VIP customers may have another SLA policy
                await sla.refresh(s, contact_id=contact_id)
            changed = events.contact(before, contact)
            if changed is not None:
                await outbox.add(s, caller.workspace_id, changed["event"], None, changed)
    if contact is None:
        raise NotFound("Customer not found.")
    return contact


async def resolve_ticket(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, *, if_match: str | None = None
) -> TicketView:
    """Resolves the ticket (T15), the same flow as setting the status to Resolved (T16).
    Resolving a ticket that is already resolved changes nothing.

    `change_ticket` writes the `ticket.resolved` event; CSAT (Phase 2) hooks in here."""
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
        rerouted = False  # routing chose the assignee (an event's `by` is then null)
        moved_team = "team_id" in changes and changes["team_id"] != current["team_id"]
        # A resolved ticket keeps the person who resolved it: routing it again would hand a
        # closed ticket to someone else and spend their round-robin turn.
        resolved = new["status"] == Status.resolved.value
        if (
            moved_team
            and new["team_id"] is not None
            and "assignee_id" not in changes
            and not resolved
        ):
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
            rerouted = True
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
            await sla.refresh(s, ticket_ids=[ticket_id])
            for body in lines:
                await _line(s, caller, ticket_id, body)
        if routed_to is not None:
            await _tell_assignee(s, caller, ticket_id, routed_to, current["number"], new["subject"])
        view = await _view(s, ticket_id)
        if lines:
            actor = None if rerouted else caller.member_id
            for event, extra in events.after_change(current, new, actor):
                await _emit(s, caller, view.ticket, event, extra)
        return view
