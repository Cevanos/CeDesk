"""SQL for the inbound API channel (md C3). RLS applies."""

from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def ticket_by_external_id(
    session: AsyncSession, source: str, external_id: str
) -> RowMapping | None:
    result = await session.execute(
        text("SELECT id, number FROM hd_ticket WHERE source = :source AND source_id = :id"),
        {"source": source, "id": external_id},
    )
    return result.mappings().first()


async def ticket_channel(session: AsyncSession, ticket_id: UUID) -> RowMapping | None:
    """The ticket's channel, and the ticket it was merged into, if any."""
    result = await session.execute(
        text(
            "SELECT t.channel, t.merged_into_id, mt.number AS merged_into_number "
            "FROM hd_ticket t LEFT JOIN hd_ticket mt ON mt.id = t.merged_into_id "
            "WHERE t.id = :id"
        ),
        {"id": ticket_id},
    )
    return result.mappings().first()
