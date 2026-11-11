"""SLA breach alerts (md W14): the worker, once a minute, tells the assignee and the
Supervisors of the ticket's team when an open ticket's SLA clock runs out.

A ticket is alerted once per clock: when `sla_due_at` (the current clock's deadline) has
passed and is not the one already alerted for (`sla_alerted_due`). The first-reply clock
and then the resolve clock can each alert; a due time moved later (a new priority) and
missed again alerts again. Nobody to tell (unassigned, no team Supervisor): marked anyway,
the pill shows the breach.
"""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.sla_settings import breach_note
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import sla as repo
from cedesk.helpdesk.service.auth import Sessionmaker

BATCH = 200  # tickets per workspace per pass; the rest come next minute
KIND = "sla_breach"


async def alert_workspace(
    sm: Sessionmaker, workspace_id: UUID, now: datetime | None = None, batch: int = BATCH
) -> int:
    """One workspace, one transaction. Returns how many tickets were alerted."""
    now = now or datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        tickets = await repo.newly_breached(s, now, batch)
        if not tickets:
            return 0
        teams = sorted({t["team_id"] for t in tickets if t["team_id"] is not None})
        supervisors: dict[UUID, list[UUID]] = defaultdict(list)
        for row in await repo.team_supervisors(s, teams) if teams else ():
            supervisors[row["team_id"]].append(row["member_id"])
        assignees = await repo.active_members(
            s, [t["assignee_id"] for t in tickets if t["assignee_id"] is not None]
        )
        for t in tickets:
            people = [t["assignee_id"]] if t["assignee_id"] in assignees else []
            people += [m for m in supervisors.get(t["team_id"], []) if m not in people]
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
        await repo.mark_alerted(s, [t["id"] for t in tickets])
        return len(tickets)


async def alert_everywhere(sm: Sessionmaker, now: datetime | None = None) -> int:
    """Every workspace (the worker, once a minute). Returns how many tickets were alerted."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    return sum([await alert_workspace(sm, ws, now) for ws in workspace_ids])
