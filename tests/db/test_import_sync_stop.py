"""Stopping the sync never disturbs a worker that is on the run (`POST .../sync-stop`).

Before: stopping the sync while the first import was still running set `lease_until` to
NULL, so a second worker could take the run while the first was still writing it; and the
decision to delete the credentials used the run as read before the stop, so an import that
finished at that moment kept its credentials for good. Checked with RLS on:

- a stop while a worker holds the run keeps its lease: no other worker can take it, and the
  import ends without syncing and deletes the credentials;
- a stop during a sync look leaves the look's lease alone; no further look is taken;
- a stop racing the import's end: whichever commits first, the run ends without syncing and
  the credentials are deleted;
- stops sent twice, or at once: one wins, the others are a 409; one event each.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.imports import Page, RetryPolicy, Ticket
from cedesk.helpdesk.repo import imports as repo
from tests.db.helpers import Sm, ok
from tests.db.test_import_failures import Flaky, flaky_desk, im, pass_, run_row
from tests.db.test_imports import Fake, I, end_runs, sql

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared

POLICY = RetryPolicy(attempts=1, base_seconds=30, max_seconds=3600)


class Hooked(Flaky):
    """`Flaky` that runs `hook` while the worker is inside the page at `at` (it holds the
    run's lease then), or inside a sync look."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.at: int | None = None
        self.hook: Callable[[], Awaitable[None]] | None = None

    async def _maybe(self) -> None:
        if self.hook is not None:
            hook, self.hook = self.hook, None
            await hook()

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if kind == "tickets" and (int(cursor["offset"]) if cursor else 0) == self.at:
            await self._maybe()
        return await super().page(kind, cursor)

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        await self._maybe()
        return await super().changed_tickets(since, cursor)


async def syncing_import(
    im: AsyncClient, sm: Sm, src: Hooked
) -> tuple[dict[str, Any], dict[str, Any], Any, str]:
    w, conn, importers = await flaky_desk(im, sm, src, POLICY)
    r = await im.post(
        f"{I}/connections/{conn['id']}/run", headers=w["admin"],
        json={"kinds": ["tickets"], "sync": True},
    )  # fmt: skip
    return w, conn, importers, str(ok(r, 202)["id"])


def stop(im: AsyncClient, w: dict[str, Any], run_id: str) -> Any:
    return im.post(f"{I}/runs/{run_id}/sync-stop", headers=w["admin"])


async def claim(sm: Sm, ws: UUID) -> Any:
    """What a second worker would take now (imports first, then a due sync look)."""
    now = datetime.now(UTC)
    async with workspace_session(sm, ws) as s:
        taken = await repo.claim_due(s, now, now + timedelta(seconds=120))
        if taken is None:
            taken = await repo.claim_sync(s, now, now + timedelta(seconds=120))
        await s.rollback()  # only looking
    return taken


async def credentials(sm: Sm, ws: UUID) -> Mapping[str, Any]:
    [row] = await sql(sm, ws, "SELECT state, credentials_sealed FROM hd_import_connection")
    return row


async def events(sm: Sm, ws: UUID, kind: str) -> int:
    return len(await sql(sm, ws, "SELECT 1 FROM hd_import_event WHERE kind = :k", k=kind))


async def test_a_stop_while_a_worker_holds_the_import_keeps_its_lease(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=12)
    w, _, importers, run_id = await syncing_import(im, sm, src)
    seen: dict[str, Any] = {}

    async def stop_mid_import() -> None:
        ok(await stop(im, w, run_id))
        seen["row"] = await run_row(sm, w["ws"], run_id)
        seen["second_worker"] = await claim(sm, w["ws"])
        seen["credentials"] = await credentials(sm, w["ws"])

    src.at, src.hook = 5, stop_mid_import
    await pass_(sm, importers, w["ws"])

    held = seen["row"]
    assert held["state"] == "running" and held["sync_on"] is False
    assert held["lease_until"] is not None and held["lease_until"] > datetime.now(UTC)
    assert seen["second_worker"] is None  # nobody else can take it while the worker is on it
    assert seen["credentials"]["state"] == "connected"  # mid-import: they go when it ends

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] is False and row["lease_until"] is None
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 12  # no page twice
    gone = await credentials(sm, w["ws"])
    assert gone["state"] == "closed" and gone["credentials_sealed"] is None
    assert await events(sm, w["ws"], "credentials_deleted") == 1
    await end_runs(sm, w["ws"])


