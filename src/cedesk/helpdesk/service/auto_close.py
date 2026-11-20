"""Auto-close inactive tickets (feature-gap plan 1.4); rules in domain/auto_close.py.

- The setting: read and changed with the `settings` right; each change is audited (area
  `auto_close`).
- `run_auto_close` (the worker, once a day): in each workspace that turned it on, every
  Pending ticket with no reply from the customer (and no other change) for the set
  number of days is resolved, unless something holds it (snoozed, a handover waiting,
  an approval waiting, an open child, merged, imported). Before closing, the notice (if
  any) is added as a reply from the workspace and goes out as replies do: emailed on
  email and web-form tickets (not on demo ones), and through the outbox to the portal
  and the Cevanos chat. Each close writes a line, the usual events (`ticket.resolved`,
  so the CSAT survey goes out as after any resolve), tells the followers, and adds an
  audit entry (area `tickets`, by the server).
- Batches of 200 tickets, each its own transaction, skipping tickets another worker has
  locked, so several workers share the work.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import auto_close as rules
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.placeholders import render
from cedesk.helpdesk.domain.reports import IMPORT_SOURCES
from cedesk.helpdesk.domain.seeds import DEMO_SOURCE
from cedesk.helpdesk.domain.tickets import Status, label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import inbox as repo
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, children, followers, sla
from cedesk.helpdesk.service import email as email_service
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk.auto_close")
WEB_FORM_SOURCE = "web_form"  # as service/messages.py: web form tickets are emailed
WHO = "Auto-close"


@dataclass(frozen=True)
class Setting:
    enabled: bool
    after_days: int
    notice: str | None
    updated_at: datetime | None
    updated_by_name: str | None


def _setting(row: Any) -> Setting:
    if row is None:
        return Setting(False, rules.DEFAULT_DAYS, None, None, None)
    return Setting(
        enabled=bool(row["enabled"]),
        after_days=int(row["after_days"]),
        notice=row["notice"],
        updated_at=row["updated_at"],
        updated_by_name=row["updated_by_name"],
    )


async def get_setting(sm: Sessionmaker, caller: Caller) -> Setting:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return _setting(await repo.auto_close_setting(s))


async def set_setting(
    sm: Sessionmaker, caller: Caller, *, enabled: bool, after_days: int, notice: str | None
) -> Setting:
    require(caller, Action.settings)
    try:
        after_days, notice = rules.check(after_days, notice)
    except rules.AutoCloseError as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        before = _setting(await repo.auto_close_setting(s))
        await repo.save_auto_close(
            s, caller.workspace_id, enabled=enabled, after_days=after_days, notice=notice,
            by=caller.member_id,
        )  # fmt: skip
        after = _setting(await repo.auto_close_setting(s))
        fields = ("enabled", "after_days", "notice")
        changed = audit_rules.changes(before.__dict__, after.__dict__, fields=fields)
        if changed:
            await audit.record(
                s, caller, Area.auto_close, "changed", "Auto-close inactive tickets",
                detail={"changes": changed},
            )  # fmt: skip
        return after


async def run_auto_close(sm: Sessionmaker, *, now: datetime | None = None) -> int:
    """Every workspace (the worker, once a day). Returns how many tickets were closed."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    total = 0
    for workspace_id in workspace_ids:
        try:
            total += await close_workspace(sm, workspace_id, now)
        except Exception:  # one workspace's problem never stops the others
            logger.exception("auto-close failed", extra={"workspace_id": workspace_id})
    return total


async def close_workspace(sm: Sessionmaker, workspace_id: UUID, now: datetime) -> int:
    async with workspace_session(sm, workspace_id) as s:
        setting = _setting(await repo.auto_close_setting(s))
    if not setting.enabled:
        return 0
    cutoff = now - timedelta(days=setting.after_days)
    closed = 0
    while True:
        async with workspace_session(sm, workspace_id) as s:
            batch = await repo.auto_close_candidates(s, cutoff, IMPORT_SOURCES, rules.BATCH)
            workspace = await auth_repo.workspace_row(s)
            name = str(workspace["name"]) if workspace else "Support"
            for ticket in batch:
                await _close(s, workspace_id, ticket, setting, name)
        closed += len(batch)
        if len(batch) < rules.BATCH:
            break
    if closed:
        logger.info("auto-close", extra={"workspace_id": workspace_id, "event": "auto_close"})
    return closed


async def _close(
    s: AsyncSession, workspace_id: UUID, t: Any, setting: Setting, sender_name: str
) -> None:
    ticket_id = t["id"]
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    assert detail is not None
    if setting.notice:
        await _notice(s, workspace_id, detail, setting.notice, sender_name)
    after = {
        k: t[k]
        for k in (
            "subject",
            "priority",
            "type_id",
            "team_id",
            "assignee_id",
            "tags",
            "reopen_count",
        )
    } | {"status": Status.resolved.value, "fields": dict(t["fields"])}
    await ticket_repo.update_ticket(s, ticket_id, after)
    await sla.refresh(s, ticket_ids=[ticket_id])
    await ticket_repo.insert_message(
        s,
        workspace_id=workspace_id,
        ticket_id=ticket_id,
        kind="system",
        body=rules.closed_line(setting.after_days),
        author_member_id=None,
        author_name=None,
    )
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    assert detail is not None
    for event, extra in events.after_change(t, after, None):
        await outbox.add(
            s, workspace_id, event.value, ticket_id, events.payload(event, detail, extra)
        )
    await children.child_resolved(s, workspace_id, t, WHO, None)
    ticket_label = label(int(t["number"]))
    await followers.tell(
        s, workspace_id, ticket_id, rules.follower_note(ticket_label, str(t["subject"]))
    )
    await audit.record_server(
        s, workspace_id, Area.tickets, "auto_closed", ticket_label,
        detail={"ticket_id": str(ticket_id), "after_days": setting.after_days,
                "notice": setting.notice is not None},
        actor_name=WHO,
    )  # fmt: skip


async def _notice(
    s: AsyncSession, workspace_id: UUID, detail: Any, notice: str, sender_name: str
) -> None:
    """The notice as a reply from the workspace, sent the way a reply is."""
    body = render(
        notice,
        customer_name=str(detail["contact_name"]),
        agent_name=sender_name,
        fields=detail["fields"],
    )
    message_id = await ticket_repo.insert_message(
        s,
        workspace_id=workspace_id,
        ticket_id=detail["id"],
        kind="agent",
        body=body,
        author_member_id=None,
        author_name=sender_name[:120],
    )
    emailed = (detail["channel"] == "email" or detail["source"] == WEB_FORM_SOURCE) and detail[
        "source"
    ] != DEMO_SOURCE
    if emailed:
        await email_service.queue_reply(s, workspace_id, detail, message_id, body, sender_name)
    await outbox.add(
        s,
        workspace_id,
        events.Event.message_created.value,
        detail["id"],
        events.payload(
            events.Event.message_created, detail, events.message(message_id, "agent", None)
        ),
    )
