"""Demo and sample data (md D1-D3), for sales demos and for trying CeDesk out. Admin only
(`manage_access`, which no custom role has), audited, and only on a server that turned it
on (CEDESK_DEMO_ENABLED=true) and is not production.

- seed: 16 tickets. The 6 samples (new, untouched); 5 past tickets, resolved and rated,
  over the last week; 4 live ones assigned to the caller, who is working on them (a reply,
  a note, a task, time logged, one waiting on the customer); 1 chat handover nobody has
  accepted yet. 409 when demo tickets are already there.
- samples: the 6 samples only ("Load sample tickets"). 409 when they are already there.
- reset: makes the demo again, then deletes the old demo tickets and the demo customers
  left with no ticket.

Only demo data is ever touched: tickets with `source = 'demo'`. Tickets made by hand,
imported or from a channel stay, and so do members and settings.

Safe to repeat and to run twice at once: each call holds a per-workspace advisory lock
for its whole run. Built through the same services as the API (customers write in
through a channel, so the rules, triage, routing and SLA run on every ticket), so the new
tickets are made in their own transactions; when one step fails, the tickets made so far
are deleted again, and reset deletes the old ones only after the new ones are all made,
so the workspace never ends up without its demo. The tickets come from
`domain/seeds.py`; `cedesk seed-demo` makes a whole demo workspace with its members.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import seeds
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.seeds import DemoTicket
from cedesk.helpdesk.domain.ticket_tools import DueIn
from cedesk.helpdesk.domain.tickets import Status
from cedesk.helpdesk.repo import demo as repo
from cedesk.helpdesk.service import audit, csat, messages, ticket_tools, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.messages import MessageKind
from cedesk.helpdesk.service.tickets import NewTicket
from cedesk.storage.base import ObjectStore

logger = logging.getLogger("cedesk.demo")

NOT_HERE = "Demo data is never on a production server (CEDESK_ENV=production)."
TURNED_OFF = (
    "Demo data is off on this server. Its operator turns it on with CEDESK_DEMO_ENABLED=true."
)
ALREADY = "Demo data is already there; use reset."
SAMPLES_ALREADY = "The sample tickets are already there; use reset."

Made = list[UUID]  # the tickets made so far, deleted again if a step fails


@dataclass(frozen=True)
class DemoResult:
    created: int
    deleted: int = 0


def _check(caller: Caller, *, enabled: bool, production: bool) -> None:
    # First: a clear answer about the server, whoever asks.
    if production:
        raise Forbidden(NOT_HERE)
    if not enabled:
        raise Forbidden(TURNED_OFF)
    require(caller, Action.manage_access)


async def seed(sm: Sessionmaker, caller: Caller, *, enabled: bool, production: bool) -> DemoResult:
    _check(caller, enabled=enabled, production=production)
    made: Made = []
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            await repo.lock(s, caller.workspace_id)
            if await repo.demo_ticket_ids(s):
                raise Conflict(ALREADY)
            await _seed(sm, caller, made)
            await audit.record(s, caller, Area.demo, "seed", "demo data",
                               detail={"created": len(made)})  # fmt: skip
    except BaseException:
        await _undo(sm, caller.workspace_id, made)
        raise
    return DemoResult(len(made))


async def samples(
    sm: Sessionmaker, caller: Caller, *, enabled: bool, production: bool
) -> DemoResult:
    _check(caller, enabled=enabled, production=production)
    made: Made = []
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            await repo.lock(s, caller.workspace_id)
            if await repo.demo_subjects(s, [t.subject for t in seeds.DEMO_SAMPLES]):
                raise Conflict(SAMPLES_ALREADY)
            await _samples(sm, caller, made)
            await audit.record(s, caller, Area.demo, "samples", "demo data",
                               detail={"created": len(made)})  # fmt: skip
    except BaseException:
        await _undo(sm, caller.workspace_id, made)
        raise
    return DemoResult(len(made))


async def reset(
    sm: Sessionmaker, store: ObjectStore, caller: Caller, *, enabled: bool, production: bool
) -> DemoResult:
    """The new demo first, then the old demo tickets go in one transaction (with the
    audit line). A failure on the way rolls that back and deletes what was made, so the
    old demo stays as it was."""
    _check(caller, enabled=enabled, production=production)
    made: Made = []
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            await repo.lock(s, caller.workspace_id)
            old = await repo.demo_ticket_ids(s)
            await _seed(sm, caller, made)
            keys = await repo.file_keys(s, old)
            deleted = await repo.delete_tickets(s, old)
            await repo.delete_unused_contacts(s)
            await audit.record(s, caller, Area.demo, "reset", "demo data",
                               detail={"deleted": deleted, "created": len(made)})  # fmt: skip
    except BaseException:
        await _undo(sm, caller.workspace_id, made)
        raise
    for key in keys:  # files an agent added to an old demo ticket (the seed makes none)
        try:
            await store.delete(key)
        except Exception:
            logger.warning("demo file not deleted from storage", extra={"event": "demo"})
    return DemoResult(len(made), deleted)


async def _undo(sm: Sessionmaker, workspace_id: UUID, made: Made) -> None:
    """Deletes the demo tickets made by a call that failed half-way (after its own
    transaction rolled back)."""
    if not made:
        return
    try:
        async with workspace_session(sm, workspace_id) as s:
            await repo.delete_tickets(s, made)
            await repo.delete_unused_contacts(s)
    except Exception:
        logger.exception("demo tickets of a failed call not deleted", extra={"event": "demo"})


async def _arrive(
    sm: Sessionmaker,
    caller: Caller,
    made: Made,
    t: DemoTicket,
    *,
    handover: dict[str, str] | None = None,
) -> UUID:
    """The customer writes in (a handover waits to be accepted)."""
    new = NewTicket(
        subject=t.subject,
        contact_email=t.email,
        contact_name=t.name,
        description=t.body,
        priority=t.priority,
        channel=t.channel,
        language=t.language,
        source=seeds.DEMO_SOURCE,
        accepted=handover is None,
        handover=handover,
    )
    view = await tickets.create_from_channel(sm, caller.workspace_id, new)
    ticket_id = UUID(str(view.ticket["id"]))
    made.append(ticket_id)
    return ticket_id


async def _take(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> None:
    view = await tickets.get_ticket(sm, caller, ticket_id)
    if view.ticket["assignee_id"] != caller.member_id:
        await tickets.change_ticket(sm, caller, ticket_id, {"assignee_id": str(caller.member_id)})


async def _samples(sm: Sessionmaker, caller: Caller, made: Made) -> None:
    for t in seeds.DEMO_SAMPLES:
        await _arrive(sm, caller, made, t)


async def _seed(sm: Sessionmaker, caller: Caller, made: Made) -> None:
    await _samples(sm, caller, made)
    for past in seeds.DEMO_PAST:
        ticket_id = await _arrive(sm, caller, made, past.ticket)
        await _take(sm, caller, ticket_id)
        await messages.post_message(sm, caller, ticket_id, kind=MessageKind.reply, body=past.reply)
        await tickets.resolve_ticket(sm, caller, ticket_id)
        await csat.demo_rating(sm, caller.workspace_id, ticket_id, past.score, past.comment)
        async with workspace_session(sm, caller.workspace_id) as s:
            await repo.backdate(
                s,
                caller.workspace_id,
                ticket_id,
                ago=timedelta(days=past.days_ago),
                took=timedelta(hours=past.hours),
            )
    for live in seeds.DEMO_LIVE:
        ticket_id = await _arrive(sm, caller, made, live.ticket)
        await _take(sm, caller, ticket_id)
        if live.reply:
            await messages.post_message(
                sm, caller, ticket_id, kind=MessageKind.reply, body=live.reply
            )
        if live.note:
            await messages.post_message(
                sm, caller, ticket_id, kind=MessageKind.note, body=live.note
            )
        if live.task:
            await ticket_tools.add_task(
                sm, caller, ticket_id, text=live.task, owner_id=None, due_in=DueIn.four_hours,
                due_at=None,
            )  # fmt: skip
        if live.minutes:
            await ticket_tools.log_time(
                sm, caller, ticket_id, minutes=live.minutes, note=None, billable=False
            )
        if live.pending:
            await tickets.change_ticket(sm, caller, ticket_id, {"status": Status.pending.value})
    h = seeds.DEMO_HANDOVER
    await _arrive(sm, caller, made, h.ticket, handover={"summary": h.summary, "reason": h.reason})
