"""An Admin resumes a failed import from its last saved page (audit: resumable runs).

A fake source (`test_import_failures.Flaky`, pages of 5) fails for good on one page; the run
fails with its cursor before that page. `POST /imports/runs/{id}/resume` queues the same run
again, and the worker carries on from that page: the pages already saved are not read again,
nothing is made twice, the counts, options, team mapping, sync setting and failure notes are
kept. Checked with RLS on, against PostgreSQL:

- who may resume (Admin of the workspace), and which runs (failed only; never queued,
  running, done or syncing; never one whose connection was closed or lost its credentials);
- two resumes at once, and a resume sent twice: one wins, the other is a 409, and a running
  worker's cursor and count of tries are untouched;
- a worker that crashes after the resume: the lease runs out, the next worker carries on from
  the last saved page, no duplicates;
- the same page failing again: bounded retries again, then failed again, cursor in place;
- the history: a `resumed` event (who, where it carries on from) and an audit entry.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain.imports import Attachment, Page, RetryPolicy
from tests.db.helpers import Sm, ok
from tests.db.test_import_failures import (
    Flaky,
    failures,
    flaky_desk,
    im,
    pass_,
    run_row,
    two_messages,
    with_files,
)
from tests.db.test_imports import I, desk, end_runs, sql, start

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared

ONE_TRY = RetryPolicy(attempts=1, base_seconds=30, max_seconds=3600)


class Outage(Flaky):
    """`Flaky` that remembers which ticket pages it was asked for (by offset), and can
    stop the worker dead on one page (a crash: nothing below catches it)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.reads: list[int] = []
        self.crash_at: int | None = None

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        at = int(cursor["offset"]) if cursor else 0
        if kind == "tickets":
            self.reads.append(at)
            if self.crash_at == at:
                self.crash_at = None
                raise asyncio.CancelledError  # the process dies mid-page
        return await super().page(kind, cursor)


async def failed_run(
    im: AsyncClient, sm: Sm, src: Outage, **options: Any
) -> tuple[dict[str, Any], dict[str, Any], Any, str]:
    """A run that saved pages 0, 5 and 10, then failed for good on 15 (a 503 every time)."""
    w, conn, importers = await flaky_desk(im, sm, src, ONE_TRY)
    run_id = await start(im, w, conn, kinds=["tickets"], **options)
    src.list_down = 15
    for _ in range(2):  # one try, then the isolating one
        await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "failed" and row["cursor"]["page"] == {"offset": 15}, row
    return w, conn, importers, run_id


def resume(im: AsyncClient, w: dict[str, Any], run_id: str, who: str = "admin") -> Any:
    return im.post(f"{I}/runs/{run_id}/resume", headers=w[who])


async def ticket_ids(sm: Sm, ws: UUID) -> list[str]:
    rows = await sql(sm, ws, "SELECT source_id FROM hd_ticket ORDER BY source_id")
    return [r["source_id"] for r in rows]


