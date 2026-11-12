"""SLA breach alerts (md W14): the worker, once a minute, tells the assignee and the
Supervisors of the ticket's team when an open ticket's SLA clock runs out.

A ticket is alerted once per clock: when `sla_due_at` (the current clock's deadline) has
passed and is not the one already alerted for (`sla_alerted_due`). The first-reply clock
and then the resolve clock can each alert; a due time moved later (a new priority) and
missed again alerts again. Nobody to tell (unassigned, no team Supervisor): marked anyway,
the pill shows the breach.

The `sla_breached` rules run first, so the alert goes to whoever the ticket belongs to
after them (a rule that moves it to Escalations tells Escalations' Supervisors). The
breach is marked before the rules run: a rule that changes the priority moves the due
time, and a due time it puts in the future must alert again when it passes.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain.reports import IMPORT_SOURCES
from cedesk.helpdesk.domain.rules import Event as RuleEvent
from cedesk.helpdesk.domain.sla_settings import breach_note
from cedesk.helpdesk.domain.tickets import Status, label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.repo import sla as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import routing, sla
from cedesk.helpdesk.service import rules as automation
from cedesk.helpdesk.service.auth import Sessionmaker

BATCH = 200  # tickets per workspace per pass; the rest come next minute
KIND = "sla_breach"


async def _run_rules(
    s: AsyncSession, workspace_id: UUID, ticket: Any
) -> tuple[UUID | None, UUID | None]:
    """The `sla_breached` rules on one ticket, routing it again if one moved the team.
    Returns the assignee and team it ends with."""
    run = await automation.apply(s, workspace_id, ticket["id"], RuleEvent.sla_breached)
    if run is None:
        return ticket["assignee_id"], ticket["team_id"]
    after = run.after
    if (
        run.team_changed
        and after["team_id"] is not None
        and after["status"] != Status.resolved.value
    ):
        pick = await routing.route(
            s,
            ticket_id=ticket["id"],
            team_id=after["team_id"],
            channel=str(run.before["channel"]),
            type_id=after["type_id"],
        )
        after["assignee_id"] = pick.agent.member_id if pick.agent else None
        await ticket_repo.set_assignee(s, ticket["id"], after["assignee_id"])
        await ticket_repo.insert_message(
            s,
            workspace_id=workspace_id,
            ticket_id=ticket["id"],
            kind="system",
            body=pick.line,
            author_member_id=None,
            author_name=None,
        )
    await sla.refresh(s, ticket_ids=[ticket["id"]])
    detail = await ticket_repo.ticket_detail(s, ticket["id"])
    assert detail is not None
    for event, extra in events.after_change(run.before, after, None):
        await outbox.add(
            s, workspace_id, event.value, ticket["id"], events.payload(event, detail, extra)
        )
    return after["assignee_id"], after["team_id"]


async def alert_workspace(
    sm: Sessionmaker, workspace_id: UUID, now: datetime | None = None, batch: int = BATCH
) -> int:
    """One workspace, one transaction. Returns how many tickets were alerted."""
    now = now or datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        tickets = await repo.newly_breached(s, now, batch, IMPORT_SOURCES)
        if not tickets:
            return 0
        await repo.mark_alerted(s, [t["id"] for t in tickets])
        owners: dict[UUID, tuple[UUID | None, UUID | None]] = {
            t["id"]: (t["assignee_id"], t["team_id"]) for t in tickets
        }
        if await automation.any_enabled(s, RuleEvent.sla_breached):
            for t in tickets:
                owners[t["id"]] = await _run_rules(s, workspace_id, t)

        teams = sorted({team for _, team in owners.values() if team is not None})
        supervisors: dict[UUID, list[UUID]] = defaultdict(list)
        for row in await repo.team_supervisors(s, teams) if teams else ():
            supervisors[row["team_id"]].append(row["member_id"])
        assignees = await repo.active_members(s, [a for a, _ in owners.values() if a is not None])
        for t in tickets:
            assignee, team = owners[t["id"]]
            people = [assignee] if assignee in assignees else []
            people += [m for m in supervisors.get(team, []) if m not in people] if team else []
            if people:
                await message_repo.notify(
                    s,
                    workspace_id=workspace_id,
                    member_ids=people,
                    ticket_id=t["id"],
                    kind=KIND,
                    body=breach_note(
                        label(int(t["number"])),
                        str(t["subject"]),
                        first_reply=t["first_reply_at"] is None,
                    ),
                )
        return len(tickets)


async def alert_everywhere(sm: Sessionmaker, now: datetime | None = None) -> int:
    """Every workspace (the worker, once a minute). Returns how many tickets were alerted."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    return sum([await alert_workspace(sm, ws, now) for ws in workspace_ids])
