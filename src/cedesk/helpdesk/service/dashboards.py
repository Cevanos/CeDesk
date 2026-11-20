"""Dashboards (feature-gap plan 3.3); rules in domain/dashboards.py.

- Everyone who holds the reports right lists their own personal dashboards and every shared
  one, and makes, changes and deletes their own personal dashboards.
- Shared dashboards are made, changed and deleted by roles with `share_views`. Someone
  else's personal dashboard doesn't exist for you (404).
- A card is a saved report you may see (your own, or a shared one) or a built-in report.
  A shared dashboard may only hold built-in and shared reports; a report on a shared
  dashboard can't be unshared (see saved_reports) until it comes off.
- Each card is run on its own (`run_card`), so one slow report never holds the page, under
  the viewer's scope. The page may send a range and a team that replace every card's own.
- Every create, change and delete is audited (area `dashboards`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import dashboards as rules
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import dashboards as repo
from cedesk.helpdesk.repo import saved_reports as reports_repo
from cedesk.helpdesk.repo.dashboards import Row
from cedesk.helpdesk.service import audit, report_engine, shared_items
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "Dashboard not found."
TAKEN = "You already have a dashboard with that name."
TAKEN_SHARED = "A shared dashboard with that name already exists."
CANT_SHARE = "Your role can't share dashboards. Save it as a personal dashboard."


@dataclass(frozen=True)
class Dashboard:
    row: Row
    cards: list[Row]


def _name(raw: str) -> str:
    try:
        return rules.clean_name(raw)
    except rules.DashboardError as exc:
        raise Invalid(str(exc)) from exc


def _refs(raw: Sequence[Mapping[str, Any]]) -> list[rules.CardRef]:
    try:
        return rules.parse_cards(raw)
    except rules.DashboardError as exc:
        raise Invalid(str(exc)) from exc


async def _check_cards(
    s: AsyncSession, caller: Caller, refs: Sequence[rules.CardRef], *, shared: bool
) -> None:
    """Every saved report on it is one the caller may see; on a shared dashboard, shared."""
    for i, ref in enumerate(refs, 1):
        if ref.report_id is None:
            continue
        row = await reports_repo.get(s, ref.report_id)
        if row is None or (not row["shared"] and row["owner_id"] != caller.member_id):
            raise Invalid(f"Card {i}: no such report.")
        if shared and not row["shared"]:
            raise Invalid(
                f"Card {i}: a shared dashboard can only hold shared reports. "
                f'Share "{row["name"]}" first.'
            )


def _visible(caller: Caller, row: Row | None) -> Row:
    found: Row = shared_items.visible(caller, row, NOT_FOUND)
    return found


def _may_change(caller: Caller, row: Row) -> None:
    shared_items.may_change(caller, row, "dashboards")


can_change = shared_items.can_change  # for the list: whether to show edit and delete


def card_label(card: Row) -> str:
    if card["preset"] is not None:
        found = defs.PRESETS.get(card["preset"])
        return found.label if found else str(card["preset"])
    return str(card["report_name"])


def _taken(shared: bool) -> Conflict:
    return Conflict(TAKEN_SHARED if shared else TAKEN)


def _snapshot(row: Row, cards: Sequence[Row]) -> dict[str, Any]:
    return audit_rules.snapshot(row, ["name", "shared"]) | {"cards": len(cards)}


async def _load(s: AsyncSession, rows: Sequence[Row]) -> list[Dashboard]:
    cards = await repo.cards(s, [r["id"] for r in rows])
    return [Dashboard(r, [c for c in cards if c["dashboard_id"] == r["id"]]) for r in rows]


async def list_dashboards(sm: Sessionmaker, caller: Caller) -> list[Dashboard]:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _load(s, await repo.visible(s, caller.member_id))


async def get_dashboard(sm: Sessionmaker, caller: Caller, dashboard_id: UUID) -> Dashboard:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = _visible(caller, await repo.get(s, dashboard_id))
        return (await _load(s, [row]))[0]


async def create_dashboard(
    sm: Sessionmaker,
    caller: Caller,
    *,
    name: str,
    shared: bool,
    cards: Sequence[Mapping[str, Any]],
) -> Dashboard:
    require(caller, Action.reports)
    if shared and not caller.can(Action.share_views):
        raise Forbidden(CANT_SHARE)
    clean, refs = _name(name), _refs(cards)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            await _check_cards(s, caller, refs, shared=shared)
            # Soft caps: two saves at the same moment can pass them by one each.
            if shared and await repo.count_shared(s) >= rules.MAX_SHARED:
                raise Invalid(f"A workspace can have at most {rules.MAX_SHARED} shared dashboards.")
            if not shared and await repo.count_personal(s, caller.member_id) >= rules.MAX_PERSONAL:
                raise Invalid(f"You can have at most {rules.MAX_PERSONAL} personal dashboards.")
            dashboard_id = await repo.insert(
                s, caller.workspace_id, owner_id=caller.member_id, name=clean, shared=shared
            )
            await repo.replace_cards(s, caller.workspace_id, dashboard_id, refs)
            row = await repo.get(s, dashboard_id)
            assert row is not None
            made = await _load(s, [row])
            await audit.record(
                s, caller, Area.dashboards, "created", f"Dashboard {clean}",
                target_id=dashboard_id, detail=_snapshot(row, made[0].cards),
            )  # fmt: skip
            return made[0]
    except IntegrityError as exc:
        raise _taken(shared) from exc


async def change_dashboard(
    sm: Sessionmaker,
    caller: Caller,
    dashboard_id: UUID,
    *,
    name: str | None,
    shared: bool | None,
    cards: Sequence[Mapping[str, Any]] | None,
) -> Dashboard:
    require(caller, Action.reports)
    clean = None if name is None else _name(name)
    refs = None if cards is None else _refs(cards)
    share = bool(shared)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            before = _visible(caller, await repo.get(s, dashboard_id))
            _may_change(caller, before)
            old_cards = list(await repo.cards(s, [dashboard_id]))
            share = before["shared"] if shared is None else shared
            if share != before["shared"] and not caller.can(Action.share_views):
                raise Forbidden(CANT_SHARE)
            current = (
                refs
                if refs is not None
                else [rules.CardRef(c["report_id"], c["preset"]) for c in old_cards]
            )
            await _check_cards(s, caller, current, shared=share)
            await repo.update(s, dashboard_id, name=clean or before["name"], shared=share)
            if refs is not None:
                await repo.replace_cards(s, caller.workspace_id, dashboard_id, refs)
            row = await repo.get(s, dashboard_id)
            assert row is not None
            made = (await _load(s, [row]))[0]
            changed = audit_rules.changes(
                _snapshot(before, old_cards),
                _snapshot(row, made.cards),
                fields=("name", "shared", "cards"),
            )
            if changed:
                await audit.record(
                    s, caller, Area.dashboards, "changed", f"Dashboard {row['name']}",
                    target_id=dashboard_id, detail={"changes": changed},
                )  # fmt: skip
            return made
    except IntegrityError as exc:
        raise _taken(share) from exc


async def delete_dashboard(sm: Sessionmaker, caller: Caller, dashboard_id: UUID) -> None:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = _visible(caller, await repo.get(s, dashboard_id))
        _may_change(caller, before)
        old_cards = await repo.cards(s, [dashboard_id])
        if not await repo.delete(s, dashboard_id):
            raise NotFound(NOT_FOUND)
        await audit.record(
            s, caller, Area.dashboards, "deleted", f"Dashboard {before['name']}",
            target_id=dashboard_id, detail=_snapshot(before, old_cards),
        )  # fmt: skip


async def run_card(
    sm: Sessionmaker,
    caller: Caller,
    dashboard_id: UUID,
    card_id: UUID,
    *,
    range_: Mapping[str, Any] | None,
    team_id: UUID | None,
    timeout_ms: int,
    slots: report_engine.ReportSlots | None,
) -> report_engine.Result:
    """One card's report, under the caller's scope. `range_` and `team_id` replace the
    card's own (the dashboard page's range and team pickers)."""
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        _visible(caller, await repo.get(s, dashboard_id))
        card = await repo.card(s, dashboard_id, card_id)
        if card is None:
            raise NotFound("Card not found.")
        if card["preset"] is not None:
            try:
                raw: Mapping[str, Any] = defs.preset_definition(card["preset"]).stored()
            except defs.DefinitionError as exc:
                raise NotFound(str(exc)) from exc
        else:
            report = await reports_repo.get(s, card["report_id"])
            if report is None or (not report["shared"] and report["owner_id"] != caller.member_id):
                raise NotFound("Report not found.")
            raw = report["definition"]
    stored = dict(raw)
    if range_ is not None:
        stored["range"] = range_
    if team_id is not None:
        stored["filters"] = {**stored.get("filters", {}), "team": str(team_id)}
    try:
        definition = defs.parse(stored)
    except defs.DefinitionError as exc:
        raise Invalid(str(exc)) from exc
    return await report_engine.run(sm, caller, definition, timeout_ms=timeout_ms, slots=slots)
