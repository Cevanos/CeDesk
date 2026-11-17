"""A worker that lost its lease can't write anything about the run (fencing).

Each claim stamps the run with a new `lease_token`. Every write a worker makes about the run
(a page saved with its cursor, a pause, a retry, the end of the import, a sync look) only
applies while the run still carries that worker's token. A worker that stalled past its
lease, and whose run another worker took, gets `LeaseLost` on its next write: its page
transaction rolls back (no record, no cursor move), its copied files are deleted, and it
stops without touching the run. Checked with RLS on, against PostgreSQL:

- a takeover in the middle of a page: the stale worker's page is not saved; the new owner's
  lease, cursor and counts stay as they are; the import ends once, with no duplicates;
- the new owner finishes the whole import while the stale one is stalled: the stale one
  writing the same page afterwards changes nothing (records, counts, files);
- a stale worker's failure, retry or rate-limit pause never fails, delays or counts against
  the run;
- each worker-only write, with a token that is not the run's, raises and changes nothing.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.imports import (
    Attachment,
    LeaseLost,
    RateLimited,
    RetryPolicy,
    SourceError,
)
from cedesk.helpdesk.repo import imports as repo
from tests.db.helpers import Sm
from tests.db.test_import_failures import flaky_desk, im, pass_, run_row, with_files
from tests.db.test_import_sync_stop import Hooked
from tests.db.test_imports import end_runs, sql, start

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared

POLICY = RetryPolicy(attempts=3, base_seconds=30, max_seconds=3600)


async def take_over(sm: Sm, ws: UUID) -> dict[str, Any]:
    """The first worker's lease has run out; a second worker takes the run (committed)."""
    await sql(sm, ws, "UPDATE hd_import_run SET lease_until = now() - interval '1 second'")
    now = datetime.now(UTC)
    async with workspace_session(sm, ws) as s:
        taken = await repo.claim_due(s, now, now + timedelta(seconds=120))
    assert taken is not None and taken["lease_token"] is not None
    return dict(taken)


async def events(sm: Sm, ws: UUID, kind: str) -> int:
    return len(await sql(sm, ws, "SELECT 1 FROM hd_import_event WHERE kind = :k", k=kind))


