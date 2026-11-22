"""Scheduled report emails (feature-gap plan 3.5); rules in domain/report_schedules.py.

People:
- Anyone who holds the reports right makes a schedule (they are its owner) and picks its
  recipients: members who hold the reports right and see at least as many teams' tickets as
  the owner does. Free email addresses are never accepted.
- The owner, or an Admin (`settings`), changes or deletes it. A schedule is seen by its
  owner, its recipients and Admins; any recipient can leave it (`leave`).

The worker (`run_scheduled_reports`, every minute):
- claims one due schedule with `FOR UPDATE SKIP LOCKED` and moves `next_run_at` on in the
  same transaction that queues the emails, so two workers or a restart never send twice, and
  after an outage it sends once, not once per missed period;
- runs the report under the *owner's* current rights, and checks every recipient again:
  one who lost the right, was removed or has a narrower scope than the owner is skipped; an
  owner who lost the reports right (or was deactivated) switches the schedule off;
- sends one plain-text email per recipient from the workspace's default support address, or
  `CEDESK_MAIL_FROM_ADDRESS`; with neither, nothing is sent and the schedule says why.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain import report_schedules as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.csat import deliverable
from cedesk.helpdesk.domain.email import new_message_id
from cedesk.helpdesk.domain.permissions import Access, Action, Scope
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import report_schedules as repo
from cedesk.helpdesk.repo import saved_reports as reports_repo
from cedesk.helpdesk.repo.report_schedules import Row
from cedesk.helpdesk.service import audit, report_engine, sla
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.errors import Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.password_reset import Sender

logger = logging.getLogger("cedesk.report_schedules")

NOT_FOUND = "Schedule not found."
NO_SENDER = "No sender address: set a support email address (or CEDESK_MAIL_FROM_ADDRESS)."
FIELDS = ("name", "report_id", "preset", "range_preset", "cadence", "weekday", "day_of_month",
          "hour", "enabled")  # fmt: skip


@dataclass(frozen=True)
class Schedule:
    row: Row
    timezone: str
    recipients: list[Row]


def _name(raw: str) -> str:
    try:
        return rules.clean_name(raw)
    except rules.ScheduleError as exc:
        raise Invalid(str(exc)) from exc


def _timing(
    cadence: str, weekday: int | None, day_of_month: int | None, hour: int, range_preset: str | None
) -> str:
    try:
        return rules.check_timing(cadence, weekday, day_of_month, hour, range_preset)
    except rules.ScheduleError as exc:
        raise Invalid(str(exc)) from exc


def _next(row: Mapping[Any, Any], after: datetime, timezone: str) -> datetime:
    try:
        return rules.next_run(
            after, row["cadence"], row["weekday"], row["day_of_month"], row["hour"], timezone
        )
    except rules.ScheduleError as exc:
        raise Invalid(str(exc)) from exc


async def _timezone(s: AsyncSession) -> str:
    return (await sla.config(s)).calendar.timezone


async def _target(
    s: AsyncSession, owner_id: UUID, report_id: UUID | None, preset: str | None
) -> None:
    """The schedule sends exactly one report the owner may see: theirs, a shared one, or a
    built-in one."""
    if (report_id is None) == (preset is None):
        raise Invalid("Send a report_id or a preset, not both and not neither.")
    if preset is not None:
        if preset not in defs.PRESETS:
            raise Invalid(f"No built-in report called {preset}.")
        return
    assert report_id is not None
    found = await reports_repo.get(s, report_id)
    if found is None or (not found["shared"] and found["owner_id"] != owner_id):
        raise Invalid("No such report.")


async def _check_recipients(
    s: AsyncSession, owner: Access, member_ids: Sequence[UUID]
) -> list[UUID]:
    """Members who may get this owner's report, or `Invalid` naming the first who may not."""
    ids = list(dict.fromkeys(member_ids))
    if not ids or len(ids) > rules.MAX_RECIPIENTS:
        raise Invalid(f"A schedule goes to 1 to {rules.MAX_RECIPIENTS} members.")
    found = {r["id"]: r for r in await repo.members_with_access(s, ids)}
    for member_id in ids:
        row = found.get(member_id)
        if row is None or not row["is_active"]:
            raise Invalid("A recipient is not an active member of this workspace.")
        if not rules.may_receive(owner, member_access(row)):
            raise Invalid(
                f"{row['name']} can't receive this report: they need the reports right and to "
                "see at least as many teams' tickets as you."
            )
    return ids