async def test_a_stop_during_a_sync_look_leaves_the_look_alone(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=3)
    w, _, importers, run_id = await syncing_import(im, sm, src)
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    seen: dict[str, Any] = {}

    async def stop_mid_look() -> None:
        ok(await stop(im, w, run_id))
        seen["row"] = await run_row(sm, w["ws"], run_id)
        seen["second_worker"] = await claim(sm, w["ws"])

    src.changed = [Fake.ticket(7)]
    src.hook = stop_mid_look
    await pass_(sm, importers, w["ws"])

    held = seen["row"]
    assert held["lease_until"] is not None and held["sync_on"] is False
    assert seen["second_worker"] is None
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] is False and row["lease_until"] is None
    assert (await credentials(sm, w["ws"]))["state"] == "closed"  # stopped after the import

    src.changed = [Fake.ticket(8)]
    await pass_(sm, importers, w["ws"])  # no further look
    ids = {r["source_id"] for r in await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")}
    assert "Z8" not in ids
    await end_runs(sm, w["ws"])


async def test_a_stop_racing_the_end_of_the_import_still_deletes_the_credentials(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The worker is ending the import (it holds the run's row, about to make it `done` and
    syncing) when the stop comes: the stop waits, then sees the run is done and deletes the
    credentials. Before, it used the run as read before and kept them for good."""
    sm = app_sessionmaker
    src = Hooked(tickets=3)
    w, _, _, run_id = await syncing_import(im, sm, src)
    await sql(
        sm, w["ws"], "UPDATE hd_import_run SET state = 'running', lease_token = gen_random_uuid()"
    )

    async with workspace_session(sm, w["ws"]) as worker:  # the worker, ending the import
        locked = await repo.run(worker, UUID(run_id), lock=True)
        assert locked is not None and locked["sync_on"] is True
        stopping = asyncio.create_task(stop(im, w, run_id))
        await asyncio.sleep(0.3)
        assert not stopping.done()  # the stop waits for the worker
        now = datetime.now(UTC)
        await repo.finish_to_sync(
            worker, UUID(run_id), last_sync_at=now, next_run_at=now + timedelta(minutes=5),
            counts={}, token=locked["lease_token"],
        )  # fmt: skip
    ok(await stopping)

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] is False
    gone = await credentials(sm, w["ws"])
    assert gone["state"] == "closed" and gone["credentials_sealed"] is None
    assert await events(sm, w["ws"], "credentials_deleted") == 1
    await end_runs(sm, w["ws"])


async def test_the_import_ending_after_a_stop_does_not_sync_and_deletes_the_credentials(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=4)
    w, _, importers, run_id = await syncing_import(im, sm, src)

    async def stop_on_the_last_page() -> None:
        ok(await stop(im, w, run_id))

    src.at, src.hook = 0, stop_on_the_last_page  # one page: the stop, then the end
    await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] is False and row["last_sync_at"] is None
    assert (await credentials(sm, w["ws"]))["state"] == "closed"
    await end_runs(sm, w["ws"])


async def test_stops_sent_twice_or_at_once_stop_it_once(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=3)
    w, _, importers, run_id = await syncing_import(im, sm, src)
    await pass_(sm, importers, w["ws"])

    together = await asyncio.gather(*(stop(im, w, run_id) for _ in range(4)))
    assert sorted(r.status_code for r in together) == [200, 409, 409, 409]
    again = await stop(im, w, run_id)
    assert again.status_code == 409 and "isn't syncing" in again.json()["error"]["message"]
    assert await events(sm, w["ws"], "sync_stopped") == 1
    assert await events(sm, w["ws"], "credentials_deleted") == 1
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] is False
    await end_runs(sm, w["ws"])