async def test_a_worker_taken_over_mid_page_saves_nothing(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=12)
    w, conn, importers = await flaky_desk(im, sm, src, POLICY)
    run_id = await start(im, w, conn, kinds=["tickets"])
    seen: dict[str, Any] = {}

    async def stall_then_lose_the_run() -> None:
        seen["owner"] = await take_over(sm, w["ws"])

    src.at, src.hook = 5, stall_then_lose_the_run  # page 0 saved, then the stall on page 5
    await pass_(sm, importers, w["ws"])  # the stale worker goes on, and must stop

    row = await run_row(sm, w["ws"], run_id)
    owner = seen["owner"]
    assert row["state"] == "running" and row["lease_token"] == owner["lease_token"]
    assert row["lease_until"] == owner["lease_until"]  # not released, not extended
    assert row["cursor"]["page"] == {"offset": 5}  # page 5 was not saved by the stale one
    assert row["counts"]["tickets"]["created"] == 5
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 5
    assert await events(sm, w["ws"], "failed") == 0 and row["last_error"] is None

    await sql(sm, w["ws"], "UPDATE hd_import_run SET lease_until = now() - interval '1 second'")
    await pass_(sm, importers, w["ws"])  # the run carries on from page 5
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["counts"]["tickets"]["created"] == 12
    ids = [r["source_id"] for r in await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")]
    assert len(ids) == len(set(ids)) == 12
    assert await events(sm, w["ws"], "finished") == 1
    await end_runs(sm, w["ws"])


class StallInFiles(Hooked):
    """`Hooked`, stalling while it copies one file (inside the page, before it is written)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.stall_on: str | None = None

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        if attachment.url == self.stall_on:
            self.stall_on = None
            await self._maybe()
        return await super().fetch(attachment, limit)


async def test_a_stale_worker_writing_the_same_page_after_the_new_owner_changes_nothing(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = StallInFiles()
    src.tickets = [
        with_files(i, Attachment(f"f{i}.pdf", f"https://files.example/{i}", 3, None))
        for i in range(12)
    ]
    src.files = {f"https://files.example/{i}": b"pdf" for i in range(12)}
    w, conn, importers = await flaky_desk(im, sm, src, POLICY)
    run_id = await start(im, w, conn, kinds=["tickets"])

    async def stall_while_another_worker_does_it_all() -> None:
        await sql(sm, w["ws"], "UPDATE hd_import_run SET lease_until = now() - interval '1 second'")
        await pass_(sm, importers, w["ws"])  # a second worker: pages 5 and 10, then the end
        finished = await run_row(sm, w["ws"], run_id)
        assert finished["state"] == "done"

    # The stale worker has copied page 5's first two files when it stalls; afterwards it
    # copies the other three and tries to write the page.
    src.stall_on, src.hook = "https://files.example/7", stall_while_another_worker_does_it_all
    await pass_(sm, importers, w["ws"])

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["counts"]["tickets"]["created"] == 12
    assert row["counts"]["tickets"]["files"] == 12  # not counted twice
    ids = [r["source_id"] for r in await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")]
    assert len(ids) == len(set(ids)) == 12
    files = {
        r["object_key"] for r in await sql(sm, w["ws"], "SELECT object_key FROM hd_attachment")
    }
    assert len(files) == 12
    store = importers.store
    mine = {k for k in store.put_keys if k.startswith(str(w["ws"]))}
    assert mine - files <= set(store.deleted)  # the stale worker's copies were deleted
    assert len(mine) > 12  # it did copy them (the case being tested)
    assert await events(sm, w["ws"], "finished") == 1
    assert await events(sm, w["ws"], "failed") == 0
    await end_runs(sm, w["ws"])


@pytest.mark.parametrize(
    ("raised", "label"),
    [
        (SourceError("The source refused these credentials.", permanent=True), "fail"),
        (SourceError("The source answered 503; will retry.", permanent=False), "retry"),
        (RateLimited(90), "pause"),
    ],
)
async def test_a_stale_worker_never_fails_delays_or_retries_the_run(
    im: AsyncClient, app_sessionmaker: Sm, raised: Exception, label: str
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=12)
    w, conn, importers = await flaky_desk(im, sm, src, POLICY)
    run_id = await start(im, w, conn, kinds=["tickets"])
    seen: dict[str, Any] = {}

    async def lose_the_run_then_hit_an_error() -> None:
        seen["owner"] = await take_over(sm, w["ws"])
        raise raised

    src.at, src.hook = 5, lose_the_run_then_hit_an_error
    await pass_(sm, importers, w["ws"])

    row = await run_row(sm, w["ws"], run_id)
    owner = seen["owner"]
    assert row["state"] == "running", label
    assert (row["lease_token"], row["lease_until"]) == (owner["lease_token"], owner["lease_until"])
    assert row["next_run_at"] == owner["next_run_at"] and row["retry_attempts"] == 0
    assert row["last_error"] is None and row["finished_at"] is None
    assert await events(sm, w["ws"], "failed") == 0
    await end_runs(sm, w["ws"])


Write = Callable[[Any, UUID, UUID], Awaitable[None]]
NOW = datetime.now(UTC)

WRITES: dict[str, Write] = {
    "save_progress": lambda s, r, t: repo.save_progress(
        s, r, cursor={"done": []}, counts={}, lease_until=NOW, token=t
    ),
    "release": lambda s, r, t: repo.release(s, r, next_run_at=NOW, token=t),
    "retry_later": lambda s, r, t: repo.retry_later(
        s, r, next_run_at=NOW, error="x", attempts=1, token=t
    ),
    "finish": lambda s, r, t: repo.finish(s, r, state="failed", error="x", token=t),
    "finish_to_sync": lambda s, r, t: repo.finish_to_sync(
        s, r, last_sync_at=NOW, next_run_at=NOW, counts={}, token=t
    ),
    "sync_pass_done": lambda s, r, t: repo.sync_pass_done(
        s, r, last_sync_at=NOW, next_run_at=NOW, counts={}, token=t
    ),
    "sync_error": lambda s, r, t: repo.sync_error(s, r, next_run_at=NOW, error="x", token=t),
}


@pytest.mark.parametrize("name", sorted(WRITES))
async def test_every_worker_write_needs_the_runs_current_token(
    im: AsyncClient, app_sessionmaker: Sm, name: str
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=3)
    w, conn, _ = await flaky_desk(im, sm, src, POLICY)
    run_id = UUID(await start(im, w, conn, kinds=["tickets"]))
    owner = await take_over(sm, w["ws"])
    before = await run_row(sm, w["ws"], str(run_id))

    with pytest.raises(LeaseLost):
        async with workspace_session(sm, w["ws"]) as s:
            await WRITES[name](s, run_id, uuid4())  # a token that isn't the run's
    assert await run_row(sm, w["ws"], str(run_id)) == before

    async with workspace_session(sm, w["ws"]) as s:  # the owner's token works
        await WRITES[name](s, run_id, owner["lease_token"])
    await end_runs(sm, w["ws"])


async def test_a_long_page_keeps_its_lease_so_no_other_worker_takes_the_run(
    im: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, the lease was renewed only when a page was saved: a page that took longer
    than 2 minutes let a second worker take the run (fenced, but the work was wasted). Now a
    heartbeat moves the lease forward while the worker is on the run."""
    import asyncio

    from cedesk.helpdesk.service.imports import framework

    monkeypatch.setattr(framework, "HEARTBEAT_SECONDS", 0.05)
    sm = app_sessionmaker
    src = Hooked(tickets=3)
    w, conn, importers = await flaky_desk(im, sm, src, POLICY)
    run_id = await start(im, w, conn, kinds=["tickets"])
    seen: dict[str, Any] = {}

    async def a_slow_page() -> None:
        # The lease is made to look almost over; the heartbeat must move it forward.
        await sql(sm, w["ws"], "UPDATE hd_import_run SET lease_until = now() + interval '1 second'")
        await asyncio.sleep(0.3)
        seen["row"] = await run_row(sm, w["ws"], run_id)

    src.at, src.hook = 0, a_slow_page
    await pass_(sm, importers, w["ws"])
    held = seen["row"]
    assert held["lease_until"] > datetime.now(UTC) + timedelta(seconds=60)
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    await end_runs(sm, w["ws"])


async def test_the_heartbeat_never_extends_a_lease_another_worker_holds(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Hooked(tickets=3)
    w, conn, _ = await flaky_desk(im, sm, src, POLICY)
    run_id = UUID(await start(im, w, conn, kinds=["tickets"]))
    owner = await take_over(sm, w["ws"])
    later = datetime.now(UTC) + timedelta(hours=1)
    async with workspace_session(sm, w["ws"]) as s:
        assert not await repo.renew_lease(s, run_id, token=uuid4(), lease_until=later)
    row = await run_row(sm, w["ws"], str(run_id))
    assert row["lease_until"] == owner["lease_until"]
    async with workspace_session(sm, w["ws"]) as s:
        assert await repo.renew_lease(s, run_id, token=owner["lease_token"], lease_until=later)
    await end_runs(sm, w["ws"])