def _can_manage(caller: Caller, row: Row) -> bool:
    return row["owner_id"] == caller.member_id or caller.can(Action.settings)


async def _load(s: AsyncSession, rows: Sequence[Row]) -> list[Schedule]:
    timezone = await _timezone(s)
    people = await repo.recipients(s, [r["id"] for r in rows])
    return [Schedule(r, timezone, [p for p in people if p["schedule_id"] == r["id"]]) for r in rows]


def can_change(caller: Caller, schedule: Schedule) -> bool:
    return _can_manage(caller, schedule.row)


def is_recipient(caller: Caller, schedule: Schedule) -> bool:
    return any(p["member_id"] == caller.member_id for p in schedule.recipients)


def _snapshot(row: Mapping[Any, Any], recipients: int) -> dict[str, Any]:
    return audit_rules.snapshot(row, list(FIELDS)) | {"recipients": recipients}


async def _visible(s: AsyncSession, caller: Caller, schedule_id: UUID) -> Schedule:
    row = await repo.get(s, schedule_id)
    if row is None:
        raise NotFound(NOT_FOUND)
    schedule = (await _load(s, [row]))[0]
    if not (_can_manage(caller, row) or is_recipient(caller, schedule)):
        raise NotFound(NOT_FOUND)
    return schedule


async def list_schedules(sm: Sessionmaker, caller: Caller) -> list[Schedule]:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.visible(s, caller.member_id, everything=caller.can(Action.settings))
        return await _load(s, rows)


async def get_schedule(sm: Sessionmaker, caller: Caller, schedule_id: UUID) -> Schedule:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _visible(s, caller, schedule_id)


async def create_schedule(
    sm: Sessionmaker,
    caller: Caller,
    *,
    name: str,
    report_id: UUID | None,
    preset: str | None,
    range_preset: str | None,
    cadence: str,
    weekday: int | None,
    day_of_month: int | None,
    hour: int,
    recipient_ids: Sequence[UUID],
) -> Schedule:
    require(caller, Action.reports)
    clean = _name(name)
    rng = _timing(cadence, weekday, day_of_month, hour, range_preset)
    async with workspace_session(sm, caller.workspace_id) as s:
        await _target(s, caller.member_id, report_id, preset)
        if await repo.count(s) >= rules.MAX_PER_WORKSPACE:
            raise Invalid(f"A workspace can have at most {rules.MAX_PER_WORKSPACE} schedules.")
        ids = await _check_recipients(s, caller.access, recipient_ids)
        timezone = await _timezone(s)
        first = _next(
            {"cadence": cadence, "weekday": weekday, "day_of_month": day_of_month, "hour": hour},
            datetime.now(UTC), timezone,
        )  # fmt: skip
        schedule_id = await repo.insert(
            s, caller.workspace_id, owner_id=caller.member_id, name=clean, report_id=report_id,
            preset=preset, range_preset=rng, cadence=cadence, weekday=weekday,
            day_of_month=day_of_month, hour=hour, next_run_at=first,
        )  # fmt: skip
        await repo.replace_recipients(s, caller.workspace_id, schedule_id, ids)
        made = await _visible(s, caller, schedule_id)
        await audit.record(
            s, caller, Area.report_schedules, "created", f"Schedule {clean}",
            target_id=schedule_id, detail=_snapshot(made.row, len(ids)),
        )  # fmt: skip
        return made


