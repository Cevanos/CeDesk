"""One record that can't be read never stops an import, and nothing retries forever (P0-2).

A fake source (see `test_imports.Fake`) reads each ticket's details one at a time, through
`RecordFailures`, like the real importers do. What is checked, with RLS on:

- a ticket deleted after it was listed is noted (`hd_import_failure`, `not_found`) and the
  rest of the page is imported; the run ends `done` and the failure stays visible;
- a temporary error is retried with backoff; the count of tries is kept on the run, so a
  worker restart doesn't reset it; a 429 waits for Retry-After and uses up no try;
- when the tries run out, one more try isolates the record that keeps failing (noted as
  `temporary`) and imports the rest; a page that still can't be read fails the run with a
  clear message and the cursor stays where it was;
- refused credentials on one record stop the run (never noted as a missing record);
- no duplicates after retries, a restart or an import run again; a record that comes back
  later is imported and its failure is marked resolved;
- a page that fails after its files were copied leaves no file behind.
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from cedesk.helpdesk.domain.imports import (
    Attachment,
    Message,
    Page,
    RateLimited,
    RecordFailures,
    RecordGone,
    RetryPolicy,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.service.imports import framework
from cedesk.main import create_app
from tests.db.helpers import Sm, ok
from tests.db.test_imports import SETTINGS, Fake, I, desk, end_runs, run_state, sql, start

pytestmark = pytest.mark.db


@pytest.fixture
async def im(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(SETTINGS)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.desk_app = app  # type: ignore[attr-defined]
        yield c


class Flaky(Fake):
    """A help desk whose ticket details are read one ticket at a time, as the real ones are.

    `gone`: deleted after the list was read (a 404). `broken`: how many more reads of that
    ticket answer 503. `refused`: the key may not read that ticket (401). `list_down`: the
    page at that offset can't be listed at all (a 503 every time)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.failures = RecordFailures()
        self.gone: set[str] = set()
        self.broken: dict[str, int] = {}
        self.refused: set[str] = set()
        self.list_down: int | None = None
        self.files: dict[str, bytes] = {}
        self.file_down: dict[str, int] = {}
        self.file_limited: dict[str, int] = {}  # a 429 for that file, this many times
        self.changed: list[Ticket] = []
        self.partial: set[str] = set()  # only the first message can be read (the rest: 404)

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        start_at = int(cursor["offset"]) if cursor else 0
        if kind == "tickets" and self.list_down == start_at:
            raise SourceError("The source answered 503; will retry.", permanent=False)
        page = await super().page(kind, cursor)
        if kind != "tickets":
            return page
        kept: list[Ticket] = []
        for ticket in page.items:
            read = await self.failures.guard(
                "ticket", ticket.external_id, partial(self.detail, ticket)
            )
            if read is not None:
                kept.append(read)
        return Page(kept, page.cursor)

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        kept: list[Ticket] = []
        for ticket in self.changed:
            read = await self.failures.guard(
                "ticket", ticket.external_id, partial(self.detail, ticket)
            )
            if read is not None:
                kept.append(read)
        return Page(kept, None)

    async def detail(self, ticket: Ticket) -> Ticket:
        key = ticket.external_id
        if key in self.gone:
            raise RecordGone("The source no longer has this record (404).")
        if key in self.refused:
            raise SourceError("The source refused these credentials.", permanent=True)
        if self.broken.get(key, 0) > 0:
            self.broken[key] -= 1
            raise SourceError("The source answered 503; will retry.", permanent=False)
        if key in self.partial:
            self.failures.incomplete("ticket", key, "The source sent no replies (404).")
            return replace(ticket, messages=list(ticket.messages[:1]))
        return ticket

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        if self.file_limited.get(attachment.url, 0) > 0:
            self.file_limited[attachment.url] -= 1
            raise RateLimited(30)
        if self.file_down.get(attachment.url, 0) > 0:
            self.file_down[attachment.url] -= 1
            raise SourceError("The source answered 503 for a file; will retry.", permanent=False)
        return self.files.get(attachment.url)


