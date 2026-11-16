"""Files, keep-syncing-until-go-live and import history (md M7, M9, M10) with RLS on.

A fake source stands in for a help desk (see `test_imports.Fake`): it can hand over files and
say what changed since a time. What is checked:

- a message's files are copied to the object store and shown on the message; a file over
  25 MB, or one the source no longer has, is kept as a link in a system line;
- a run started with `sync` keeps its credentials, asks for changes every 5 minutes, brings
  new tickets and only the new messages of ones already here (no duplicates), `sync-now` makes
  the next look due, `sync-stop` deletes the credentials and ends it;
- the history lists every run with who started it and how it went, and the ticket list can be
  filtered to imported tickets.

The test database is shared: a syncing run is stopped at the end of every test, so no other
test's `run_due` picks it up.
"""

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from cedesk.helpdesk.domain.imports import Attachment, Message, Page, Ticket
from cedesk.main import create_app
from tests.db.helpers import Sm, ok
from tests.db.test_imports import (
    SETTINGS,
    Fake,
    I,
    desk,
    fake_desk,
    importers,
    run_state,
    sql,
    start,
    work,
)

pytestmark = pytest.mark.db


@pytest.fixture
async def im(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(SETTINGS)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.desk_app = app  # type: ignore[attr-defined]
        yield c


MB = 1024 * 1024


class Source(Fake):
    """A `Fake` that also hands over files and says what changed."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.files: dict[str, bytes] = {}
        self.changed: list[Ticket] = []
        self.asked: list[datetime] = []

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        data = self.files.get(attachment.url)
        return data if data is not None and len(data) <= limit else None

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        self.asked.append(since)
        return Page(list(self.changed), None)


def with_files(i: int, *files: Attachment) -> Ticket:
    at = datetime(2025, 2, 1, 8, 0, tzinfo=UTC) + timedelta(hours=i)
    return Fake.ticket(i, messages=[Message("customer", "See files", at, "m", attachments=files)])


async def stop_all(sm: Sm, ws: Any) -> None:
    await sql(sm, ws, "UPDATE hd_import_run SET sync_on = false, state = 'failed'")


# Files


async def test_files_are_copied_and_big_or_missing_ones_become_links(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    sm = app_sessionmaker
    src = Source()
    src.tickets = [
        with_files(
            0,
            Attachment("invoice.pdf", "https://files.example/invoice", 5, "application/pdf"),
            Attachment("movie.mp4", "https://files.example/movie", 30 * MB, "video/mp4"),
            Attachment("gone.png", "https://files.example/gone", None, "image/png"),
        )
    ]
    src.files["https://files.example/invoice"] = b"%PDF-"
    w, conn = await fake_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await work(im, sm)
    done = await run_state(im, w, run_id)
    assert done["state"] == "done"
    assert (
        done["counts"]["tickets"]["files"] == 1 and done["counts"]["tickets"]["files_linked"] == 2
    )

    rows = await sql(sm, w["ws"], "SELECT * FROM hd_attachment")
    assert len(rows) == 1
    kept = rows[0]
    assert kept["name"] == "invoice.pdf" and kept["size"] == 5 and kept["message_id"] is not None
    assert kept["uploaded_by"] is None
    assert await importers(im).store.get(kept["object_key"]) == b"%PDF-"

    lines = [
        r["body"]
        for r in await sql(sm, w["ws"], "SELECT body FROM hd_message WHERE kind = 'system'")
        if "kept as a link" in r["body"]
    ]
    assert len(lines) == 2
    big = next(x for x in lines if "movie.mp4" in x)
    gone = next(x for x in lines if "gone.png" in x)
    assert "over 25 MB" in big and "files.example/movie" in big
    assert "no longer in the source" in gone
    await stop_all(sm, w["ws"])


# Keep syncing until go-live


async def test_a_sync_brings_new_tickets_and_only_new_messages(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    sm = app_sessionmaker
    src = Source(tickets=2)
    w, conn = await fake_desk(im, sm, src)
    started = ok(
        await im.post(
            f"{I}/connections/{conn['id']}/run",
            headers=w["admin"],
            json={"kinds": ["tickets"], "sync": True},
        ),
        202,
    )
    run_id = started["id"]
    assert started["sync_on"] is True
    await work(im, sm)
    first = await run_state(im, w, run_id)
    assert first["state"] == "done" and first["sync_on"] is True and first["last_sync_at"]
    kept = await sql(sm, w["ws"], "SELECT state, credentials_sealed FROM hd_import_connection")
    assert kept[0]["state"] == "connected" and kept[0]["credentials_sealed"] is not None

    # Not due for 5 minutes: nothing is asked.
    await work(im, sm)
    assert src.asked == []

    # In the source: Z0 got a reply and was resolved again; Z9 is new.
    at = datetime(2025, 2, 2, 8, 0, tzinfo=UTC)
    z0 = Fake.ticket(
        0, status="open",
        messages=[
            Message("customer", "Hello 0", datetime(2025, 2, 1, 8, 0, tzinfo=UTC), "m0"),
            Message("agent", "On it", at, "m0b", author_name="Old Agent"),
        ],
    )  # fmt: skip
    src.changed = [z0, Fake.ticket(9)]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    assert len(src.asked) == 1
    look = (await run_state(im, w, run_id))["counts"]["sync"]
    assert look["created"] == 1 and look["updated"] == 1 and look["new_messages"] == 1
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 3
    texts = [
        r["body"]
        for r in await sql(
            sm,
            w["ws"],
            "SELECT m.body FROM hd_message m JOIN hd_ticket t ON t.id = m.ticket_id "
            "WHERE t.source_id = 'Z0' AND m.kind <> 'system'",
        )
    ]
    assert sorted(texts) == ["Hello 0", "On it"]
    status = await sql(sm, w["ws"], "SELECT status FROM hd_ticket WHERE source_id = 'Z0'")
    assert status[0]["status"] == "open"

    # In the source: only Z1's status changed (no new message). It counts as updated.
    src.changed = [Fake.ticket(1, status="open")]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    look = (await run_state(im, w, run_id))["counts"]["sync"]
    assert look["created"] == 1 and look["updated"] == 2 and look["new_messages"] == 1
    z1 = await sql(sm, w["ws"], "SELECT status, resolved_at FROM hd_ticket WHERE source_id = 'Z1'")
    assert z1[0]["status"] == "open" and z1[0]["resolved_at"] is None
    src.changed = [z0, Fake.ticket(9)]

    # The same changes again create nothing.
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    again = (await run_state(im, w, run_id))["counts"]["sync"]
    # Nothing really changed this time, so nothing more is counted.
    assert again["created"] == 1 and again["updated"] == 2 and again["new_messages"] == 1
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 3
    # Each look starts a little before the last one, so a change made meanwhile is not missed.
    assert src.asked[1] < datetime.now(UTC)
    await stop_all(sm, w["ws"])


async def test_a_sync_brings_the_owner_team_and_new_tags(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    """A later assignment, team or tag in the source comes over; an owner the source doesn't
    name (or one not in CeDesk) leaves the one here; tags are only added."""
    sm = app_sessionmaker
    src = Source(tickets=1)
    w, conn = await fake_desk(im, sm, src)
    started = ok(
        await im.post(
            f"{I}/connections/{conn['id']}/run",
            headers=w["admin"],
            json={"kinds": ["tickets"], "sync": True, "team_map": {"g1": None}},
        ),
        202,
    )
    await work(im, sm)
    row = await sql(sm, w["ws"], "SELECT assignee_id, team_id, tags FROM hd_ticket")
    assert row[0]["assignee_id"] is None and row[0]["team_id"] is None
    await sql(sm, w["ws"], "UPDATE hd_ticket SET tags = tags || '{local}'")

    # In the source: assigned to Asha, moved to the group mapped to Billing, tagged.
    await sql(
        sm, w["ws"],
        "UPDATE hd_import_run SET options = jsonb_set(options, '{team_map}', "
        "jsonb_build_object('g2', CAST(:team AS text))), next_run_at = now()",
        team=str(w["team"]),
    )  # fmt: skip
    src.changed = [
        Fake.ticket(0, assignee_email="asha@example.com", group_id="g2", tags=["Refund", "vip"])
    ]
    await work(im, sm)
    look = (await run_state(im, w, started["id"]))["counts"]["sync"]
    assert look["updated"] == 1 and look["new_messages"] == 0
    row = await sql(sm, w["ws"], "SELECT assignee_id, team_id, tags FROM hd_ticket")
    assert row[0]["assignee_id"] == w["asha_id"] and str(row[0]["team_id"]) == str(w["team"])
    assert row[0]["tags"] == ["imported-zendesk", "local", "refund", "vip"]
    line = await sql(
        sm, w["ws"], "SELECT body FROM hd_message WHERE body LIKE 'Updated from%'"
    )  # fmt: skip
    assert [r["body"] for r in line] == [
        "Updated from Zendesk: assigned to Asha; team g2; tags added: refund, vip"
    ]

    # The source no longer names an owner, or names one not in CeDesk: Asha stays. Tags the
    # source dropped stay too. Nothing changed, so nothing is counted.
    src.changed = [Fake.ticket(0, assignee_email="nobody@example.org", tags=[])]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    look = (await run_state(im, w, started["id"]))["counts"]["sync"]
    assert look["updated"] == 1
    row = await sql(sm, w["ws"], "SELECT assignee_id, tags FROM hd_ticket")
    assert row[0]["assignee_id"] == w["asha_id"] and "refund" in row[0]["tags"]
    await stop_all(sm, w["ws"])


async def test_an_import_run_again_updates_tickets_already_here(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    """Changes made in the source between two imports (with no sync on) are not lost: the
    second import brings a ticket's new messages and status; one with nothing new is skipped."""
    sm = app_sessionmaker
    src = Source(tickets=2)
    w, conn = await fake_desk(im, sm, src)
    ok(await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"],
                     json={"kinds": ["tickets"]}), 202)  # fmt: skip
    await work(im, sm)
    at = datetime(2025, 2, 2, 8, 0, tzinfo=UTC)
    src.tickets = [
        Fake.ticket(
            0, status="open", assignee_email="asha@example.com",
            messages=[
                Message("customer", "Hello 0", datetime(2025, 2, 1, 8, 0, tzinfo=UTC), "m0"),
                Message("note", "Checked", at, "m0n", author_name="Old Agent"),
            ],
        ),
        Fake.ticket(1),
    ]  # fmt: skip
    # The first run closed its connection: the Admin connects again (as in the app).
    conn2 = ok(await im.post(f"{I}/connections", headers=w["admin"], json={
        "source": "zendesk", "settings": {"subdomain": "acme"},
        "credentials": {"token": "tok-secret-1234"}}), 201)  # fmt: skip
    again = ok(await im.post(f"{I}/connections/{conn2['id']}/run", headers=w["admin"],
                             json={"kinds": ["tickets"]}), 202)  # fmt: skip
    await work(im, sm)
    counts = (await run_state(im, w, again["id"]))["counts"]["tickets"]
    assert (counts["created"], counts["updated"], counts["skipped"]) == (0, 1, 1)
    assert counts["new_messages"] == 1
    z0 = await sql(sm, w["ws"], "SELECT status, assignee_id FROM hd_ticket WHERE source_id = 'Z0'")
    assert z0[0]["status"] == "open" and z0[0]["assignee_id"] == w["asha_id"]
    notes = await sql(sm, w["ws"], "SELECT body FROM hd_message WHERE kind = 'note'")
    assert [r["body"] for r in notes] == ["Checked"]
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 2
    await stop_all(sm, w["ws"])


