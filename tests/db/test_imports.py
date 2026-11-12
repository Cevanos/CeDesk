"""Imports from other help desks (md M1-M8) with RLS on.

A CSV file goes through the real API and the worker's `run_due`; a fake source stands in for
a help desk's API (the importers' `build` is replaced). What is checked: only Admins import,
a dry run saves nothing, an imported ticket keeps its dates, status, tags, CSAT and history,
agents are matched by email, card numbers are removed, running again creates no duplicates,
a crash or a 429 resumes where it stopped, imports stay out of live work (no outbox events,
no notifications, no SLA alerts, ticket numbers not held), and credentials are deleted when
the run ends.

The test database is shared: runs are looked up by id, and every test ends its runs.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Article,
    Category,
    Contact,
    Counts,
    Csat,
    Group,
    Message,
    Page,
    RateLimited,
    Reply,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.repo import sla as sla_repo
from cedesk.helpdesk.service.imports import framework
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

I = "/api/v1/imports"  # noqa: E741
SETTINGS = Settings(sign_in_limit=100_000)

HEADER = (
    "ticket_id,subject,requester_email,requester_name,status,team,message_from,message_body,"
    "message_created_at,author_name,author_email,csat_score"
)
CSV = "\n".join(
    [
        HEADER,
        "T1,Refund please,ann@example.org,Ann,solved,Billing,customer,Where is my refund?,"
        "2025-01-10T09:00:00Z,,,5",
        "T1,,,,,,agent,Refunded.,2025-01-10T10:00:00Z,Asha,asha@example.com,",
        "T1,,,,,,agent,Closing this.,2025-01-10T11:00:00Z,Sana K.,sana@old.example,",
        "T2,Parcel,bob@example.org,Bob,open,Shipping,customer,"
        "My card 4242 4242 4242 4242 was charged,2024-12-01T08:00:00Z,,,",
        ",No id,x@example.org,,,,customer,Hi,2025-01-01T00:00:00Z,,,",
    ]
)


@pytest.fixture
async def im(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(SETTINGS)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.desk_app = app  # type: ignore[attr-defined]
        yield c


def importers(c: AsyncClient) -> framework.Importers:
    found: framework.Importers = c.desk_app.state.importers  # type: ignore[attr-defined]
    return found


async def desk(c: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(c, slug, "admin@example.com")
    admin = bearer(token)
    asha = await add(c, token, "asha@example.com", "agent", "Asha")
    team = ok(
        await c.post(
            "/api/v1/teams", headers=admin, json={"name": "Billing", "member_ids": [asha]}
        ),
        201,
    )
    return {
        "ws": UUID(token.split(".")[0]),
        "admin": admin,
        "asha_id": UUID(asha),
        "asha": bearer(await sign_in(c, slug, "asha@example.com")),
        "team": team["id"],
    }


async def sql(sm: Sm, ws: UUID, query: str, **params: Any) -> list[Mapping[str, Any]]:
    async with workspace_session(sm, ws) as s:
        result = await s.execute(text(query), params)
        return list(result.mappings().all()) if result.returns_rows else []


async def end_runs(sm: Sm, ws: UUID) -> None:
    """So a run left paused by a test is never picked up by another test's `run_due`."""
    await sql(
        sm,
        ws,
        "UPDATE hd_import_run SET state = 'failed', sync_on = false "
        "WHERE state <> 'done' OR sync_on",
    )


async def upload(c: AsyncClient, w: dict[str, Any], data: str = CSV) -> dict[str, Any]:
    r = await c.post(
        f"{I}/csv",
        params={"filename": "tickets.csv"},
        content=data.encode(),
        headers={**w["admin"], "Content-Type": "text/csv"},
    )
    return ok(r, 201)  # type: ignore[no-any-return]


async def work(c: AsyncClient, sm: Sm) -> int:
    return await framework.run_due(sm, importers(c))