class Recording:
    """The object store, remembering every key written and deleted (orphan check)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.put_keys: list[str] = []
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.put_keys.append(key)
        await self.inner.put(key, data, content_type=content_type)

    async def get(self, key: str) -> bytes:
        found: bytes = await self.inner.get(key)
        return found

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        await self.inner.delete(key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


POLICY = RetryPolicy(attempts=2, base_seconds=30, max_seconds=3600)


def worker(im: AsyncClient, src: Flaky, policy: RetryPolicy = POLICY) -> framework.Importers:
    """What a worker process builds when it starts (a new one for a "restart")."""
    app = im.desk_app  # type: ignore[attr-defined]

    async def build(name: str, settings: Mapping[str, Any], secret: str | None) -> Flaky:
        src.failures = RecordFailures()  # a connector is built fresh for every slice
        return src

    store = app.state.store
    if not isinstance(store, Recording):
        store = Recording(store)
    app.state.store = store
    found = framework.make_importers(
        SETTINGS.secret_key.get_secret_value(), store, build=build, retry=policy
    )
    app.state.importers = found
    return found


async def flaky_desk(
    im: AsyncClient, sm: Sm, src: Flaky, policy: RetryPolicy = POLICY
) -> tuple[dict[str, Any], dict[str, Any], framework.Importers]:
    importers = worker(im, src, policy)
    w = await desk(im, sm)
    r = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "zendesk", "settings": {"subdomain": "acme"},
              "credentials": {"token": "tok-secret-1234"}},
    )  # fmt: skip
    return w, ok(r, 201), importers


async def pass_(sm: Sm, importers: framework.Importers, ws: UUID) -> None:
    """One worker pass, with the run made due first (no waiting out the backoff)."""
    await sql(sm, ws, "UPDATE hd_import_run SET next_run_at = now() WHERE state <> 'failed'")
    await framework.run_due(sm, importers)


async def run_row(sm: Sm, ws: UUID, run_id: str) -> Mapping[str, Any]:
    return (await sql(sm, ws, "SELECT * FROM hd_import_run WHERE id = :i", i=run_id))[0]


async def failures(sm: Sm, ws: UUID) -> list[Mapping[str, Any]]:
    return await sql(sm, ws, "SELECT * FROM hd_import_failure ORDER BY external_id")


async def test_a_deleted_ticket_is_noted_and_the_rest_are_imported(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=6)
    src.gone = {"Z2"}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])

    run = await run_state(im, w, run_id)
    assert run["state"] == "done", run
    assert run["counts"]["tickets"]["created"] == 5 and run["counts"]["tickets"]["failed"] == 1
    ids = {r["source_id"] for r in await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")}
    assert ids == {"Z0", "Z1", "Z3", "Z4", "Z5"}
    [noted] = await failures(sm, w["ws"])
    assert (noted["kind"], noted["external_id"], noted["category"]) == ("ticket", "Z2", "not_found")
    assert noted["status"] == "open" and noted["attempts"] == 1 and noted["source"] == "zendesk"
    assert "404" in noted["last_error"] and str(noted["run_id"]) == run_id

    # Still visible after the run ended.
    listed = ok(await im.get(f"{I}/runs/{run_id}/failures", headers=w["admin"]))
    assert [(f["external_id"], f["category"], f["status"]) for f in listed["failures"]] == [
        ("Z2", "not_found", "open")
    ]
    assert listed["next_cursor"] is None
    agent = await im.get(f"{I}/runs/{run_id}/failures", headers=w["asha"])
    assert agent.status_code == 403
    await end_runs(sm, w["ws"])


async def test_a_temporary_failure_is_retried_with_backoff_and_then_succeeds(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=3)
    src.broken = {"Z1": 1}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])

    row = await run_row(sm, w["ws"], run_id)
    wait = (row["next_run_at"] - datetime.now(UTC)).total_seconds()
    assert row["state"] == "running" and row["retry_attempts"] == 1
    assert 13 <= wait <= 31  # base 30 s, at least half of it (jitter)
    assert "503" in row["last_error"]
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket") == []  # the page was not written

    await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["retry_attempts"] == 0
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 3
    assert await failures(sm, w["ws"]) == []
    await end_runs(sm, w["ws"])


async def test_when_retries_run_out_the_failing_record_is_isolated(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=4)
    src.broken = {"Z1": 10**6}  # never comes back
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    for expected in (1, 2):
        await pass_(sm, importers, w["ws"])
        row = await run_row(sm, w["ws"], run_id)
        assert row["state"] == "running" and row["retry_attempts"] == expected
    await pass_(sm, importers, w["ws"])  # the isolating try

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["retry_attempts"] == 0
    ids = {r["source_id"] for r in await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")}
    assert ids == {"Z0", "Z2", "Z3"}
    [noted] = await failures(sm, w["ws"])
    assert (noted["external_id"], noted["category"], noted["attempts"]) == ("Z1", "temporary", 3)
    await end_runs(sm, w["ws"])


async def test_a_page_that_keeps_failing_fails_the_run_and_keeps_its_place(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=8)
    src.list_down = 5  # the second page can't be listed
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    for _ in range(3):  # two tries, then the isolating one
        await pass_(sm, importers, w["ws"])

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "failed"
    assert "kept failing after 3 tries" in row["last_error"] and "503" in row["last_error"]
    assert "Start the import again" in row["last_error"]
    assert row["cursor"]["page"] == {"offset": 5}  # never moved past the unread page
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 5
    await end_runs(sm, w["ws"])


async def test_the_count_of_tries_survives_a_worker_restart(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=2)
    src.broken = {"Z0": 10**6}
    policy = RetryPolicy(attempts=3, base_seconds=30, max_seconds=3600)
    w, conn, first = await flaky_desk(im, sm, src, policy)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, first, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["retry_attempts"] == 1

    restarted = worker(im, src, policy)  # a new worker process: nothing kept in memory
    await pass_(sm, restarted, w["ws"])
    await pass_(sm, restarted, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "running" and row["retry_attempts"] == 3  # not reset to 1
    await pass_(sm, restarted, w["ws"])  # the isolating try
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    await end_runs(sm, w["ws"])


async def test_a_429_waits_for_retry_after_and_uses_up_no_try(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=2)
    src.broken = {"Z0": 1}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["retry_attempts"] == 1

    src.fail[("tickets", 0)] = RateLimited(90)
    await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    wait = (row["next_run_at"] - datetime.now(UTC)).total_seconds()
    assert row["retry_attempts"] == 1 and 80 < wait <= 91 and "rate limiting" in row["last_error"]
    await end_runs(sm, w["ws"])


async def test_refused_credentials_on_one_record_stop_the_run(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=3)
    src.refused = {"Z1"}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    run = await run_state(im, w, run_id)
    assert run["state"] == "failed" and "refused these credentials" in run["last_error"]
    assert await failures(sm, w["ws"]) == []  # never mistaken for a missing record
    await end_runs(sm, w["ws"])


async def test_no_duplicates_after_retries_a_restart_and_a_second_import(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=7)
    src.broken = {"Z6": 1}  # the second page fails once
    src.gone = {"Z3"}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["retry_attempts"] == 1
    restarted = worker(im, src)
    await pass_(sm, restarted, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"

    # Z3 is back in the source (it was only unavailable): an import run again brings it,
    # and its failure is marked resolved; nothing else is made twice.
    src.gone = set()
    r = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "zendesk", "settings": {"subdomain": "acme"},
              "credentials": {"token": "tok-secret-5678"}},
    )  # fmt: skip
    again = await start(im, w, ok(r, 201), kinds=["tickets"])
    await pass_(sm, restarted, w["ws"])
    assert (await run_row(sm, w["ws"], again))["state"] == "done"
    tickets = await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")
    assert sorted(r["source_id"] for r in tickets) == [f"Z{i}" for i in range(7)]
    per_ticket = await sql(
        sm, w["ws"],
        "SELECT ticket_id, count(*) AS n FROM hd_message WHERE kind = 'customer' "
        "GROUP BY ticket_id",
    )  # fmt: skip
    assert len(per_ticket) == 7 and {r["n"] for r in per_ticket} == {1}
    [noted] = await failures(sm, w["ws"])
    assert noted["external_id"] == "Z3" and noted["status"] == "resolved"
    assert noted["resolved_at"] is not None
    await end_runs(sm, w["ws"])


def with_files(i: int, *files: Attachment) -> Ticket:
    at = datetime(2025, 2, 1, 8, 0, tzinfo=UTC) + timedelta(hours=i)
    return Fake.ticket(i, messages=[Message("customer", "See files", at, "m", attachments=files)])


async def test_a_failed_page_leaves_no_files_behind_and_never_skips_its_place(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = [
        with_files(
            0,
            Attachment("a.pdf", "https://files.example/a", 3, "application/pdf"),
            Attachment("b.pdf", "https://files.example/b", 3, "application/pdf"),
        )
    ]
    src.files = {"https://files.example/a": b"aaa", "https://files.example/b": b"bbb"}
    # a was copied, then b answered 429: the whole run pauses, and the page is not saved. (A
    # file that merely fails no longer fails the page: see the test below.)
    src.file_limited = {"https://files.example/b": 1}
    w, conn, importers = await flaky_desk(im, sm, src)
    store = importers.store
    assert isinstance(store, Recording)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "running" and row["cursor"] == {}
    assert store.put_keys and set(store.put_keys) <= set(store.deleted)  # nothing orphaned
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_attachment") == []

    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")  # the pause is over
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"
    kept = {r["object_key"] for r in await sql(sm, w["ws"], "SELECT object_key FROM hd_attachment")}
    assert len(kept) == 2
    assert set(store.put_keys) == kept | set(store.deleted)  # every copy is used or deleted
    await end_runs(sm, w["ws"])


@pytest.fixture(autouse=True)
def quick_file_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(framework, "FILE_RETRY_SECONDS", 0.0)


async def test_a_file_whose_host_cant_be_reached_never_stalls_the_import(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Live B-29 (Freshdesk) and B-42 (Intercom): a file host the server couldn't reach made
    the page retry for hours and the import (or the sync) stall. Now the file is tried a few
    times at once, then kept as a link; the ticket is noted with why, and the run ends in
    the same pass, reported as complete with exceptions."""
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = [
        with_files(
            0,
            Attachment("b.pdf", "https://files.example/b", 3, None),
            Attachment("c.pdf", "https://files.example/c", 3, None),
        ),
        Fake.ticket(1),
    ]
    src.files = {"https://files.example/c": b"ccc"}
    src.file_down = {"https://files.example/b": 10**6, "https://files.example/c": 1}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])  # one pass, no wait

    run = await run_state(im, w, run_id)
    assert run["state"] == "done" and run["retry_attempts"] == 0
    assert run["counts"]["tickets"]["created"] == 2
    assert run["counts"]["tickets"]["files"] == 1  # c failed once, then was copied
    assert run["counts"]["tickets"]["files_linked"] == 1
    assert run["outcome"] == "complete_with_exceptions"
    report = run["report"]
    assert report["open_failures"] == 1
    assert [(x["key"], x["count"]) for x in report["losses"]] == [("file_unreachable", 1)]
    [line] = await sql(
        sm, w["ws"], "SELECT body FROM hd_message WHERE kind = 'system' AND body LIKE '%b.pdf%'"
    )
    assert "couldn't be reached" in line["body"]
    failures = ok(await im.get(f"{I}/runs/{run_id}/failures", headers=w["admin"]))["failures"]
    [noted] = failures
    assert (noted["external_id"], noted["category"]) == ("Z0", "incomplete")
    assert "host couldn't be reached" in noted["last_error"]
    await end_runs(sm, w["ws"])