async def test_sync_now_makes_the_next_look_due_and_sync_stop_ends_it(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    sm = app_sessionmaker
    src = Source(tickets=1)
    w, conn = await fake_desk(im, sm, src)
    run_id = (
        ok(
            await im.post(
                f"{I}/connections/{conn['id']}/run",
                headers=w["admin"],
                json={"kinds": ["tickets"], "sync": True},
            ),
            202,
        )
    )["id"]
    # While the first import is queued, there is nothing to sync yet.
    assert (await im.post(f"{I}/runs/{run_id}/sync-now", headers=w["admin"])).status_code == 409
    # A connection that is syncing (or importing) takes no second run.
    assert (
        await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json={})
    ).status_code == 409
    await work(im, sm)

    assert (await im.post(f"{I}/runs/{run_id}/sync-now", headers=w["asha"])).status_code == 403
    now = ok(await im.post(f"{I}/runs/{run_id}/sync-now", headers=w["admin"]), 202)
    assert now["sync_on"] is True
    await work(im, sm)
    assert len(src.asked) == 1  # due at once, not in 5 minutes

    # Still syncing: a new run is refused until it is stopped.
    again = await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json={})
    assert again.status_code == 409 and "syncing" in again.text.lower()

    assert (await im.post(f"{I}/runs/{run_id}/sync-stop", headers=w["asha"])).status_code == 403
    stopped = ok(await im.post(f"{I}/runs/{run_id}/sync-stop", headers=w["admin"]))
    assert stopped["sync_on"] is False and stopped["state"] == "done"
    left = await sql(sm, w["ws"], "SELECT state, credentials_sealed FROM hd_import_connection")
    assert left[0]["state"] == "closed" and left[0]["credentials_sealed"] is None
    assert (await im.post(f"{I}/runs/{run_id}/sync-stop", headers=w["admin"])).status_code == 409
    assert (await im.post(f"{I}/runs/{run_id}/sync-now", headers=w["admin"])).status_code == 409
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    assert len(src.asked) == 1  # a stopped sync is never asked again

    kinds = [
        r["kind"]
        for r in await sql(sm, w["ws"], "SELECT kind FROM hd_import_event ORDER BY created_at")
    ]
    assert "sync_stopped" in kinds and "credentials_deleted" in kinds
    audited = await sql(
        sm, w["ws"], "SELECT action FROM hd_audit WHERE area = 'imports' ORDER BY seq"
    )
    assert "sync_stopped" in [a["action"] for a in audited]
    await stop_all(sm, w["ws"])


