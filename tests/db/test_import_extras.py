"""What an import keeps beyond the conversation (P2-21..23), real PostgreSQL with RLS on:

- a contact's phone and organization, filled only where blank, cleared by erasure;
- a message's recipients (to, cc, bcc) in `recipients`, never in `email`: a reply to an
  imported ticket goes only to the customer, as before;
- the ticket's cc, followers, links and SLA kept as source fields (agents see them); a link
  to a ticket already imported becomes a CeDesk link.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain.imports import Contact, Link, Message
from tests.db.helpers import Sm, ok
from tests.db.test_import_failures import Flaky, flaky_desk, im, pass_
from tests.db.test_imports import Fake, end_runs, sql, start

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared

T = "/api/v1/tickets"
AT = datetime(2025, 2, 1, 8, 0, tzinfo=UTC)


def rich() -> list[Any]:
    priya = Contact("u1", "priya@example.org", "Priya", phone="+91 98000 00001",
                    organization="Globex")  # fmt: skip
    first = replace(
        Fake.ticket(1), requester=priya, cc=["ops@globex.test", "ops@globex.test"],
        followers=["neha@example.com"], sla_due_at=AT, sla_breached=True,
        messages=[Message("customer", "Hello", AT, external_id="m1", to=["help@acme.test"],
                          cc=["ops@globex.test"], bcc=["audit@globex.test"])],
    )  # fmt: skip
    second = replace(Fake.ticket(2), links=[Link("related", "Z1"), Link("parent", "Z404")])
    return [first, second]


async def test_contacts_recipients_and_ticket_extras_are_kept(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = rich()
    w, conn, importers = await flaky_desk(im, sm, src)
    await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])

    [c] = await sql(sm, w["ws"], "SELECT phone, organization FROM hd_contact "
                    "WHERE email = 'priya@example.org'")  # fmt: skip
    assert (c["phone"], c["organization"]) == ("+91 98000 00001", "Globex")
    [m] = await sql(sm, w["ws"], "SELECT recipients, email FROM hd_message WHERE kind = 'customer' "
                    "AND body = 'Hello'")  # fmt: skip
    assert m["recipients"] == {"to": ["help@acme.test"], "cc": ["ops@globex.test"],
                               "bcc": ["audit@globex.test"]}  # fmt: skip
    assert m["email"] is None  # replies never read imported recipients

    [one] = await sql(sm, w["ws"], "SELECT id, fields FROM hd_ticket WHERE source_id = 'Z1'")
    assert one["fields"]["source_cc"] == "ops@globex.test"
    assert one["fields"]["source_followers"] == "neha@example.com"
    assert one["fields"]["source_sla_breached"] == "true"
    assert one["fields"]["source_sla_due_at"].startswith("2025-02-01T08:00")
    [two] = await sql(sm, w["ws"], "SELECT id, fields FROM hd_ticket WHERE source_id = 'Z2'")
    assert two["fields"]["source_links"] == "related Z1; parent Z404"
    links = await sql(sm, w["ws"], "SELECT ticket_a, ticket_b FROM hd_link")
    assert [{x["ticket_a"], x["ticket_b"]} for x in links] == [{one["id"], two["id"]}]

    seen = ok(await im.get(f"{T}/{one['id']}", headers=w["admin"]))
    assert seen["contact"]["phone"] == "+91 98000 00001"
    assert seen["contact"]["organization"] == "Globex"
    hello = next(x for x in seen["messages"] if x["body"] == "Hello")
    assert hello["recipients"]["cc"] == ["ops@globex.test"]
    await end_runs(sm, w["ws"])


async def test_an_import_never_overwrites_a_phone_an_agent_set_and_erasure_clears_both(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Flaky()
    src.tickets = rich()[:1]
    w, conn, importers = await flaky_desk(im, sm, src)
    await sql(
        sm, w["ws"], "INSERT INTO hd_contact (workspace_id, email, name, phone) "
        "VALUES (:wid, 'priya@example.org', 'Priya', '+1 555 0100')", wid=w["ws"],
    )  # fmt: skip
    await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    [c] = await sql(sm, w["ws"], "SELECT id, phone, organization FROM hd_contact")
    assert (c["phone"], c["organization"]) == ("+1 555 0100", "Globex")

    erase = {"contact_id": str(c["id"]), "confirm_email": "priya@example.org"}
    ok(await im.post("/api/v1/privacy/erase", headers=w["admin"], json=erase), 201)
    [c] = await sql(sm, w["ws"], "SELECT phone, organization FROM hd_contact")
    assert (c["phone"], c["organization"]) == (None, None)
    left = await sql(sm, w["ws"], "SELECT 1 FROM hd_message WHERE recipients IS NOT NULL")
    assert left == []  # the addresses a message went to are erased with it
    await end_runs(sm, w["ws"])


async def test_articles_keep_their_dates_translations_and_a_cut_is_counted(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """P2-28: before, every article was dated the import's day, a translation had the same
    title as its original, and a body over 100,000 characters was cut without a word."""
    from cedesk.helpdesk.domain.imports import Article

    sm = app_sessionmaker
    src = Flaky()
    src.articles = [
        Article("a1", "Refunds", "How refunds work", published_at=AT, updated_at=AT),
        Article("a2", "Refunds", "Comment rembourser", locale="fr", translation_of="a1"),
        Article("a3", "Long", "x" * 100_001, published=False),
    ]
    w, conn, importers = await flaky_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["articles"])
    await pass_(sm, importers, w["ws"])
    rows = await sql(sm, w["ws"], "SELECT title, published_at, created_at, updated_at, "
                     "length(body) AS n FROM hd_article ORDER BY source_id")  # fmt: skip
    assert [r["title"] for r in rows] == ["Refunds", "Refunds (fr)", "Long"]
    assert rows[0]["published_at"] == rows[0]["created_at"] == rows[0]["updated_at"] == AT
    assert rows[2]["n"] == 100_000 and rows[2]["published_at"] is None
    run = ok(await im.get(f"/api/v1/imports/runs/{run_id}", headers=w["admin"]))
    assert ("article_cut", 1) in [(x["key"], x["count"]) for x in run["report"]["losses"]]
    await end_runs(sm, w["ws"])


async def test_the_card_number_line_sits_after_the_message_it_was_taken_from(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Live B-32: the "card number removed" line was dated at the last message, so in a long
    thread it showed far from the message the number was taken from."""
    from datetime import timedelta

    sm = app_sessionmaker
    src = Flaky()
    src.tickets = [replace(Fake.ticket(1), messages=[
        Message("customer", "My card is 4242 4242 4242 4242, refund it", AT, external_id="a"),
        Message("agent", "On it.", AT + timedelta(hours=1), external_id="b"),
        Message("customer", "Thanks", AT + timedelta(days=3), external_id="c"),
    ])]  # fmt: skip
    w, conn, importers = await flaky_desk(im, sm, src)
    await start(im, w, conn, kinds=["tickets"])
    await pass_(sm, importers, w["ws"])
    rows = await sql(sm, w["ws"], "SELECT kind, body FROM hd_message ORDER BY created_at, id")
    kinds = [
        "card" if r["kind"] == "system" and "card number" in r["body"] else r["kind"] for r in rows
    ]
    assert kinds == ["system", "customer", "card", "agent", "customer"], kinds
    assert not any("4242" in r["body"] for r in rows)
    await end_runs(sm, w["ws"])