async def test_a_sync_look_that_keeps_failing_is_tried_later_and_never_ends_the_sync(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky(tickets=1)
    policy = RetryPolicy(attempts=1, base_seconds=30, max_seconds=3600)
    w, conn, importers = await flaky_desk(im, sm, src, policy)
    r = await im.post(
        f"{I}/connections/{conn['id']}/run", headers=w["admin"],
        json={"kinds": ["tickets"], "sync": True},
    )  # fmt: skip
    run_id = str(ok(r, 202)["id"])
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["state"] == "done"

    # A look: Z5 was deleted since; Z6 answers 503 every time.
    src.changed = [Fake.ticket(5), Fake.ticket(6)]
    src.gone = {"Z5"}
    src.list_down = None
    src.broken = {"Z6": 10**6}
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], run_id))["retry_attempts"] == 1
    await pass_(sm, importers, w["ws"])  # the isolating try: Z6 is set aside, Z5 noted
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["sync_on"] and row["retry_attempts"] == 0
    noted = {(f["external_id"], f["category"]) for f in await failures(sm, w["ws"])}
    assert noted == {("Z5", "not_found"), ("Z6", "temporary")}

    # Z6 works again: the next look imports it and resolves its failure.
    src.broken = {}
    src.changed = [Fake.ticket(6)]
    await pass_(sm, importers, w["ws"])
    status = {f["external_id"]: f["status"] for f in await failures(sm, w["ws"])}
    assert status == {"Z5": "open", "Z6": "resolved"}
    await sql(sm, w["ws"], "UPDATE hd_import_run SET sync_on = false")
    await end_runs(sm, w["ws"])


