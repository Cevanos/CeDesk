"""Saved reports (feature-gap plan 3.2); rules in domain/saved_reports.py.

- Everyone who holds the reports right lists their own personal reports and every shared
  one, and makes, changes and deletes their own personal reports.
- Shared reports are made, changed and deleted by roles with `share_views` (Admins and
  Supervisors; a custom role with the settings right). Someone else's personal report is
  never shown or changed: 404, as if it didn't exist.
- The definition is checked with the report engine's own rules (`report_defs.parse`) and its
  range must be a preset, so the report stays current. A team in it must be one of this
  workspace's. It is checked again at every run.
- Running one is `report_engine.run`: it is the *viewer's* scope that applies (a role limited
  to one team sees that team's numbers, whoever made the report).
- Every create, change and delete is audited (area `reports`).
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain import saved_reports as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import dashboards as dashboards_repo
from cedesk.helpdesk.repo import report_schedules as schedules_repo
from cedesk.helpdesk.repo import reports as reports_repo
from cedesk.helpdesk.repo import saved_reports as repo
from cedesk.helpdesk.repo.saved_reports import Row
from cedesk.helpdesk.service import audit, report_engine, shared_items
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "Report not found."
TAKEN = "You already have a report with that name."
TAKEN_SHARED = "A shared report with that name already exists."
CANT_SHARE = "Your role can't share reports. Save it as a personal report."
FIELDS = ("name", "definition", "shared")


def _name(raw: str) -> str:
    try:
        return rules.clean_name(raw)
    except rules.ReportError as exc:
        raise Invalid(str(exc)) from exc


async def _definition(s: AsyncSession, raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        checked = defs.parse(raw)
    except defs.DefinitionError as exc:
        raise Invalid(str(exc)) from exc
    if checked.range.preset is None:
        raise Invalid(
            "A saved report uses a preset range (" + ", ".join(defs.PRESET_RANGES) + "), "
            "so it stays current. Run a fixed range without saving it."
        )
    team = checked.filters.get("team")
    if team is not None and not await reports_repo.team_exists(s, UUID(team)):
        raise Invalid("That team does not exist.")
    return checked.stored()


def _visible(caller: Caller, row: Row | None) -> Row:
    found: Row = shared_items.visible(caller, row, NOT_FOUND)
    return found


def _may_change(caller: Caller, row: Row) -> None:
    shared_items.may_change(caller, row, "reports")


can_change = shared_items.can_change  # for the list: whether to show edit and delete


async def list_reports(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.visible(s, caller.member_id)


async def get_report(sm: Sessionmaker, caller: Caller, report_id: UUID) -> Row:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        return _visible(caller, await repo.get(s, report_id))


def _taken(shared: bool) -> Conflict:
    return Conflict(TAKEN_SHARED if shared else TAKEN)


async def create_report(
    sm: Sessionmaker, caller: Caller, *, name: str, definition: Mapping[str, Any], shared: bool
) -> Row:
    require(caller, Action.reports)
    if shared and not caller.can(Action.share_views):
        raise Forbidden(CANT_SHARE)
    clean = _name(name)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            stored = await _definition(s, definition)
            # Soft caps: two saves at the same moment can pass them by one each.
            if shared and await repo.count_shared(s) >= rules.MAX_SHARED:
                raise Invalid(f"A workspace can have at most {rules.MAX_SHARED} shared reports.")
            if not shared and await repo.count_personal(s, caller.member_id) >= rules.MAX_PERSONAL:
                raise Invalid(f"You can have at most {rules.MAX_PERSONAL} personal reports.")
            report_id = await repo.insert(
                s, caller.workspace_id, owner_id=caller.member_id, name=clean,
                definition=stored, shared=shared,
            )  # fmt: skip
            row = await repo.get(s, report_id)
            assert row is not None
            await audit.record(
                s, caller, Area.reports, "created", f"Report {clean}", target_id=report_id,
                detail=audit_rules.snapshot(row, list(FIELDS)),
            )  # fmt: skip
            return row
    except IntegrityError as exc:
        raise _taken(shared) from exc


async def change_report(
    sm: Sessionmaker,
    caller: Caller,
    report_id: UUID,
    *,
    name: str | None,
    definition: Mapping[str, Any] | None,
    shared: bool | None,
) -> Row:
    require(caller, Action.reports)
    clean = None if name is None else _name(name)
    share = bool(shared)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            before = _visible(caller, await repo.get(s, report_id))
            _may_change(caller, before)
            share = before["shared"] if shared is None else shared
            if share != before["shared"] and not caller.can(Action.share_views):
                raise Forbidden(CANT_SHARE)
            if before["shared"] and not share and await dashboards_repo.shared_using(s, report_id):
                raise Conflict(
                    "This report is on a shared dashboard. Take it off there before unsharing it."
                )
            if (
                before["shared"]
                and not share
                and await schedules_repo.using_report_of_others(s, report_id, before["owner_id"])
            ):
                raise Conflict(
                    "Another member's scheduled report sends this one. Ask them to change the "
                    "schedule before you unshare it."
                )
            stored = (
                await _definition(s, definition) if definition is not None else before["definition"]
            )
            await repo.update(
                s, report_id, name=clean or before["name"], definition=stored, shared=share
            )
            row = await repo.get(s, report_id)
            assert row is not None
            changed = audit_rules.changes(before, row, fields=FIELDS)
            if changed:
                await audit.record(
                    s, caller, Area.reports, "changed", f"Report {row['name']}",
                    target_id=report_id, detail={"changes": changed},
                )  # fmt: skip
            return row
    except IntegrityError as exc:
        raise _taken(share) from exc


async def delete_report(sm: Sessionmaker, caller: Caller, report_id: UUID) -> None:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = _visible(caller, await repo.get(s, report_id))
        _may_change(caller, before)
        if not await repo.delete(s, report_id):
            raise NotFound(NOT_FOUND)
        await audit.record(
            s, caller, Area.reports, "deleted", f"Report {before['name']}", target_id=report_id,
            detail=audit_rules.snapshot(before, list(FIELDS)),
        )  # fmt: skip


async def run_report(
    sm: Sessionmaker,
    caller: Caller,
    report_id: UUID,
    *,
    timeout_ms: int,
    slots: report_engine.ReportSlots | None,
) -> report_engine.Result:
    """Runs a saved report for the caller, under the caller's own scope."""
    row = await get_report(sm, caller, report_id)
    try:
        definition = defs.parse(row["definition"])
    except defs.DefinitionError as exc:  # saved under older rules: say so, don't guess
        raise Invalid(f"This report's definition is no longer valid: {exc}") from exc
    return await report_engine.run(sm, caller, definition, timeout_ms=timeout_ms, slots=slots)
