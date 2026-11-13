"""Demo and sample data (md D1-D3), for sales demos and for trying CeDesk out. Admin only,
audited, and off on a production server (CEDESK_ENV=production).

- seed: 16 tickets. The 6 samples (new, untouched); 5 past tickets, resolved and rated,
  over the last week; 4 live ones assigned to the caller, who is working on them (a reply,
  a note, a task, time logged, one waiting on the customer); 1 chat handover nobody has
  accepted yet.
- samples: the 6 samples only ("Load sample tickets").
- reset: deletes every ticket of the workspace (with its messages, tasks, ratings and so
  on), then seeds again. Contacts, members and settings stay.

Built through the same services as the API: customers write in through a channel, so the
rules, triage, routing and SLA run on every ticket. The tickets come from
`domain/seeds.py`; `cedesk seed-demo` makes a whole demo workspace with its members.
"""

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
from cedesk.helpdesk.service.errors import Forbidden
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.messages import MessageKind
from cedesk.helpdesk.service.tickets import NewTicket

NOT_HERE = "Demo data is off on a production server (CEDESK_ENV=production)."
SEED_COUNT = (
    len(seeds.DEMO_SAMPLES) + len(seeds.DEMO_PAST) + len(seeds.DEMO_LIVE) + 1  # the handover
)


@dataclass(frozen=True)
class DemoResult:
    created: int
    deleted: int = 0


def _check(caller: Caller, allowed: bool) -> None:
    if not allowed:  # first: a clear answer on production, whoever asks
        raise Forbidden(NOT_HERE)
    require(caller, Action.settings)


async def seed(sm: Sessionmaker, caller: Caller, *, allowed: bool) -> DemoResult:
    _check(caller, allowed)
    made = await _seed(sm, caller)
    await _audit(sm, caller, "seed", made)
    return DemoResult(made)


async def samples(sm: Sessionmaker, caller: Caller, *, allowed: bool) -> DemoResult:
    _check(caller, allowed)
    made = await _samples(sm, caller)
    await _audit(sm, caller, "samples", made)
    return DemoResult(made)


async def reset(sm: Sessionmaker, caller: Caller, *, allowed: bool) -> DemoResult:
    _check(caller, allowed)
    async with workspace_session(sm, caller.workspace_id) as s:  # audited with the wipe
        deleted = await repo.delete_tickets(s, caller.workspace_id)
        await audit.record(s, caller, Area.demo, "reset", "demo data", detail={"deleted": deleted})
    made = await _seed(sm, caller)
    await _audit(sm, caller, "seed", made)
    return DemoResult(made, deleted)


async def _audit(sm: Sessionmaker, caller: Caller, action: str, made: int) -> None:
    async with workspace_session(sm, caller.workspace_id) as s:
        await audit.record(s, caller, Area.demo, action, "demo data", detail={"created": made})


async def _arrive(
    sm: Sessionmaker, caller: Caller, t: DemoTicket, *, handover: dict[str, str] | None = None
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
    return UUID(str(view.ticket["id"]))


async def _take(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> None:
    view = await tickets.get_ticket(sm, caller, ticket_id)
    if view.ticket["assignee_id"] != caller.member_id:
        await tickets.change_ticket(sm, caller, ticket_id, {"assignee_id": str(caller.member_id)})


async def _samples(sm: Sessionmaker, caller: Caller) -> int:
    for t in seeds.DEMO_SAMPLES:
        await _arrive(sm, caller, t)
    return len(seeds.DEMO_SAMPLES)


async def _seed(sm: Sessionmaker, caller: Caller) -> int:
    made = await _samples(sm, caller)
    for past in seeds.DEMO_PAST:
        ticket_id = await _arrive(sm, caller, past.ticket)
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
        made += 1
    for live in seeds.DEMO_LIVE:
        ticket_id = await _arrive(sm, caller, live.ticket)
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
        made += 1
    h = seeds.DEMO_HANDOVER
    await _arrive(sm, caller, h.ticket, handover={"summary": h.summary, "reason": h.reason})
    return made + 1
