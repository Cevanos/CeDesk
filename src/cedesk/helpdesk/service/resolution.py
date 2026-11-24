"""Resolution receipts (differentiator plan #5): the workspace setting, the receipt saved
when a ticket is resolved, reading and changing it, and the Verify button (a read of
the Shopify order or Stripe payment; nothing is ever written to them).

Audit entries hold field names and the verified kind, never the receipt's text: the
privacy erase blanks that text, and the audit log is never changed."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import resolution as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import resolution as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, reports
from cedesk.helpdesk.service import scope as scope_rules
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_RECEIPT = "This ticket has no resolution receipt."
NOT_FOUND = "Ticket not found."


@dataclass(frozen=True)
class SettingView:
    mode: str
    required_teams: list[UUID]
    updated_at: datetime | None
    updated_by_name: str | None


def _view(row: RowMapping | None) -> SettingView:
    if row is None:
        return SettingView(rules.Mode.off.value, [], None, None)
    teams = [UUID(str(t)) for t in row["required_teams"] or []]
    return SettingView(str(row["mode"]), teams, row["updated_at"], row["updated_by_name"])


async def load_setting(s: AsyncSession) -> rules.Setting:
    v = _view(await repo.setting(s))
    return rules.Setting(rules.Mode(v.mode), tuple(v.required_teams))


async def get_setting(sm: Sessionmaker, caller: Caller) -> SettingView:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return _view(await repo.setting(s))


async def set_setting(
    sm: Sessionmaker, caller: Caller, mode: str, teams: Sequence[UUID]
) -> SettingView:
    require(caller, Action.settings)
    try:
        checked = rules.setting(mode, teams)
    except rules.ResolutionError as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        if await repo.unknown_teams(s, checked.required_teams):
            raise Invalid("One of those teams doesn't exist.")
        before = _view(await repo.setting(s))
        await repo.save_setting(
            s, caller.workspace_id, checked.mode.value, checked.required_teams, caller.member_id
        )
        after = _view(await repo.setting(s))
        fields = ("mode", "required_teams")
        old = {"mode": before.mode, "required_teams": [str(t) for t in before.required_teams]}
        new = {"mode": after.mode, "required_teams": [str(t) for t in after.required_teams]}
        changed = audit_rules.changes(old, new, fields=fields)
        if changed:
            await audit.record(
                s, caller, Area.resolutions, "setting_changed", "Resolution receipts",
                detail={"changes": changed},
            )  # fmt: skip
        return after


async def check_on_resolve(
    s: AsyncSession, team_id: UUID | None, body: Mapping[str, Any] | None
) -> rules.Receipt | None:
    """Before a person resolves a ticket: the receipt sent, checked, or a refusal when the
    setting needs one (or is off and one was sent)."""
    found = await load_setting(s)
    if body is None:
        if found.needed(team_id):
            raise Invalid(rules.REQUIRED)
        return None
    if not found.asked():
        raise Invalid(rules.TURNED_OFF)
    try:
        return rules.receipt(body)
    except (rules.ResolutionError, ValueError) as exc:
        raise Invalid(str(exc)) from exc


async def save_on_resolve(
    s: AsyncSession, caller: Caller, ticket: Mapping[Any, Any], receipt: rules.Receipt
) -> RowMapping:
    """Writes the receipt in the resolve's transaction. One from an earlier resolve is
    replaced; the audit log keeps that it was (not its text)."""
    now = datetime.now(UTC)
    ticket_id = UUID(str(ticket["id"]))
    old = await repo.get(s, ticket_id, current=False)
    notified = receipt.customer_notified
    if notified is None:
        notified = rules.notified(await repo.last_public_reply(s, ticket_id), now)
    verified = receipt.verified.value
    await repo.save(
        s, caller.workspace_id, ticket_id,
        {"request": receipt.request, "action_taken": receipt.action_taken,
         "result": receipt.result, "verified": verified,
         "verified_at": now if receipt.verified is rules.Verified.agent else None,
         "customer_notified": notified, "pending_item": receipt.pending_item,
         "resolved_by": caller.member_id},
    )  # fmt: skip
    if old is not None:
        await audit.record(
            s, caller, Area.resolutions, "replaced", f"Ticket #{ticket['number']}",
            target_id=ticket_id,
            detail={"old": {"verified": old["verified"],
                            "pending": old["pending_item"] is not None,
                            "created_at": old["created_at"].isoformat()}},
        )  # fmt: skip
    saved = await repo.get(s, ticket_id)
    assert saved is not None
    return saved


async def _ticket(s: AsyncSession, caller: Caller, ticket_id: UUID, *, edit: bool) -> RowMapping:
    await scope_rules.check(s, caller, ticket_id, edit=edit)
    ticket = await ticket_repo.ticket_detail(s, ticket_id)
    if ticket is None:
        raise NotFound(NOT_FOUND)
    return ticket


async def get(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> RowMapping:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        await _ticket(s, caller, ticket_id, edit=False)
        found = await repo.get(s, ticket_id)
    if found is None:
        raise NotFound(NO_RECEIPT)
    return found


async def editable_ticket(
    s: AsyncSession, caller: Caller, ticket_id: UUID, now: datetime
) -> RowMapping:
    ticket = await _ticket(s, caller, ticket_id, edit=True)
    if ticket["status"] != "resolved":
        raise Conflict(rules.NOT_RESOLVED)
    if await repo.get(s, ticket_id) is None:
        raise NotFound(NO_RECEIPT)
    if not rules.editable(ticket["resolved_at"], ticket["status"], now):
        raise Conflict(rules.TOO_LATE)
    return ticket


async def edit(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, body: Mapping[str, Any]
) -> RowMapping:
    """Changes the keys sent, while the ticket stays resolved, for 7 days."""
    require(caller, Action.reply)
    try:
        values = rules.changes(body)
    except (rules.ResolutionError, ValueError) as exc:
        raise Invalid(str(exc)) from exc
    now = datetime.now(UTC)
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await editable_ticket(s, caller, ticket_id, now)
        if "verified" in values:
            values["verified_at"] = now if values["verified"] == rules.Verified.agent else None
        await repo.update(s, ticket_id, values)
        if values:
            await audit.record(
                s, caller, Area.resolutions, "changed", f"Ticket #{ticket['number']}",
                target_id=ticket_id, detail={"fields": sorted(values)},
            )  # fmt: skip
        found = await repo.get(s, ticket_id)
    assert found is not None
    return found


# The Resolutions report (plan #5): resolved vs. with a receipt vs. verified.

COUNTS = ("resolved", "with_receipt", "verified", "verified_agent", "verified_shopify",
          "verified_stripe", "pending", "notified")  # fmt: skip


@dataclass(frozen=True)
class ReportRow:
    id: UUID | None
    name: str
    counts: dict[str, int]


@dataclass(frozen=True)
class Report:
    start: datetime
    end: datetime
    team_id: UUID | None
    total: dict[str, int]
    teams: list[ReportRow]
    agents: list[ReportRow]


async def report(
    sm: Sessionmaker,
    caller: Caller,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    team_id: UUID | None = None,
) -> Report:
    """Tickets resolved in [start, end), still resolved: how many have a receipt, how
    many were verified (and how), have a pending item, or told the customer."""
    require(caller, Action.reports)
    team_id = reports.team_filter(caller, team_id)
    start, end = reports.check_period(start, end, datetime.now(UTC))
    async with workspace_session(sm, caller.workspace_id) as s:
        if team_id is not None and await repo.unknown_teams(s, [team_id]):
            raise NotFound("Team not found.")
        rows = await repo.report(s, start, end, team_id)
        team_ids = [r["team_id"] for r in rows if not r["g_team"] and r["team_id"]]
        agent_ids = [r["assignee_id"] for r in rows if not r["g_agent"] and r["assignee_id"]]
        team_names = await repo.names(s, "team", team_ids)
        agent_names = await repo.names(s, "member", agent_ids)
    total = dict.fromkeys(COUNTS, 0)
    teams: list[ReportRow] = []
    agents: list[ReportRow] = []
    for r in rows:
        counts = {k: int(r[k]) for k in COUNTS}
        if r["g_team"] and r["g_agent"]:
            total = counts
        elif not r["g_team"]:
            tid = r["team_id"]
            teams.append(ReportRow(tid, team_names.get(tid, "No team") if tid else "No team",
                                   counts))  # fmt: skip
        else:
            aid = r["assignee_id"]
            agents.append(ReportRow(aid, agent_names.get(aid, "Unassigned") if aid
                                    else "Unassigned", counts))  # fmt: skip
    teams.sort(key=lambda x: x.name.lower())
    agents.sort(key=lambda x: x.name.lower())
    return Report(start, end, team_id, total, teams, agents)
