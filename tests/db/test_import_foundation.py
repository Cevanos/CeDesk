"""What imports lean on, with RLS on (md 8.2): ticket numbers reserved in a block, and
breach alerts that never fire for a ticket imported from another help desk.

The migration itself (four import tables with forced RLS, `hd_csat.source` widened) is
covered by `test_migrations.py` and `test_tenancy.py`, which find every table and run the
downgrade."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.sla_alerts import alert_workspace
from tests.db.helpers import Sm, ok
from tests.db.test_reports import T, desk, ticket
from tests.db.test_sla_alerts import inbox, made_ago

pytestmark = pytest.mark.db


async def test_a_block_of_numbers_is_reserved_in_one_step(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    first_live = ok(
        await client.get(f"{T}/{await ticket(client, w, 'A', None, None)}", headers=w["h"])
    )
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        start = await ticket_repo.reserve_numbers(s, w["ws"], 5)
    assert start == first_live["number"] + 1  # right after the last one handed out
    # The next live ticket comes after the whole block.
    after = ok(await client.get(f"{T}/{await ticket(client, w, 'B', None, None)}", headers=w["h"]))
    assert after["number"] == start + 5


async def mark_imported(sm: Sm, w: dict[str, Any], ticket_id: str, source: str) -> None:
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(
            text("UPDATE hd_ticket SET source = :s, source_id = :i WHERE id = :id"),
            {"s": source, "i": f"src-{ticket_id}", "id": UUID(ticket_id)},
        )


async def test_an_imported_ticket_is_never_alerted_but_a_live_one_is(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    live = await ticket(client, w, "Live", None, "Neha")
    imported = await ticket(client, w, "Old desk", None, "Neha")
    await mark_imported(app_sessionmaker, w, imported, "zendesk")
    # Both are made 90 minutes ago, so both clocks ran out while "nobody was looking":
    # the case a recent import hits (an old one is marked alerted when it is saved).
    for t in (live, imported):
        await made_ago(app_sessionmaker, w, t, 90)

    assert await alert_workspace(app_sessionmaker, w["ws"]) == 1  # the live one only
    texts = await inbox(client, w, "Neha")
    assert len(texts) == 1 and "Live" in texts[0] and "Old desk" not in texts[0]
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        alerted = await s.scalar(
            text("SELECT sla_alerted_due IS NOT NULL FROM hd_ticket WHERE id = :id"),
            {"id": UUID(imported)},
        )
    assert alerted is False  # left alone, not marked: the pill still reads what it reads
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