async def run_state(c: AsyncClient, w: dict[str, Any], run_id: str) -> dict[str, Any]:
    return ok(await c.get(f"{I}/runs/{run_id}", headers=w["admin"]))  # type: ignore[no-any-return]


# Access and dry run


async def test_only_admins_import(im: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(im, app_sessionmaker)
    r = await im.post(f"{I}/csv", params={"filename": "a.csv"}, content=b"x", headers=w["asha"])
    assert r.status_code == 403
    assert (await im.get(f"{I}/runs/{uuid4()}", headers=w["asha"])).status_code == 403
    assert (
        await im.post(f"{I}/csv", params={"filename": "a.csv"}, content=b"x")
    ).status_code == 401


async def test_a_bad_file_is_refused_and_a_big_one_too(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(im, app_sessionmaker)
    r = await im.post(
        f"{I}/csv", params={"filename": "a.csv"}, content=b"just,words\n", headers=w["admin"]
    )
    assert r.status_code == 422 and "message_body" in r.json()["error"]["message"]


async def test_discover_and_a_dry_run_save_nothing(im: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(im, app_sessionmaker)
    conn = await upload(im, w)
    found = ok(await im.get(f"{I}/connections/{conn['id']}/discover", headers=w["admin"]))
    assert found["counts"]["tickets"] == 2 and found["counts"]["groups"] == 2
    suggested = {g["name"]: g["suggested_team_id"] for g in found["groups"]}
    assert suggested == {"Billing": w["team"], "Shipping": None}  # by name, only if it exists
    assert "1 rows" in found["warnings"][0]  # the row with no ticket id

    body = {"team_map": {"Billing": w["team"]}}
    dry = ok(await im.post(f"{I}/connections/{conn['id']}/dry-run", headers=w["admin"], json=body))
    assert dry["will_create"]["tickets"] == 2 and dry["already_imported"]["tickets"] == 0
    assert dry["warnings"] == ["No team picked for 'Shipping'."] or any(
        "No team picked for 'Shipping'" in x for x in dry["warnings"]
    )
    assert not any("'Billing'" in x and x.startswith("No team") for x in dry["warnings"])
    assert (
        await sql(app_sessionmaker, w["ws"], "SELECT 1 FROM hd_ticket WHERE source = 'csv'") == []
    )
    assert await sql(app_sessionmaker, w["ws"], "SELECT 1 FROM hd_import_run") == []

    bad = await im.post(
        f"{I}/connections/{conn['id']}/dry-run",
        headers=w["admin"],
        json={"team_map": {"Billing": str(uuid4())}},
    )
    assert bad.status_code == 422  # a team that doesn't exist


# A CSV through the API and the worker


async def test_a_csv_import_end_to_end(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(im, sm)
    conn = await upload(im, w)
    started = ok(
        await im.post(
            f"{I}/connections/{conn['id']}/run",
            headers=w["admin"],
            json={"team_map": {"Billing": w["team"]}},
        ),
        202,
    )
    assert started["state"] == "queued"
    await work(im, sm)
    run = await run_state(im, w, started["id"])
    assert run["state"] == "done", run
    tickets = run["counts"]["tickets"]
    assert (tickets["created"], tickets["skipped"], tickets["unknown_agent_messages"]) == (2, 0, 1)
    assert tickets["files"] == 0 and tickets["updated"] == 0
    assert run["counts"]["rows_left_out"] == 1

    t = {
        r["source_id"]: r
        for r in await sql(
            sm, w["ws"], "SELECT * FROM hd_ticket WHERE source = 'csv' ORDER BY source_id"
        )
    }
    assert set(t) == {"T1", "T2"}
    one, two = t["T1"], t["T2"]
    assert one["status"] == "resolved" and one["resolved_at"] is not None
    assert one["created_at"] == datetime(2025, 1, 10, 9, 0, tzinfo=UTC)
    assert one["first_reply_at"] == datetime(2025, 1, 10, 10, 0, tzinfo=UTC)
    assert "imported-csv" in one["tags"] and str(one["team_id"]) == w["team"]
    assert one["assignee_id"] is None  # imported tickets arrive unassigned
    assert two["status"] == "open" and two["team_id"] is None  # Shipping wasn't picked

    msgs = await sql(
        sm, w["ws"],
        "SELECT kind, body, author_member_id, author_name FROM hd_message "
        "WHERE ticket_id = :t ORDER BY created_at", t=one["id"])  # fmt: skip
    assert [m["kind"] for m in msgs] == ["system", "customer", "agent", "agent"]
    assert msgs[0]["body"] == (
        "Imported from CSV file with full history · Billing · source ID T1 · was solved"
    )
    assert msgs[2]["author_member_id"] == w["asha_id"]  # matched by email
    assert msgs[3]["author_member_id"] is None
    assert msgs[3]["author_name"] == "Sana K. (CSV file, not in CeDesk)"

    card = await sql(
        sm, w["ws"], "SELECT kind, body FROM hd_message WHERE ticket_id = :t", t=two["id"]
    )
    assert not any("4242" in m["body"] for m in card)  # removed before saving, for good
    assert any(m["kind"] == "system" and "card number" in m["body"] for m in card)

    rating = await sql(sm, w["ws"], "SELECT score, source FROM hd_csat")
    assert [(r["score"], r["source"]) for r in rating] == [(5, "csv")]
    ann = await sql(
        sm, w["ws"], "SELECT customer_since FROM hd_contact WHERE email = 'ann@example.org'"
    )
    assert ann[0]["customer_since"] == datetime(2025, 1, 10, 9, 0, tzinfo=UTC)

    # Credentials and the file are deleted when the run ends; the history stays.
    row = (
        await sql(
            sm, w["ws"], "SELECT state, credentials_sealed, settings FROM hd_import_connection"
        )
    )[0]
    assert row["state"] == "closed" and "file_key" not in row["settings"]
    kinds = [
        e["kind"]
        for e in await sql(sm, w["ws"], "SELECT kind FROM hd_import_event ORDER BY created_at, id")
    ]
    assert kinds == ["connected", "started", "finished", "credentials_deleted"]
    # The Admin's steps are in the audit log too (S9), and so is the worker's finish.
    logged = ok(await im.get("/api/v1/audit", headers=w["admin"], params={"area": "imports"}))
    assert [(e["action"], e["target"]) for e in logged["entries"]] == [
        ("finished", "Import from CSV file"),
        ("started", "Import from CSV file"),
        ("connected", "Import from a CSV file"),
    ]
    again = await im.get(f"{I}/connections/{conn['id']}/discover", headers=w["admin"])
    assert again.status_code == 409

    # Another workspace can't see the run.
    other = await desk(im, sm)
    assert (await im.get(f"{I}/runs/{started['id']}", headers=other["admin"])).status_code == 404


async def test_an_import_stays_out_of_live_work(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(im, sm)
    conn = await upload(im, w)
    ok(await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json={}), 202)
    await work(im, sm)

    # No events, no webhooks. (Adding Asha in `desk` sends the Admins a security alert.)
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_outbox WHERE event <> 'security.alert'") == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_notification") == []
    async with workspace_session(sm, w["ws"]) as s:  # an old open ticket pages nobody
        assert await sla_repo.newly_breached(s, datetime.now(UTC), 100) == []
    # No rule ran and nobody was routed.
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_message WHERE body LIKE 'Rule applied%'") == []

    # Live tickets carry on after the imported ones' numbers; nothing was held.
    imported = {r["number"] for r in await sql(sm, w["ws"], "SELECT number FROM hd_ticket")}
    live = ok(
        await im.post(
            "/api/v1/tickets",
            headers=w["admin"],
            json={"subject": "Live", "contact": {"email": "live@example.org", "name": "Live"}},
        ),
        201,
    )
    assert live["number"] > max(imported) and len(imported) == 2
    await end_runs(sm, w["ws"])


async def test_running_again_creates_no_duplicates(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(im, sm)
    first = await upload(im, w)
    ok(await im.post(f"{I}/connections/{first['id']}/run", headers=w["admin"], json={}), 202)
    await work(im, sm)
    second = await upload(im, w)
    run = ok(await im.post(f"{I}/connections/{second['id']}/run", headers=w["admin"], json={}), 202)
    await work(im, sm)
    done = await run_state(im, w, run["id"])
    assert done["state"] == "done"
    assert done["counts"]["tickets"]["created"] == 0 and done["counts"]["tickets"]["skipped"] == 2
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket WHERE source = 'csv'")) == 2
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_csat")) == 1
    await end_runs(sm, w["ws"])


# A fake help desk: contacts, articles, saved replies, agents by email, resume, 429


class Fake:
    source = "zendesk"
    status_map: Mapping[str, str] = {"solved": "resolved", "open": "open", "hold": "pending"}

    def __init__(self, *, tickets: int = 0, page_size: int = 5) -> None:
        self.page_size = page_size
        self.contacts: list[Any] = []
        self.articles: list[Any] = []
        self.replies: list[Any] = []
        self.group_list = [Group("g1", "Billing")]
        self.tickets = [self.ticket(i) for i in range(tickets)]
        self.fail: dict[tuple[str, int], Exception] = {}

    @staticmethod
    def ticket(i: int, **over: Any) -> Ticket:
        at = datetime(2025, 2, 1, 8, 0, tzinfo=UTC) + timedelta(hours=i)
        fields: dict[str, Any] = {
            "subject": f"Subject {i}",
            "status": "solved",
            "created_at": at,
            "requester": Contact(f"u{i}", f"user{i}@example.org", f"User {i}"),
            "group_id": "g1",
            "messages": [Message("customer", f"Hello {i}", at, external_id=f"m{i}")],
        }
        return Ticket(external_id=f"Z{i}", **{**fields, **over})

    async def check(self) -> AccountInfo:
        return AccountInfo("acme.zendesk.com")

    async def discover(self) -> Counts:
        return Counts(len(self.contacts), len(self.tickets), len(self.articles),
                      len(self.replies), len(self.group_list))  # fmt: skip

    async def groups(self) -> Sequence[Group]:
        return self.group_list

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        items: list[Any] = getattr(self, kind)
        start = int(cursor["offset"]) if cursor else 0
        failure = self.fail.pop((kind, start), None)
        if failure is not None:
            raise failure
        chunk = items[start : start + self.page_size]
        end = start + len(chunk)
        return Page(chunk, {"offset": end} if end < len(items) else None)


async def fake_desk(im: AsyncClient, sm: Sm, source: Fake) -> tuple[dict[str, Any], dict[str, Any]]:
    app = im.desk_app  # type: ignore[attr-defined]

    async def build(name: str, settings: Mapping[str, Any], secret: str | None) -> Fake:
        return source

    app.state.importers = framework.make_importers(
        SETTINGS.secret_key.get_secret_value(), app.state.store, build=build
    )
    w = await desk(im, sm)
    r = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "zendesk", "settings": {"subdomain": "acme"},
              "credentials": {"token": "tok-secret-1234"}},
    )  # fmt: skip
    conn = ok(r, 201)
    assert conn["credentials_hint"] == "1234" and "tok-secret" not in r.text
    return w, conn


async def start(c: AsyncClient, w: dict[str, Any], conn: dict[str, Any], **options: Any) -> str:
    r = await c.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json=options)
    return str(ok(r, 202)["id"])


async def test_a_help_desks_records_are_matched_and_mapped(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Fake()
    src.contacts = [Contact("c1", None, "No Email"), Contact("c2", "dee@example.org", "Dee")]
    src.tickets = [
        Fake.ticket(
            1,
            requester=Contact("c1", None, "No Email"),
            assignee_email="Asha@Example.com",
            csat=Csat(4, "ok"),
            messages=[
                Message("customer", "Help", datetime(2025, 2, 1, 8, 0, tzinfo=UTC)),
                Message(
                    "note",
                    "Internal",
                    datetime(2025, 2, 1, 8, 5, tzinfo=UTC),
                    author_name="Lee",
                    author_email="lee@old.example",
                ),
            ],
        )
    ]
    src.articles = [
        Article("a1", "How to refund", "Steps", Category("k1", "Refunds"), True),
        Article("a2", "Draft idea", "Later", None, False),
    ]
    src.replies = [Reply("r1", "hello", "Hi there")]
    w, conn = await fake_desk(im, sm, src)
    ok(await im.post("/api/v1/saved-replies", headers=w["admin"],
                     json={"shortcut": "hello", "body": "Mine"}), 201)  # fmt: skip

    run_id = await start(im, w, conn, team_map={"g1": w["team"]})
    await work(im, sm)
    run = await run_state(im, w, run_id)
    assert run["state"] == "done", run
    assert run["counts"]["contacts"]["created"] == 2

    contact = await sql(sm, w["ws"], "SELECT email FROM hd_contact WHERE email LIKE '%.invalid'")
    assert [c["email"] for c in contact] == ["zendesk-c1@zendesk.invalid"]
    ticket = (await sql(sm, w["ws"], "SELECT * FROM hd_ticket WHERE source = 'zendesk'"))[0]
    assert ticket["assignee_id"] == w["asha_id"] and str(ticket["team_id"]) == w["team"]
    assert ticket["status"] == "resolved"
    note = (await sql(sm, w["ws"], "SELECT author_name, kind FROM hd_message WHERE kind = 'note'"))[
        0
    ]
    assert note["author_name"] == "Lee (Zendesk, not in CeDesk)"

    arts = await sql(sm, w["ws"], "SELECT title, status FROM hd_article ORDER BY title")
    assert [(a["title"], a["status"]) for a in arts] == [
        ("Draft idea", "draft"), ("How to refund", "published")]  # fmt: skip
    cats = await sql(sm, w["ws"], "SELECT name, source FROM hd_article_category")
    assert [(c["name"], c["source"]) for c in cats] == [("Refunds", "zendesk")]
    shortcuts = {
        r["shortcut"] for r in await sql(sm, w["ws"], "SELECT shortcut FROM hd_saved_reply")
    }
    assert {"hello", "hello1"} <= shortcuts  # a clash gets a suffix, never replaces yours

    # Connect again and import again: nothing is made twice.
    second = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "zendesk", "settings": {}, "credentials": {"token": "tok-secret-5678"}},
    )
    conn2 = ok(second, 201)
    again = await start(im, w, conn2, team_map={"g1": w["team"]})
    await work(im, sm)
    done = await run_state(im, w, again)
    assert done["state"] == "done"
    assert done["counts"]["tickets"]["created"] == 0
    assert done["counts"]["articles"]["created"] == 0 and done["counts"]["replies"]["created"] == 0
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket WHERE source = 'zendesk'")) == 1
    assert (
        len(await sql(sm, w["ws"], "SELECT 1 FROM hd_saved_reply WHERE shortcut LIKE 'hello%'"))
        == 2
    )
    await end_runs(sm, w["ws"])