async def change_schedule(
    sm: Sessionmaker,
    caller: Caller,
    schedule_id: UUID,
    *,
    changes: Mapping[str, Any],
    recipient_ids: Sequence[UUID] | None,
) -> Schedule:
    """`changes` holds only what was sent (name, report_id, preset, range, cadence, weekday,
    day_of_month, hour, enabled)."""
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _visible(s, caller, schedule_id)
        if not _can_manage(caller, before.row):
            raise Forbidden("Only its owner or an Admin can change a schedule.")
        row = dict(before.row)
        owner = row["owner_id"]
        if "report_id" in changes or "preset" in changes:
            row["report_id"] = changes.get("report_id")
            row["preset"] = changes.get("preset")
            await _target(s, owner, row["report_id"], row["preset"])
        if "name" in changes:
            row["name"] = _name(changes["name"])
        for key in ("cadence", "weekday", "day_of_month", "hour", "enabled"):
            if key in changes:
                row[key] = changes[key]
        if changes.get("cadence", row["cadence"]) != before.row["cadence"]:
            # a new cadence: the fields that don't fit it go
            row["weekday"] = changes.get("weekday") if row["cadence"] == "weekly" else None
            row["day_of_month"] = (
                changes.get("day_of_month") if row["cadence"] == "monthly" else None
            )
        row["range_preset"] = _timing(
            row["cadence"], row["weekday"], row["day_of_month"], row["hour"],
            changes.get("range") if "range" in changes else row["range_preset"],
        )  # fmt: skip
        timezone = before.timezone
        # Due again from now whenever the timing, or the on/off switch, changed.
        retime = any(
            k in changes for k in ("cadence", "weekday", "day_of_month", "hour", "enabled")
        )
        next_run = _next(row, datetime.now(UTC), timezone) if retime else row["next_run_at"]
        await repo.update(
            s, schedule_id, name=row["name"], report_id=row["report_id"], preset=row["preset"],
            range_preset=row["range_preset"], cadence=row["cadence"], weekday=row["weekday"],
            day_of_month=row["day_of_month"], hour=row["hour"], enabled=row["enabled"],
            next_run_at=next_run,
        )  # fmt: skip
        if recipient_ids is not None:
            owner_row = (await repo.members_with_access(s, [owner]))[0]
            ids = await _check_recipients(s, member_access(owner_row), recipient_ids)
            await repo.replace_recipients(s, caller.workspace_id, schedule_id, ids)
        after = await _visible(s, caller, schedule_id)
        changed = audit_rules.changes(
            _snapshot(before.row, len(before.recipients)),
            _snapshot(after.row, len(after.recipients)),
            fields=(*FIELDS, "recipients"),
        )
        if changed:
            await audit.record(
                s, caller, Area.report_schedules, "changed", f"Schedule {after.row['name']}",
                target_id=schedule_id, detail={"changes": changed},
            )  # fmt: skip
        return after


async def delete_schedule(sm: Sessionmaker, caller: Caller, schedule_id: UUID) -> None:
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _visible(s, caller, schedule_id)
        if not _can_manage(caller, before.row):
            raise Forbidden("Only its owner or an Admin can delete a schedule.")
        await repo.delete(s, schedule_id)
        await audit.record(
            s, caller, Area.report_schedules, "deleted", f"Schedule {before.row['name']}",
            target_id=schedule_id, detail=_snapshot(before.row, len(before.recipients)),
        )  # fmt: skip


async def leave_schedule(sm: Sessionmaker, caller: Caller, schedule_id: UUID) -> None:
    """A recipient stops getting it. Others keep getting it; the owner still owns it."""
    require(caller, Action.reports)
    async with workspace_session(sm, caller.workspace_id) as s:
        found = await _visible(s, caller, schedule_id)
        if not is_recipient(caller, found):
            raise NotFound("You are not a recipient of this schedule.")
        await repo.remove_recipient(s, schedule_id, caller.member_id)
        await audit.record(
            s, caller, Area.report_schedules, "left", f"Schedule {found.row['name']}",
            target_id=schedule_id,
        )  # fmt: skip


# The worker ----------------------------------------------------------------------------------


async def _sender(s: AsyncSession, fallback: Sender | None) -> Sender | None:
    default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
    if default is not None:
        return Sender(str(default["address"]), str(default["name"]))
    return fallback


def _report_name(row: Row, result: report_engine.Result) -> str:
    """What the email calls the report: the saved report's name, or the built-in one's."""
    if row["preset"] is not None:
        preset = defs.PRESETS.get(row["preset"])
        return preset.label if preset else result.label
    return str(row["report_name"] or result.label)


def _period(result: report_engine.Result) -> str:
    if result.start is None or result.end is None:
        return "as of now"
    return f"{result.start:%Y-%m-%d} to {result.end:%Y-%m-%d} ({result.timezone})"


def _lines(result: report_engine.Result) -> tuple[list[tuple[str, str]], int]:
    """The groups to show in the email and how many more there are: the latest days of a
    time series, the biggest groups of anything else."""
    groups = result.groups
    in_time = result.group_by in defs.TIME_GROUPS or result.metric == "backlog"
    shown = groups[-rules.LIST_ROWS :] if in_time else groups[: rules.LIST_ROWS]
    rows = [(g.label, rules.format_value(g.value, result.unit)) for g in shown]
    return rows, max(0, len(groups) - len(shown))