async def test_a_failed_run_resumes_from_its_saved_page_and_finishes_without_duplicates(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The PostgreSQL integration: three pages committed, a failure, a resume, the end."""
    sm = app_sessionmaker
    src = Outage(tickets=22)
    w, _, importers, run_id = await failed_run(im, sm, src)
    assert len(await ticket_ids(sm, w["ws"])) == 15
    before = await run_row(sm, w["ws"], run_id)

    src.list_down = None  # the platform is back
    src.reads.clear()
    r = await resume(im, w, run_id)
    resumed = ok(r, 202)
    assert resumed["id"] == run_id and resumed["state"] == "queued"
    assert resumed["retry_attempts"] == 0 and resumed["last_error"] is None
    row = await run_row(sm, w["ws"], run_id)
    assert row["cursor"] == before["cursor"] and row["counts"] == before["counts"]
    assert row["started_at"] == before["started_at"] and row["finished_at"] is None

    await pass_(sm, importers, w["ws"])
    done = await run_row(sm, w["ws"], run_id)
    assert done["state"] == "done", done
    assert src.reads == [15, 20]  # carried on from the failed page; 0, 5, 10 never again
    ids = await ticket_ids(sm, w["ws"])
    assert len(ids) == len(set(ids)) == 22
    assert done["counts"]["tickets"]["created"] == 22  # the first 15 kept, 7 added
    per_ticket = await sql(
        sm, w["ws"],
        "SELECT ticket_id, count(*) AS n FROM hd_message WHERE kind = 'customer' "
        "GROUP BY ticket_id",
    )  # fmt: skip
    assert len(per_ticket) == 22 and {r["n"] for r in per_ticket} == {1}
    await end_runs(sm, w["ws"])


async def test_options_team_mapping_and_sync_are_kept(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, _, importers, run_id = await failed_run(im, sm, src, team_map={"g1": None})
    await sql(sm, w["ws"], "UPDATE hd_import_run SET sync_on = true")  # sync after the import
    before = await run_row(sm, w["ws"], run_id)

    # A failed import never syncs: nothing asks the source for changes.
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "failed"

    src.list_down = None
    ok(await resume(im, w, run_id), 202)
    await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] is True
    assert row["options"] == before["options"] and row["options"]["team_map"] == {"g1": None}
    assert row["last_sync_at"] == before["started_at"]  # changes since the import began
    await sql(sm, w["ws"], "UPDATE hd_import_run SET sync_on = false")
    await end_runs(sm, w["ws"])


async def test_the_team_mapping_applies_to_the_pages_after_the_resume(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, conn, importers = await flaky_desk(im, sm, src, ONE_TRY)
    run_id = await start(im, w, conn, kinds=["tickets"], team_map={"g1": w["team"]})
    src.list_down = 15
    for _ in range(2):
        await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "failed"
    src.list_down = None
    ok(await resume(im, w, run_id), 202)
    await pass_(sm, importers, w["ws"])
    teams = await sql(sm, w["ws"], "SELECT DISTINCT team_id FROM hd_ticket")
    assert [str(r["team_id"]) for r in teams] == [w["team"]]  # before and after the resume
    await end_runs(sm, w["ws"])


async def test_only_an_admin_of_the_workspace_may_resume(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, _, _, run_id = await failed_run(im, sm, src)
    assert (await resume(im, w, run_id, "asha")).status_code == 403
    other = await desk(im, sm)
    assert (await resume(im, other, run_id)).status_code == 404  # another workspace's run
    assert (await im.post(f"{I}/runs/{run_id}/resume")).status_code == 401
    assert (await run_row(sm, w["ws"], run_id))["state"] == "failed"  # untouched
    await end_runs(sm, w["ws"])


async def test_a_resume_sent_twice_or_at_once_queues_the_run_once(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, _, importers, run_id = await failed_run(im, sm, src)
    src.list_down = None

    together = await asyncio.gather(*(resume(im, w, run_id) for _ in range(4)))
    assert sorted(r.status_code for r in together) == [202, 409, 409, 409]
    events = await sql(sm, w["ws"], "SELECT 1 FROM hd_import_event WHERE kind = 'resumed'")
    assert len(events) == 1

    # Sent again while a worker holds it: refused, its place and tries untouched.
    await sql(sm, w["ws"], "UPDATE hd_import_run SET state = 'running', retry_attempts = 1, "
              "lease_until = now() + interval '2 minutes'")  # fmt: skip
    held = await run_row(sm, w["ws"], run_id)
    again = await resume(im, w, run_id)
    assert again.status_code == 409 and "in progress" in again.json()["error"]["message"]
    after = await run_row(sm, w["ws"], run_id)
    assert (after["cursor"], after["retry_attempts"], after["lease_until"]) == (
        held["cursor"], 1, held["lease_until"],
    )  # fmt: skip
    await sql(sm, w["ws"], "UPDATE hd_import_run SET lease_until = NULL")
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    await end_runs(sm, w["ws"])


async def test_runs_that_are_not_failed_are_never_resumed(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=3)
    w, conn, importers = await flaky_desk(im, sm, src, ONE_TRY)
    run_id = await start(im, w, conn, kinds=["tickets"])
    queued = await resume(im, w, run_id)
    assert queued.status_code == 409 and "in progress" in queued.json()["error"]["message"]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET state = 'running'")
    assert (await resume(im, w, run_id)).status_code == 409
    await sql(sm, w["ws"], "UPDATE hd_import_run SET state = 'queued'")
    await pass_(sm, importers, w["ws"])
    done = await resume(im, w, run_id)
    assert done.status_code == 409 and "finished" in done.json()["error"]["message"]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET sync_on = true")  # syncing
    syncing = await resume(im, w, run_id)
    assert syncing.status_code == 409
    await sql(sm, w["ws"], "UPDATE hd_import_run SET sync_on = false")
    assert (await im.post(f"{I}/runs/{UUID(int=7)}/resume", headers=w["admin"])).status_code == 404
    await end_runs(sm, w["ws"])


async def test_a_run_whose_connection_was_closed_is_not_resumed(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, conn, _, run_id = await failed_run(im, sm, src)
    closed = await im.post(f"{I}/connections/{conn['id']}/close", headers=w["admin"])
    assert closed.status_code == 204
    before = await run_row(sm, w["ws"], run_id)
    r = await resume(im, w, run_id)
    assert r.status_code == 409 and "credentials" in r.json()["error"]["message"]
    assert "tok-secret" not in r.text
    assert await run_row(sm, w["ws"], run_id) == before  # no change at all
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_import_event WHERE kind = 'resumed'") == []
    await end_runs(sm, w["ws"])


async def test_a_run_whose_credentials_cant_be_read_is_not_resumed(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, _, _, run_id = await failed_run(im, sm, src)
    await sql(sm, w["ws"], "UPDATE hd_import_connection SET credentials_sealed = 'not sealed'")
    before = await run_row(sm, w["ws"], run_id)
    r = await resume(im, w, run_id)
    assert r.status_code == 409 and "credentials" in r.json()["error"]["message"]
    assert await run_row(sm, w["ws"], run_id) == before
    await end_runs(sm, w["ws"])


async def test_another_run_on_the_connection_blocks_the_resume(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, conn, _, run_id = await failed_run(im, sm, src)
    newer = await start(im, w, conn, kinds=["tickets"])  # started again instead
    r = await resume(im, w, run_id)
    assert r.status_code == 409 and "another run" in r.json()["error"]["message"].lower()
    assert (await run_row(sm, w["ws"], run_id))["state"] == "failed"
    assert (await run_row(sm, w["ws"], newer))["state"] == "queued"
    await end_runs(sm, w["ws"])


async def test_a_worker_that_crashes_after_the_resume_leaves_no_duplicates(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage()
    src.tickets = [
        with_files(i, Attachment(f"f{i}.pdf", f"https://files.example/{i}", 3, None))
        for i in range(30)
    ]
    src.files = {f"https://files.example/{i}": b"pdf" for i in range(30)}
    w, _, importers, run_id = await failed_run(im, sm, src)
    store = importers.store

    src.list_down = None
    src.crash_at = 20  # saves 15, then dies reading 20
    ok(await resume(im, w, run_id), 202)
    with pytest.raises(asyncio.CancelledError):
        await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "running" and row["cursor"]["page"] == {"offset": 20}
    assert row["lease_until"] is not None  # still leased by the dead worker

    await pass_(sm, importers, w["ws"])  # not yet: the lease holds
    assert (await run_row(sm, w["ws"], run_id))["state"] == "running"
    await sql(sm, w["ws"], "UPDATE hd_import_run SET lease_until = now() - interval '1 second'")
    src.reads.clear()
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    assert src.reads == [20, 25]
    ids = await ticket_ids(sm, w["ws"])
    assert len(ids) == len(set(ids)) == 30
    files = await sql(sm, w["ws"], "SELECT object_key FROM hd_attachment")
    assert len(files) == 30 and len({f["object_key"] for f in files}) == 30
    kept = {f["object_key"] for f in files}
    mine = {k for k in store.put_keys if k.startswith(str(w["ws"]))}
    assert len(mine) == 30 and mine <= kept | set(store.deleted)  # each copy used or deleted
    await end_runs(sm, w["ws"])


async def test_the_same_page_failing_again_fails_the_run_again_in_place(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, _, importers, run_id = await failed_run(im, sm, src)  # still down
    ok(await resume(im, w, run_id), 202)
    await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "running" and row["retry_attempts"] == 1  # counted from 0 again
    await pass_(sm, importers, w["ws"])  # the isolating try
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "failed" and row["cursor"]["page"] == {"offset": 15}
    assert "kept failing after 2 tries" in row["last_error"]
    assert len(await ticket_ids(sm, w["ws"])) == 15
    ok(await resume(im, w, run_id), 202)  # and can be resumed again
    await end_runs(sm, w["ws"])


async def test_failure_notes_keep_their_state_through_a_resume(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage()
    src.tickets = [two_messages(i) for i in range(20)]
    src.gone = {"Z1"}  # not_found, stays open
    src.partial = {"Z2", "Z3", "Z6"}  # incomplete: Z2 stays open, Z3 dismissed, Z6 recovered
    w, _, importers, run_id = await failed_run(im, sm, src)
    notes = {f["external_id"]: f for f in await failures(sm, w["ws"])}
    assert set(notes) == {"Z1", "Z2", "Z3", "Z6"}
    url = f"{I}/runs/{run_id}/failures/{notes['Z3']['id']}/dismiss"
    ok(await im.post(url, headers=w["admin"]))

    src.list_down = None
    ok(await resume(im, w, run_id), 202)
    status = {f["external_id"]: f["status"] for f in await failures(sm, w["ws"])}
    assert status == {"Z1": "open", "Z2": "open", "Z3": "dismissed", "Z6": "open"}  # untouched

    # The resumed run reads Z6 in full on a later page (the source lists it again there):
    # only that one is resolved; one row per record, its first failure time kept.
    src.partial = {"Z2"}
    src.tickets[17] = two_messages(6)
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    after = {f["external_id"]: f for f in await failures(sm, w["ws"])}
    assert {k: f["status"] for k, f in after.items()} == {
        "Z1": "open", "Z2": "open", "Z3": "dismissed", "Z6": "resolved",
    }  # fmt: skip
    assert after["Z6"]["first_failed_at"] == notes["Z6"]["first_failed_at"]
    await end_runs(sm, w["ws"])


async def test_the_resume_is_in_the_history_and_the_audit_log(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, conn, _, run_id = await failed_run(im, sm, src)
    ok(await resume(im, w, run_id), 202)

    [event] = await sql(
        sm, w["ws"],
        "SELECT e.run_id, e.connection_id, e.detail, e.created_at, m.email FROM hd_import_event e "
        "JOIN member m ON m.id = e.member_id WHERE e.kind = 'resumed'",
    )  # fmt: skip
    assert str(event["run_id"]) == run_id and str(event["connection_id"]) == conn["id"]
    assert event["email"] == "admin@example.com"
    assert abs(event["created_at"] - datetime.now(UTC)) < timedelta(minutes=1)
    detail = event["detail"]
    assert detail["kinds_done"] == [] and detail["page_saved"] is True
    assert detail["created"] == {"tickets": 15} and "kept failing" in detail["error"]
    assert "offset" not in str(detail) and "tok-secret" not in str(detail)  # no raw cursor
    [entry] = await sql(
        sm, w["ws"], "SELECT target_id FROM hd_audit WHERE area = 'imports' AND action = 'resumed'"
    )
    assert str(entry["target_id"]) == run_id

    history = ok(await im.get(f"{I}/history", headers=w["admin"]))
    [listed] = history["runs"]
    assert listed["id"] == run_id and listed["result"] == "running"  # same history shape
    await end_runs(sm, w["ws"])


async def test_an_idempotent_repeat_returns_the_run_the_first_resume_queued(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Outage(tickets=20)
    w, _, _, run_id = await failed_run(im, sm, src)
    headers = {**w["admin"], "Idempotency-Key": "resume-once-1"}
    first = await im.post(f"{I}/runs/{run_id}/resume", headers=headers)
    second = await im.post(f"{I}/runs/{run_id}/resume", headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"] == run_id
    assert second.headers.get("Idempotent-Replayed") == "true"
    events = await sql(sm, w["ws"], "SELECT 1 FROM hd_import_event WHERE kind = 'resumed'")
    assert len(events) == 1
    await end_runs(sm, w["ws"])