async def test_a_crash_resumes_at_the_page_it_stopped_on(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Fake(tickets=12)
    src.fail[("tickets", 5)] = SourceError("The source is down.", permanent=False)
    w, conn = await fake_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await work(im, sm)
    paused = await run_state(im, w, run_id)
    assert paused["state"] == "running" and "down" in paused["last_error"]
    assert paused["counts"]["tickets"]["created"] == 5  # the first page was kept
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 5

    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    done = await run_state(im, w, run_id)
    assert done["state"] == "done" and done["counts"]["tickets"]["created"] == 12
    numbers = [r["number"] for r in await sql(sm, w["ws"], "SELECT number FROM hd_ticket")]
    assert len(set(numbers)) == 12
    await end_runs(sm, w["ws"])


async def test_a_429_pauses_for_retry_after(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    src = Fake(tickets=3)
    src.fail[("tickets", 0)] = RateLimited(30)
    w, conn = await fake_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await work(im, sm)
    row = (await sql(sm, w["ws"], "SELECT state, next_run_at, last_error FROM hd_import_run "
                     "WHERE id = :i", i=run_id))[0]  # fmt: skip
    wait = (row["next_run_at"] - datetime.now(UTC)).total_seconds()
    assert row["state"] == "running" and 20 < wait <= 31 and "rate limiting" in row["last_error"]
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket")) == 0
    await end_runs(sm, w["ws"])


async def test_a_permanent_error_fails_the_run_and_says_why(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Fake(tickets=3)
    src.fail[("contacts", 0)] = SourceError("The token was refused.", permanent=True)
    w, conn = await fake_desk(im, sm, src)
    run_id = await start(im, w, conn)
    await work(im, sm)
    run = await run_state(im, w, run_id)
    assert run["state"] == "failed" and run["last_error"] == "The token was refused."
    kinds = [
        e["kind"]
        for e in await sql(sm, w["ws"], "SELECT kind FROM hd_import_event ORDER BY created_at, id")
    ]
    assert kinds[-1] == "failed"
    # A failed run keeps the connection, so it can be started again; closing deletes the key.
    ok(
        await im.post(
            f"{I}/connections/{conn['id']}/run", headers=w["admin"], json={"kinds": ["tickets"]}
        ),
        202,
    )
    await end_runs(sm, w["ws"])
    assert (
        await im.post(f"{I}/connections/{conn['id']}/close", headers=w["admin"])
    ).status_code == 204
    row = (await sql(sm, w["ws"], "SELECT state, credentials_sealed FROM hd_import_connection"))[0]
    assert row["state"] == "closed" and row["credentials_sealed"] is None


async def test_one_run_at_a_time_per_connection(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w, conn = await fake_desk(im, sm, Fake(tickets=1))
    await start(im, w, conn)
    r = await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json={})
    assert r.status_code == 409
    await end_runs(sm, w["ws"])


# Idempotency-Key, and the sources not offered yet


async def test_a_repeated_key_returns_the_first_connection_and_run(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(im, sm)
    headers = {**w["admin"], "Content-Type": "text/csv", "Idempotency-Key": "csv-upload-1"}
    first = await im.post(
        f"{I}/csv", params={"filename": "t.csv"}, content=CSV.encode(), headers=headers
    )
    again = await im.post(
        f"{I}/csv", params={"filename": "t.csv"}, content=CSV.encode(), headers=headers
    )
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_import_connection")) == 1
    # The same key for a different file is refused, not mistaken for a repeat.
    other = await im.post(
        f"{I}/csv", params={"filename": "t.csv"}, content=CSV.encode() + b" ", headers=headers
    )
    assert other.status_code == 422

    conn_id = first.json()["id"]
    run_headers = {**w["admin"], "Idempotency-Key": "run-1"}
    one = ok(await im.post(f"{I}/connections/{conn_id}/run", headers=run_headers, json={}), 202)
    two = await im.post(f"{I}/connections/{conn_id}/run", headers=run_headers, json={})
    assert two.status_code == 202 and two.json()["id"] == one["id"]  # not a 409
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_import_run")) == 1
    await end_runs(sm, w["ws"])


async def test_hubspot_and_zoho_are_not_offered_yet(im: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(im, app_sessionmaker)
    for source in ("hubspot", "zoho"):
        r = await im.post(
            f"{I}/connections",
            headers=w["admin"],
            json={"source": source, "settings": {}, "credentials": {"token": "x"}},
        )
        assert r.status_code == 422
