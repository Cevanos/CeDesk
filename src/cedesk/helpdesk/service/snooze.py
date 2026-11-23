"""Waking snoozed tickets (feature-gap plan 1.1); rules in domain/snooze.py.

Snoozing and unsnoozing go through `tickets.change_ticket` (the route, and bulk changes).
Here is the worker's part: once a minute, in each workspace, every snoozed ticket whose
time has come is set back to Open, with a line on the ticket, `ticket.updated`, and a
notification for the assignee (`snooze_ended`) and the followers (`following`). One
transaction per workspace pass; tickets another worker has locked are skipped (it wakes
them). Each woken ticket then runs the `snooze_ended` rules and workflows (plan 5.1). A
customer reply wakes a ticket early (service/customer_replies.py; no `snooze_ended`).
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain import snooze as rules
from cedesk.helpdesk.domain.rules import Event as RuleEvent
from cedesk.helpdesk.domain.tickets import Status, label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import catalog, outbox
from cedesk.helpdesk.repo import inbox as repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import automation, followers, sla
from cedesk.helpdesk.service.auth import Sessionmaker

logger = logging.getLogger("cedesk.snooze")

_KEPT = ("subject", "priority", "type_id", "team_id", "assignee_id", "tags", "reopen_count")


async def wake_workspace(
    sm: Sessionmaker, workspace_id: UUID, now: datetime | None = None, batch: int = rules.WAKE_BATCH
) -> int:
    """Returns how many tickets woke."""
    now = now or datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        due = await repo.due_snoozes(s, now, batch)
        if not due:
            return 0
        active = await catalog.active_members(
            s, [t["assignee_id"] for t in due if t["assignee_id"] is not None]
        )
        for t in due:
            await _wake(s, workspace_id, t, active)
        return len(due)


async def _wake(s: Any, workspace_id: UUID, t: Any, active: set[UUID]) -> None:
    after = {k: t[k] for k in _KEPT} | {
        "status": Status.open.value,
        "fields": dict(t["fields"]),
        "snoozed_until": None,
    }
    await ticket_repo.update_ticket(s, t["id"], after)  # the database clears the snooze
    await sla.refresh(s, ticket_ids=[t["id"]])
    await ticket_repo.insert_message(
        s,
        workspace_id=workspace_id,
        ticket_id=t["id"],
        kind="system",
        body=rules.WOKE_LINE,
        author_member_id=None,
        author_name=None,
    )
    detail = await ticket_repo.ticket_detail(s, t["id"])
    assert detail is not None
    for event, extra in events.after_change(t, after, None):
        await outbox.add(
            s, workspace_id, event.value, t["id"], events.payload(event, detail, extra)
        )
    note = rules.woke_note(label(int(t["number"])), str(t["subject"]))
    assignee = t["assignee_id"] if t["assignee_id"] in active else None
    if assignee is not None:
        await message_repo.notify(
            s,
            workspace_id=workspace_id,
            member_ids=[assignee],
            ticket_id=t["id"],
            kind=rules.NOTIFICATION,
            body=note,
        )
    await followers.tell(s, workspace_id, t["id"], note, exclude=[assignee])
    # Then the `snooze_ended` rules and workflows (plan 5.1), as on any ticket event.
    await automation.run_and_settle(s, workspace_id, t["id"], RuleEvent.snooze_ended)


async def wake_everywhere(sm: Sessionmaker, now: datetime | None = None) -> int:
    """Every workspace (the worker, once a minute). Returns how many tickets woke."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    woke = 0
    for workspace_id in workspace_ids:
        try:
            woke += await wake_workspace(sm, workspace_id, now)
        except Exception:  # one workspace's problem never stops the others
            logger.exception("waking snoozed tickets failed", extra={"workspace_id": workspace_id})
    return woke
