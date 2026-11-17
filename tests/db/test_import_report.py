"""A run's report says what it brought and what it couldn't, and never calls incomplete
data complete (P0-3, P0-6, P1-12). Real PostgreSQL with RLS on; a fake source.

- a complete run is `complete`, with the source's counts reconciled;
- a body longer than CeDesk's message holds is kept whole, as a .txt file on the message;
- tags past CeDesk's 20 are kept in the field `more_tags`; the type and the address of the
  ticket in the source are kept as fields;
- a satisfaction rating is imported whatever the ticket's status is now;
- the source's warnings are carried into the run; the source counting more than the run
  accounted for makes it `complete_with_exceptions`;
- a date the source didn't give is reported, never silently 1970 or now.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import Counts, Csat, Message
from tests.db.helpers import Sm, ok
from tests.db.test_import_failures import Flaky, flaky_desk, im, pass_
from tests.db.test_imports import Fake, I, end_runs, run_state, sql, start

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared


async def run_once(im: AsyncClient, sm: Sm, src: Flaky) -> tuple[dict[str, Any], dict[str, Any]]:
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    return w, await run_state(im, w, run_id)


async def test_a_run_with_nothing_left_out_is_complete_and_reconciled(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    w, run = await run_once(im, app_sessionmaker, Flaky(tickets=7))
    assert run["state"] == "done" and run["outcome"] == "complete"
    assert run["report"] == {
        "open_failures": 0,
        "losses": [],
        "warnings": [],
        "reconciliation": {
            "tickets": {"in_source": 7, "accounted_for": 7, "difference": 0},
        },
    }
    await end_runs(app_sessionmaker, w["ws"])


async def test_a_long_body_is_kept_whole_as_a_file(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    src = Flaky()
    long = "word " * 20_000  # 100,000 characters: twice what a CeDesk message holds
    at = datetime(2025, 2, 1, 8, 0, tzinfo=UTC)
    src.tickets = [Fake.ticket(0, messages=[Message("customer", long, at, "m0")])]
    w, run = await run_once(im, sm, src)

    assert run["state"] == "done" and run["outcome"] == "complete"  # nothing was lost
    assert run["report"]["losses"] == [
        {"key": "body_as_file", "count": 1, "meaning": rules.LOSS_KEYS["body_as_file"]}
    ]
    [msg] = await sql(sm, w["ws"], "SELECT body FROM hd_message WHERE kind = 'customer'")
    assert len(msg["body"]) <= rules.BODY_MAX and "full-message.txt" in msg["body"]
    [file] = await sql(sm, w["ws"], "SELECT name, size, mime FROM hd_attachment")
    assert (file["name"], file["mime"]) == ("full-message.txt", "text/plain")
    assert file["size"] == len(long.strip().encode())
    await end_runs(sm, w["ws"])


async def test_tags_type_and_source_address_are_kept(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = [
        Fake.ticket(0, tags=[f"t{i}" for i in range(23)], ticket_type="incident",
                    source_url="https://acme.zendesk.com/agent/tickets/0")
    ]  # fmt: skip
    w, run = await run_once(im, sm, src)
    [t] = await sql(sm, w["ws"], "SELECT tags, fields FROM hd_ticket")
    assert len(t["tags"]) == 20
    assert t["fields"]["more_tags"] == "t19, t20, t21, t22"  # none dropped
    assert t["fields"]["source_type"] == "incident"
    assert t["fields"]["source_url"] == "https://acme.zendesk.com/agent/tickets/0"
    assert run["outcome"] == "complete_with_exceptions"  # tags moved to a field: reported
    assert [x["key"] for x in run["report"]["losses"]] == ["tags_over_limit"]
    await end_runs(sm, w["ws"])


async def test_a_rating_is_imported_whatever_the_ticket_status(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Before (audit C11), a rating on a ticket that is open now was dropped."""
    sm = app_sessionmaker
    src = Flaky()
    rated = datetime(2025, 2, 3, tzinfo=UTC)
    src.tickets = [
        Fake.ticket(0, status="open", csat=Csat(4, "ok", rated)),
        Fake.ticket(1, status="hold", csat=Csat(2, None, None)),
        Fake.ticket(2, status="solved", csat=Csat(5, None, None)),
    ]
    w, _ = await run_once(im, sm, src)
    rows = await sql(
        sm, w["ws"],
        "SELECT t.source_id, t.status, c.score, c.rated_at FROM hd_csat c "
        "JOIN hd_ticket t ON t.id = c.ticket_id ORDER BY t.source_id",
    )  # fmt: skip
    assert [(r["source_id"], r["status"], r["score"]) for r in rows] == [
        ("Z0", "open", 4), ("Z1", "pending", 2), ("Z2", "resolved", 5),
    ]  # fmt: skip
    assert rows[0]["rated_at"] == rated
    assert rows[1]["rated_at"] is not None  # the last message's time when the source gave none
    await end_runs(sm, w["ws"])


class Warned(Flaky):
    def warnings(self) -> list[str]:
        return ["Help Center articles can't be read through this API."]

    async def discover(self) -> Counts:
        return replace(await super().discover(), tickets=len(self.tickets) + 2)


async def test_warnings_are_carried_and_a_shortfall_is_an_exception(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    w, run = await run_once(im, app_sessionmaker, Warned(tickets=3))
    assert run["state"] == "done" and run["outcome"] == "complete_with_exceptions"
    report = run["report"]
    assert report["warnings"] == ["Help Center articles can't be read through this API."]
    assert report["reconciliation"]["tickets"] == {
        "in_source": 5, "accounted_for": 3, "difference": 2,
    }  # fmt: skip
    await end_runs(app_sessionmaker, w["ws"])


class Undated(Flaky):
    async def page(self, kind: str, cursor: Any) -> Any:
        found = await super().page(kind, cursor)
        if kind == "tickets":
            for t in found.items:
                rules.dated(self.failures, "ticket", t.external_id, None)
        return found


async def test_a_missing_date_is_reported_with_the_record(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w, run = await run_once(im, sm, Undated(tickets=1))
    assert run["outcome"] == "complete_with_exceptions"
    assert [x["key"] for x in run["report"]["losses"]] == ["date_missing"]
    page = ok(await im.get(f"{I}/runs/{run['id']}/failures", headers=w["admin"]))
    [noted] = page["failures"]
    assert noted["external_id"] == "Z0" and "no valid creation date" in noted["last_error"]
    await end_runs(sm, w["ws"])