async def _send_one(
    sm: Sessionmaker,
    s: AsyncSession,
    workspace_id: UUID,
    row: Row,
    *,
    now: datetime,
    base_url: str,
    fallback: Sender | None,
    timeout_ms: int,
) -> int:
    """Runs one claimed schedule; returns how many emails it queued. Always moves the
    schedule on (or switches it off), so a failing schedule isn't retried every minute."""
    timezone = await _timezone(s)
    upcoming = _next(row, now, timezone)

    async def skip(why: str, *, enabled: bool = True) -> int:
        await repo.finish(
            s, row["id"], next_run_at=upcoming, sent_at=None, error=why, enabled=enabled
        )
        logger.warning("scheduled report not sent: %s", why, extra={"workspace_id": workspace_id})
        return 0

    owner_rows = await repo.members_with_access(s, [row["owner_id"]])
    owner = owner_rows[0] if owner_rows else None
    if owner is None or not owner["is_active"]:
        return await skip("The owner is no longer an active member.", enabled=False)
    owner_access = member_access(owner)
    if not owner_access.can(Action.reports):
        return await skip("The owner no longer has the reports right.", enabled=False)
    scope_team = owner_access.team_id if owner_access.scope is Scope.team_only else None

    sender = await _sender(s, fallback)
    if sender is None:
        return await skip(NO_SENDER)

    try:
        if row["preset"] is not None:
            stored: dict[str, Any] = dict(defs.preset_definition(row["preset"]).stored())
        else:
            saved = await reports_repo.get(s, row["report_id"])
            if saved is None or (not saved["shared"] and saved["owner_id"] != row["owner_id"]):
                return await skip("The report is no longer available to the owner.")
            stored = dict(saved["definition"])
        stored["range"] = {"preset": row["range_preset"]}
        result = await report_engine.run_definition(
            sm, workspace_id, scope_team, defs.parse(stored), now=now, timeout_ms=timeout_ms
        )
    except (defs.DefinitionError, Invalid, NotFound) as exc:
        return await skip(f"The report could not be run: {exc}")
    except Exception as exc:  # whatever it is, move the schedule on: one bad schedule
        # must never be claimed first every minute and starve the others
        logger.exception("scheduled report failed", extra={"workspace_id": workspace_id})
        return await skip(f"The report failed ({type(exc).__name__}). It will try again next time.")

    ids = await repo.recipient_ids(s, row["id"])
    eligible = []
    for member in await repo.members_with_access(s, ids):
        address = str(member["email"])
        if (
            member["is_active"]
            and rules.may_receive(owner_access, member_access(member))
            and deliverable(address)
            and not await email_repo.is_support_address(s, address)
        ):
            eligible.append(address)
    if not eligible:
        return await skip("No one left who can receive it.")

    workspace = await auth_repo.workspace_row(s)
    workspace_name = str(workspace["name"]) if workspace else ""
    lines, more = _lines(result)
    body = rules.email_body(
        schedule=str(row["name"]), report=_report_name(row, result), period=_period(result),
        total=rules.format_value(result.total, result.unit), rows=lines, more=more,
        notes=result.notes, link=base_url.rstrip("/") + "/reports",
    )  # fmt: skip
    subject = rules.subject(workspace_name, str(row["name"]))
    for address in eligible:
        await email_repo.enqueue(
            s, workspace_id, ticket_id=None, message_id=None,
            from_address=sender.address, from_name=sender.name, to=[address], cc=[],
            subject=subject, body=body, header_message_id=new_message_id(sender.address),
            in_reply_to=None, references=[],
        )  # fmt: skip
    await repo.finish(s, row["id"], next_run_at=upcoming, sent_at=now, error=None)
    return len(eligible)


async def run_scheduled_reports(
    sm: Sessionmaker,
    *,
    base_url: str,
    fallback: Sender | None,
    timeout_ms: int,
    now: datetime | None = None,
) -> int:
    """Every workspace (the worker, every minute). Returns how many emails were queued."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    queued = 0
    for workspace_id in workspace_ids:
        try:
            for _ in range(rules.BATCH):
                async with workspace_session(sm, workspace_id) as s:
                    row = await repo.claim_due(s, now)
                    if row is None:
                        break
                    queued += await _send_one(
                        sm, s, workspace_id, row, now=now, base_url=base_url, fallback=fallback,
                        timeout_ms=timeout_ms,
                    )  # fmt: skip
        except Exception:  # one workspace's problem never stops the others
            logger.exception("scheduled reports failed", extra={"workspace_id": workspace_id})
    return queued
