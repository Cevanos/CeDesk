"""SQL for dashboards (feature-gap plan 3.3). Run inside a `workspace_session`."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.dashboards import CardRef

Row = RowMapping

_SELECT = (
    "SELECT d.id, d.name, d.shared, d.owner_id, m.name AS owner_name, d.created_at, "
    "d.updated_at FROM hd_dashboard d JOIN member m ON m.id = d.owner_id "
)
_CARDS = (
    "SELECT c.id, c.dashboard_id, c.position, c.report_id, c.preset, r.name AS report_name, "
    "coalesce(r.shared, false) AS report_shared, r.owner_id AS report_owner_id "
    "FROM hd_dashboard_card c LEFT JOIN hd_saved_report r ON r.id = c.report_id "
)


async def visible(session: AsyncSession, member_id: UUID) -> Sequence[Row]:
    """The member's own personal dashboards and every shared one: shared first, by name."""
    result = await session.execute(
        text(
            _SELECT + "WHERE d.shared OR d.owner_id = :me "
            "ORDER BY d.shared DESC, lower(d.name), d.id"
        ),
        {"me": member_id},
    )
    return result.mappings().all()


async def get(session: AsyncSession, dashboard_id: UUID) -> Row | None:
    result = await session.execute(text(_SELECT + "WHERE d.id = :id"), {"id": dashboard_id})
    return result.mappings().first()


async def cards(session: AsyncSession, dashboard_ids: Sequence[UUID]) -> Sequence[Row]:
    """The cards of these dashboards, in order."""
    if not dashboard_ids:
        return []
    result = await session.execute(
        text(
            _CARDS + "WHERE c.dashboard_id IN :ids ORDER BY c.dashboard_id, c.position"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(dashboard_ids)},
    )
    return result.mappings().all()


async def card(session: AsyncSession, dashboard_id: UUID, card_id: UUID) -> Row | None:
    result = await session.execute(
        text(_CARDS + "WHERE c.dashboard_id = :d AND c.id = :c"), {"d": dashboard_id, "c": card_id}
    )
    return result.mappings().first()


async def count_personal(session: AsyncSession, member_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_dashboard WHERE owner_id = :me AND NOT shared"),
        {"me": member_id},
    )
    return int(result.scalar_one())


async def count_shared(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_dashboard WHERE shared"))
    return int(result.scalar_one())


async def insert(
    session: AsyncSession, workspace_id: UUID, *, owner_id: UUID, name: str, shared: bool
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_dashboard (workspace_id, owner_id, name, shared) "
            "VALUES (:ws, :owner, :name, :shared) RETURNING id"
        ),
        {"ws": workspace_id, "owner": owner_id, "name": name, "shared": shared},
    )
    return UUID(str(result.scalar_one()))


async def update(session: AsyncSession, dashboard_id: UUID, *, name: str, shared: bool) -> None:
    await session.execute(
        text("UPDATE hd_dashboard SET name = :name, shared = :shared, updated_at = now() "
             "WHERE id = :id"),
        {"id": dashboard_id, "name": name, "shared": shared},
    )  # fmt: skip


async def replace_cards(
    session: AsyncSession, workspace_id: UUID, dashboard_id: UUID, refs: Sequence[CardRef]
) -> None:
    """The dashboard's cards become exactly `refs`, in that order."""
    await session.execute(
        text("DELETE FROM hd_dashboard_card WHERE dashboard_id = :d"), {"d": dashboard_id}
    )
    for position, ref in enumerate(refs):
        await session.execute(
            text(
                "INSERT INTO hd_dashboard_card (workspace_id, dashboard_id, position, report_id, "
                "preset) VALUES (:ws, :d, :p, :r, :preset)"
            ),
            {"ws": workspace_id, "d": dashboard_id, "p": position, "r": ref.report_id,
             "preset": ref.preset},
        )  # fmt: skip


async def delete(session: AsyncSession, dashboard_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_dashboard WHERE id = :id RETURNING id"), {"id": dashboard_id}
    )
    return result.first() is not None


async def shared_using(session: AsyncSession, report_id: UUID) -> int:
    """How many shared dashboards have a card for this saved report."""
    result = await session.execute(
        text(
            "SELECT count(DISTINCT d.id) FROM hd_dashboard_card c "
            "JOIN hd_dashboard d ON d.id = c.dashboard_id "
            "WHERE c.report_id = :r AND d.shared"
        ),
        {"r": report_id},
    )
    return int(result.scalar_one())
