"""Overdue tasks (md X8): the worker, once a minute, tells a task's owner when it passes
its due time.

Once per due time: `alerted_due` keeps the due time the owner was told about, so moving
the due time later and missing it again alerts again. A due time set in the past (by
hand) is marked at once and never alerted: the person setting it already knows. An
owner who was deactivated is not told; the task is marked anyway.
"""

from datetime import UTC, datetime
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.ticket_tools import overdue_note
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import ticket_tools as repo
from cedesk.helpdesk.service.auth import Sessionmaker

BATCH = 200  # tasks per workspace per pass; the rest come next minute
KIND = "task_overdue"


async def alert_workspace(
    sm: Sessionmaker, workspace_id: UUID, now: datetime | None = None, batch: int = BATCH
) -> int:
    """One workspace, one transaction. Returns how many tasks were alerted."""
    now = now or datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        tasks = await repo.overdue_tasks(s, now, batch)
        if not tasks:
            return 0
        await repo.mark_tasks_alerted(s, [t["id"] for t in tasks])
        for t in tasks:
            if t["owner_active"]:
                await message_repo.notify(
                    s,
                    workspace_id=workspace_id,
                    member_ids=[t["owner_id"]],
                    ticket_id=t["ticket_id"],
                    kind=KIND,
                    body=overdue_note(label(int(t["ticket_number"])), str(t["text"])),
                )
        return len(tasks)


async def alert_everywhere(sm: Sessionmaker, now: datetime | None = None) -> int:
    """Every workspace (the worker, once a minute). Returns how many tasks were alerted."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    return sum([await alert_workspace(sm, ws, now) for ws in workspace_ids])
