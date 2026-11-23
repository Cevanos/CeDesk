"""SQL for scheduled report emails (feature-gap plan 3.5). Run inside a `workspace_session`."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo.auth import ROLE_COLUMNS, ROLE_JOIN

Row = RowMapping

_COLUMNS = (
    "s.id, s.name, s.owner_id, s.report_id, s.preset, s.range_preset, s.cadence, s.weekday, "
    "s.day_of_month, s.hour, s.enabled, s.next_run_at, s.last_sent_at, s.last_error, "
    "s.created_at, s.updated_at"
)
_SELECT = (
    f"SELECT {_COLUMNS}, m.name AS owner_name, r.name AS report_name "  # noqa: S608
    "FROM hd_report_schedule s JOIN member m ON m.id = s.owner_id "
    "LEFT JOIN hd_saved_report r ON r.id = s.report_id "
)


async def visible(session: AsyncSession, member_id: UUID, *, everything: bool) -> Sequence[Row]:
    """The schedules a member made or receives; with `everything`, all of them."""
    result = await session.execute(
        text(
            _SELECT  # noqa: S608
            + "WHERE (CAST(:everything AS boolean) OR s.owner_id = :me OR EXISTS ("
            "SELECT 1 FROM hd_report_schedule_recipient x WHERE x.schedule_id = s.id "
            "AND x.member_id = :me)) ORDER BY lower(s.name), s.id"
        ),
        {"me": member_id, "everything": everything},
    )
    return result.mappings().all()


async def get(session: AsyncSession, schedule_id: UUID) -> Row | None:
    result = await session.execute(text(_SELECT + "WHERE s.id = :id"), {"id": schedule_id})
    return result.mappings().first()


async def recipients(session: AsyncSession, schedule_ids: Sequence[UUID]) -> Sequence[Row]:
    if not schedule_ids:
        return []
    result = await session.execute(
        text(
            "SELECT x.schedule_id, m.id AS member_id, m.name FROM hd_report_schedule_recipient x "
            "JOIN member m ON m.id = x.member_id WHERE x.schedule_id IN :ids "
            "ORDER BY lower(m.name), m.id"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(schedule_ids)},
    )
    return result.mappings().all()


async def members_with_access(session: AsyncSession, member_ids: Sequence[UUID]) -> Sequence[Row]:
    """Members with what `member_access` needs, to check who may get a report."""
    if not member_ids:
        return []
    result = await session.execute(
        text(
            "SELECT m.id, m.name, m.email, m.is_active, m.role, "  # noqa: S608
            + ROLE_COLUMNS
            + " FROM member m "
            + ROLE_JOIN
            + "WHERE m.id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(member_ids)},
    )
    return result.mappings().all()


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_report_schedule"))
    return int(result.scalar_one())


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    owner_id: UUID,
    name: str,
    report_id: UUID | None,
    preset: str | None,
    range_preset: str,
    cadence: str,
    weekday: int | None,
    day_of_month: int | None,
    hour: int,
    next_run_at: datetime,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_report_schedule (workspace_id, owner_id, name, report_id, preset, "
            "range_preset, cadence, weekday, day_of_month, hour, next_run_at) VALUES (:ws, "
            ":owner, :name, :report, :preset, :range, :cadence, :weekday, :dom, :hour, :next) "
            "RETURNING id"
        ),
        {"ws": workspace_id, "owner": owner_id, "name": name, "report": report_id,
         "preset": preset, "range": range_preset, "cadence": cadence, "weekday": weekday,
         "dom": day_of_month, "hour": hour, "next": next_run_at},
    )  # fmt: skip
    return UUID(str(result.scalar_one()))


async def update(
    session: AsyncSession,
    schedule_id: UUID,
    *,
    name: str,
    report_id: UUID | None,
    preset: str | None,
    range_preset: str,
    cadence: str,
    weekday: int | None,
    day_of_month: int | None,
    hour: int,
    enabled: bool,
    next_run_at: datetime,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_report_schedule SET name = :name, report_id = :report, preset = :preset, "
            "range_preset = :range, cadence = :cadence, weekday = :weekday, "
            "day_of_month = :dom, hour = :hour, enabled = :enabled, next_run_at = :next, "
            "last_error = NULL, updated_at = now() WHERE id = :id"
        ),
        {"id": schedule_id, "name": name, "report": report_id, "preset": preset,
         "range": range_preset, "cadence": cadence, "weekday": weekday, "dom": day_of_month,
         "hour": hour, "enabled": enabled, "next": next_run_at},
    )  # fmt: skip


async def replace_recipients(
    session: AsyncSession, workspace_id: UUID, schedule_id: UUID, member_ids: Sequence[UUID]
) -> None:
    await session.execute(
        text("DELETE FROM hd_report_schedule_recipient WHERE schedule_id = :s"),
        {"s": schedule_id},
    )
    for member_id in member_ids:
        await session.execute(
            text(
                "INSERT INTO hd_report_schedule_recipient (workspace_id, schedule_id, member_id) "
                "VALUES (:ws, :s, :m)"
            ),
            {"ws": workspace_id, "s": schedule_id, "m": member_id},
        )


async def remove_recipient(session: AsyncSession, schedule_id: UUID, member_id: UUID) -> bool:
    result = await session.execute(
        text(
            "DELETE FROM hd_report_schedule_recipient WHERE schedule_id = :s AND member_id = :m "
            "RETURNING member_id"
        ),
        {"s": schedule_id, "m": member_id},
    )
    return result.first() is not None


async def delete(session: AsyncSession, schedule_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_report_schedule WHERE id = :id RETURNING id"), {"id": schedule_id}
    )
    return result.first() is not None


# The worker --------------------------------------------------------------------------------


async def claim_due(session: AsyncSession, now: datetime) -> Row | None:
    """One enabled schedule that is due, locked until the transaction ends (two workers
    never take the same one)."""
    result = await session.execute(
        text(
            _SELECT + "WHERE s.enabled AND s.next_run_at <= :now "
            "ORDER BY s.next_run_at, s.id LIMIT 1 FOR UPDATE OF s SKIP LOCKED"
        ),
        {"now": now},
    )
    return result.mappings().first()


async def finish(
    session: AsyncSession,
    schedule_id: UUID,
    *,
    next_run_at: datetime,
    sent_at: datetime | None,
    error: str | None,
    enabled: bool = True,
) -> None:
    """Moves a run on: when it is next due, when it last went out, and why not if it didn't."""
    await session.execute(
        text(
            "UPDATE hd_report_schedule SET next_run_at = :next, "
            "last_sent_at = coalesce(:sent, last_sent_at), last_error = :error, "
            "enabled = :enabled, updated_at = now() WHERE id = :id"
        ),
        {"id": schedule_id, "next": next_run_at, "sent": sent_at,
         "error": error[:300] if error else None, "enabled": enabled},
    )  # fmt: skip


async def using_report_of_others(session: AsyncSession, report_id: UUID, owner_id: UUID) -> int:
    """How many schedules made by someone other than `owner_id` send this saved report."""
    result = await session.execute(
        text("SELECT count(*) FROM hd_report_schedule WHERE report_id = :r AND owner_id <> :owner"),
        {"r": report_id, "owner": owner_id},
    )
    return int(result.scalar_one())


async def using_report(session: AsyncSession, report_id: UUID) -> Sequence[Row]:
    """The schedules that send this saved report (id and owner), locked: a report being
    deleted takes them with it (the foreign key cascades)."""
    result = await session.execute(
        text(
            "SELECT id, owner_id FROM hd_report_schedule WHERE report_id = :r "
            "ORDER BY id FOR UPDATE"
        ),
        {"r": report_id},
    )
    return result.mappings().all()


async def recipient_ids(session: AsyncSession, schedule_id: UUID) -> list[UUID]:
    result = await session.execute(
        text("SELECT member_id FROM hd_report_schedule_recipient WHERE schedule_id = :s"),
        {"s": schedule_id},
    )
    return [UUID(str(v)) for v in result.scalars().all()]