def two_messages(i: int) -> Ticket:
    at = datetime(2025, 2, 1, 8, 0, tzinfo=UTC) + timedelta(hours=i)
    return Fake.ticket(
        i,
        messages=[
            Message("customer", f"Hello {i}", at, external_id=f"m{i}"),
            Message("agent", f"Answer {i}", at + timedelta(minutes=5), external_id=f"r{i}"),
        ],
    )


async def messages_of(sm: Sm, ws: UUID, source_id: str) -> list[str]:
    rows = await sql(
        sm, ws,
        "SELECT m.channel_message_id FROM hd_message m JOIN hd_ticket t ON t.id = m.ticket_id "
        "WHERE t.source_id = :s AND m.channel_message_id IS NOT NULL ORDER BY m.created_at",
        s=source_id,
    )  # fmt: skip
    return [r["channel_message_id"] for r in rows]


async def another_connection(im: AsyncClient, w: dict[str, Any], n: int) -> dict[str, Any]:
    r = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "zendesk", "settings": {"subdomain": "acme"},
              "credentials": {"token": f"tok-secret-{n:04d}"}},
    )  # fmt: skip
    return ok(r, 201)


async def test_a_ticket_imported_without_all_its_history_stays_open_until_it_is_recovered(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = [two_messages(0), two_messages(1)]
    src.partial = {"Z1"}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])

    run = await run_state(im, w, run_id)
    assert run["state"] == "done"
    assert run["counts"]["tickets"]["created"] == 2 and run["counts"]["tickets"]["incomplete"] == 1
    assert "failed" not in run["counts"]["tickets"]  # it was imported, only not all of it
    assert await messages_of(sm, w["ws"], "Z1") == ["m1"]  # what could be read is kept
    listed = ok(await im.get(f"{I}/runs/{run_id}/failures", headers=w["admin"]))
    assert [(f["external_id"], f["category"], f["status"]) for f in listed["failures"]] == [
        ("Z1", "incomplete", "open")
    ]

    # Imported again, still without its replies: the ticket is here, but nothing was
    # recovered, so no failure is marked resolved.
    second = await start(im, w, await another_connection(im, w, 2), kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], second))["state"] == "done"
    rows = await failures(sm, w["ws"])
    assert {(str(f["run_id"]), f["status"]) for f in rows} == {(run_id, "open"), (second, "open")}

    # The replies can be read now: the next import adds them and resolves both.
    src.partial = set()
    third = await start(im, w, await another_connection(im, w, 3), kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], third))["state"] == "done"
    assert await messages_of(sm, w["ws"], "Z1") == ["m1", "r1"]
    assert {f["status"] for f in await failures(sm, w["ws"])} == {"resolved"}
    await end_runs(sm, w["ws"])


async def test_an_admin_can_dismiss_a_failure_and_it_stays_dismissed(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = [two_messages(0)]
    src.partial = {"Z0"}
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    [row] = await failures(sm, w["ws"])
    url = f"{I}/runs/{run_id}/failures/{row['id']}/dismiss"

    assert (await im.post(url, headers=w["asha"])).status_code == 403
    dismissed = ok(await im.post(url, headers=w["admin"]))
    assert dismissed["status"] == "dismissed" and dismissed["resolved_at"]
    [admin] = await sql(sm, w["ws"], "SELECT id FROM member WHERE email = 'admin@example.com'")
    assert dismissed["resolved_by_id"] == str(admin["id"])
    assert (await im.post(url, headers=w["admin"])).status_code == 409  # not open any more
    missing = f"{I}/runs/{run_id}/failures/{run_id}/dismiss"
    assert (await im.post(missing, headers=w["admin"])).status_code == 404

    # Recovered later: an Admin's decision is kept as it was.
    src.partial = set()
    again = await start(im, w, await another_connection(im, w, 4), kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    assert (await run_row(sm, w["ws"], again))["state"] == "done"
    [kept] = await failures(sm, w["ws"])
    assert kept["status"] == "dismissed"
    await end_runs(sm, w["ws"])