async def test_a_csv_import_cannot_keep_syncing(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    from tests.db.test_imports import upload

    sm = app_sessionmaker
    w = await desk(im, sm)
    conn = await upload(im, w)
    r = await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json={"sync": True})
    assert r.status_code == 422
    await stop_all(sm, w["ws"])


# History (M10)


async def test_history_lists_every_import_and_the_ticket_list_can_show_them(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    sm = app_sessionmaker
    src = Source(tickets=3)
    w, conn = await fake_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await work(im, sm)

    assert (await im.get(f"{I}/history", headers=w["asha"])).status_code == 403
    history = ok(await im.get(f"{I}/history", headers=w["admin"]))
    assert [r["id"] for r in history["runs"]] == [run_id]
    row = history["runs"][0]
    assert row["source"] == "zendesk" and row["account"] == "acme.zendesk.com"
    assert row["result"] == "finished" and row["sync_on"] is False
    assert row["started_by"] and row["started_by_id"]  # the Admin who started it
    assert row["counts"]["tickets"]["created"] == 3 and row["kinds"] == ["tickets"]

    # A second import that fails says why.
    again = await fake_desk_second_run(im, sm, w, conn)
    failed = ok(await im.get(f"{I}/history", headers=w["admin"]))["runs"]
    assert len(failed) == 2 and failed[0]["id"] == again  # newest first
    assert failed[0]["result"] == "failed" and failed[0]["last_error"]
    page = ok(await im.get(f"{I}/history", params={"limit": 1}, headers=w["admin"]))
    assert len(page["runs"]) == 1 and page["next_cursor"]
    rest = ok(
        await im.get(
            f"{I}/history", params={"limit": 1, "cursor": page["next_cursor"]}, headers=w["admin"]
        )
    )
    assert [r["id"] for r in rest["runs"]] == [run_id] and rest["next_cursor"] is None

    # The ticket list: only imported tickets, any or by source.
    only = ok(await im.get("/api/v1/tickets", params={"imported": "zendesk"}, headers=w["admin"]))
    assert len(only["tickets"]) == 3
    assert (
        len(
            ok(await im.get("/api/v1/tickets", params={"imported": "any"}, headers=w["admin"]))[
                "tickets"
            ]
        )
        == 3
    )
    none = ok(await im.get("/api/v1/tickets", params={"imported": "intercom"}, headers=w["admin"]))
    assert none["tickets"] == []
    bad = await im.get("/api/v1/tickets", params={"imported": "nope"}, headers=w["admin"])
    assert bad.status_code == 422
    assert len(ok(await im.get("/api/v1/tickets", headers=w["admin"]))["tickets"]) == 3
    await stop_all(sm, w["ws"])


async def fake_desk_second_run(
    c: AsyncClient, sm: Sm, w: dict[str, Any], conn: dict[str, Any]
) -> str:
    """The connection was closed when the first run ended; a run on it is refused, so make
    one that fails: a new connection whose source is down for good."""
    from cedesk.helpdesk.domain.imports import SourceError

    class Down(Source):
        async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
            raise SourceError("The key was revoked.", permanent=True)

    app = c.desk_app  # type: ignore[attr-defined]
    down = Down()

    async def build(name: str, settings: Mapping[str, Any], secret: str | None) -> Down:
        return down

    from cedesk.helpdesk.service.imports import framework

    app.state.importers = framework.make_importers(
        SETTINGS.secret_key.get_secret_value(), app.state.store, build=build
    )
    made = ok(
        await c.post(
            f"{I}/connections",
            headers=w["admin"],
            json={
                "source": "zendesk",
                "settings": {"subdomain": "acme"},
                "credentials": {"token": "tok-secret-9999"},
            },
        ),
        201,
    )
    run_id = await start(c, w, made, kinds=["tickets"])
    await work(c, sm)
    return run_id


async def test_a_dry_run_says_when_its_count_is_a_guess(
    im: AsyncClient,
    app_sessionmaker: Sm,
) -> None:
    """Totals, not ids: with more already here than the account has (another account of the
    same help desk, or deleted there), "to create" can't be counted, and the dry run says so
    instead of a bare 0. The run itself still creates the new ones."""
    sm = app_sessionmaker
    src = Source(tickets=3)
    w, conn = await fake_desk(im, sm, src)
    ok(await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"],
                     json={"kinds": ["tickets"]}), 202)  # fmt: skip
    await work(im, sm)

    # Another account: two tickets, neither of them here.
    src.tickets = [Fake.ticket(7), Fake.ticket(8)]
    conn2 = ok(await im.post(f"{I}/connections", headers=w["admin"], json={
        "source": "zendesk", "settings": {"subdomain": "other"},
        "credentials": {"token": "tok-secret-1234"}}), 201)  # fmt: skip
    dry = ok(await im.post(f"{I}/connections/{conn2['id']}/dry-run", headers=w["admin"],
                           json={"kinds": ["tickets"]}))  # fmt: skip
    assert dry["already_imported"]["tickets"] == 3
    guess = [x for x in dry["warnings"] if "to create" in x]
    assert len(guess) == 1 and "more than this account has (2)" in guess[0]

    started = ok(await im.post(f"{I}/connections/{conn2['id']}/run", headers=w["admin"],
                               json={"kinds": ["tickets"]}), 202)  # fmt: skip
    await work(im, sm)
    assert (await run_state(im, w, started["id"]))["counts"]["tickets"]["created"] == 2
    await stop_all(sm, w["ws"])
