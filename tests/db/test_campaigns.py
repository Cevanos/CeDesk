"""Email campaigns (feature-gap plan phase 6) through the real API with RLS on: nothing is
sent before the compliance checks pass, recipients are the segment minus suppressed,
placeholder, role and support addresses, the worker sends each one at most once within
the workspace's limits, unsubscribe (page and one-click) and opens come back through
public routes, bounces and spam reports through email in, results and CSV, Admins only,
audited without addresses, and another workspace sees nothing."""

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from cedesk.helpdesk.domain.campaigns import Limits, Links, email_hash
from cedesk.helpdesk.service import campaigns
from cedesk.helpdesk.service.campaigns import Config
from cedesk.helpdesk.service.email_in import Outcome, receive
from cedesk.mail import OutgoingEmail, SendError
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, bearer, ok, sign_in
from tests.db.inbox_helpers import as_, audit, desk, sql
from tests.db.test_contacts import customer

pytestmark = pytest.mark.db

K = "/api/v1/campaigns"
SUP = "/api/v1/suppressions"
SETTINGS = "/api/v1/campaigns/settings"
DNS_OK = {"spf": {"status": "ok"}, "dkim": {"status": "ok"}, "dmarc": {"status": "ok"}}
POSTAL = "Acme Pvt Ltd\n12 MG Road, Bengaluru 560001"
CONFIG = Config(
    limits=Limits(max_recipients=100, daily_cap=1000, per_minute=1000),
    links=Links(page_url="https://portal.test", api_url="https://api.test"),
    require_https=False,
)


class FakeMailer:
    """Records our workspace's campaign emails; other tests' are accepted and ignored."""

    def __init__(self, domain: str, fail: SendError | None = None) -> None:
        self.domain, self.fail = domain, fail
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        if not email.from_address.endswith("@" + self.domain):
            return
        if self.fail is not None:
            raise self.fail
        self.sent.append(email)


async def camp_desk(client: AsyncClient, sm: Sm, *, verified: bool = True) -> dict[str, Any]:
    """`desk` plus a verified support address (SPF and DKIM passing), the postal address,
    three shop customers, a chat customer, and the "Shop" segment (everyone)."""
    w = await desk(client, sm)
    domain = f"{w['slug']}.test"
    body = {"address": f"news@{domain}", "name": "Acme News"}
    addr = ok(await client.post("/api/v1/email-addresses", headers=w["h"], json=body), 201)
    if verified:
        await sql(sm, w["ws"], "UPDATE hd_email_address SET forwarding_verified_at = now(), "
                  "dns_result = CAST(:d AS jsonb) WHERE id = :id",
                  d=json.dumps(DNS_OK), id=addr["id"])  # fmt: skip
        ok(await client.put(SETTINGS, headers=w["h"], json={"postal_address": POSTAL}))
    people = {}
    for name, email in (("Ana Silva", "ana@shop.test"), ("Bo", "bo@shop.test"),
                        ("Cy", "cy@shop.test")):  # fmt: skip
        people[name.split()[0]], _ = await customer(client, w, name, email)
    seg = ok(await client.post("/api/v1/segments", headers=w["h"],
                               json={"name": "Shop", "filters": {}}), 201)  # fmt: skip
    return {**w, "domain": domain, "address": addr, "segment": seg, "people": people}


async def draft(client: AsyncClient, w: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body = {
        "name": "November news",
        "segment_id": w["segment"]["id"],
        "subject": "Hi {{customer.first_name}}, our news",
        "body": "Hello {{customer.first_name}},\n\n**New** shoes: [see them](https://shop.test/new)",
        "from_address_id": w["address"]["id"],
        **extra,
    }
    result: dict[str, Any] = ok(await client.post(K, headers=w["h"], json=body), 201)
    return result


def token_of(email: OutgoingEmail) -> str:
    header = dict(email.headers)["List-Unsubscribe"]
    found = re.search(r"/public/unsubscribe/([^>]+)>", header)
    assert found is not None
    return found.group(1)


def done(r: Any) -> None:
    assert r.status_code == 204, r.text


def mine(mailer: FakeMailer) -> list[str]:
    return sorted(e.to[0] for e in mailer.sent)


async def test_nothing_is_sent_before_the_checks_pass(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await camp_desk(client, app_sessionmaker, verified=False)
    c = await draft(client, w)
    problems = " ".join(c["problems"])
    assert "Verify the support address" in problems and "postal address" in problems
    r = await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True})
    assert r.status_code == 422 and "postal address" in r.json()["error"]["message"]
    # Verified, but its domain's DNS isn't right yet.
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_email_address SET "
              "forwarding_verified_at = now() WHERE id = :id", id=w["address"]["id"])  # fmt: skip
    c = ok(await client.get(f"{K}/{c['id']}", headers=w["h"]))
    assert any("SPF and DKIM" in p for p in c["problems"])
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_email_address SET dns_result = "
              "CAST(:d AS jsonb) WHERE id = :id", d=json.dumps(DNS_OK),
              id=w["address"]["id"])  # fmt: skip
    for bad in ("short", "x" * 301):
        r = await client.put(SETTINGS, headers=w["h"], json={"postal_address": bad})
        assert r.status_code == 422
    got = ok(await client.put(SETTINGS, headers=w["h"], json={"postal_address": POSTAL}))
    assert got["postal_address"] == POSTAL and got["max_recipients"] == 5000
    assert ok(await client.get(f"{K}/{c['id']}", headers=w["h"]))["problems"] == []
    # An explicit confirm is needed.
    r = await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": False})
    assert r.status_code == 422
    assert ok(await client.get(f"{K}/{c['id']}", headers=w["h"]))["state"] == "draft"
    empty = await draft(client, w, subject="", body="")
    r = await client.post(f"{K}/{empty['id']}/send", headers=w["h"], json={"confirm": True})
    assert r.status_code == 422 and "subject" in r.json()["error"]["message"]


async def test_the_suppression_list(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await camp_desk(client, app_sessionmaker)
    h = w["h"]
    added = ok(await client.post(SUP, headers=h, json={"email": " Bo@Shop.test ",
                                                       "note": "asked by phone"}), 201)  # fmt: skip
    assert (added["email"], added["reason"], added["source"]) == ("bo@shop.test", "manual", "admin")
    assert (await client.post(SUP, headers=h, json={"email": "bo@shop.test"})).status_code == 409
    assert (await client.post(SUP, headers=h, json={"email": "nope"})).status_code == 422
    page = ok(await client.get(SUP, headers=h))
    assert [e["email"] for e in page["entries"]] == ["bo@shop.test"]
    assert page["counts"] == {"manual": 1}
    one = ok(await client.get(SUP, headers=h, params={"email": "BO@shop.test"}))
    assert len(one["entries"]) == 1
    assert ok(await client.get(SUP, headers=h, params={"email": "cy@shop.test"}))["entries"] == []
    # The audience leaves Bo out.
    c = await draft(client, w)
    aud = ok(await client.get(f"{K}/{c['id']}/audience", headers=h))
    assert (aud["in_segment"], aud["suppressed"], aud["recipients"]) == (3, 1, 2)
    # A manual entry comes off; an unsubscribe only with the person's consent; an erased
    # person's never.
    done(await client.post(f"{SUP}/{added['id']}/remove", headers=h, json={}))
    await sql(app_sessionmaker, w["ws"], "INSERT INTO hd_suppression (workspace_id, email_hash, "
              "email, reason, source) VALUES (:wid, :h, 'cy@shop.test', 'unsubscribed', 'link')",
              wid=w["ws"], h=email_hash(w["ws"], "cy@shop.test"))  # fmt: skip
    unsub = ok(await client.get(SUP, headers=h, params={"reason": "unsubscribed"}))["entries"][0]
    r = await client.post(f"{SUP}/{unsub['id']}/remove", headers=h, json={})
    assert r.status_code == 409
    r = await client.post(f"{SUP}/{unsub['id']}/remove", headers=h,
                          json={"confirm_consent": True, "justification": "short"})  # fmt: skip
    assert r.status_code == 409
    why = "Cy emailed us from cy@shop.test on 20 Nov asking to get news again"
    done(await client.post(f"{SUP}/{unsub['id']}/remove", headers=h,
                         json={"confirm_consent": True, "justification": why}))  # fmt: skip
    await sql(app_sessionmaker, w["ws"], "INSERT INTO hd_suppression (workspace_id, email_hash, "
              "reason, source) VALUES (:wid, :h, 'erased', 'erase')", wid=w["ws"],
              h=b"\x01" * 32)  # fmt: skip
    gone = ok(await client.get(SUP, headers=h, params={"reason": "erased"}))["entries"][0]
    assert gone["email"] is None
    r = await client.post(f"{SUP}/{gone['id']}/remove", headers=h,
                          json={"confirm_consent": True, "justification": why})  # fmt: skip
    assert r.status_code == 409
    entries = await audit(client, w, "campaigns")
    removed = [e for e in entries if e["action"] == "suppression_removed"]
    assert len(removed) == 2
    text = json.dumps(entries)
    assert "shop.test" not in text  # no address in the audit log, even in a typed reason
    assert "(address)" in text


async def test_a_campaign_goes_once_to_each_mailable_unsuppressed_customer(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    h = w["h"]
    # Left out: a chat customer's placeholder, a role address, a support address of
    # this workspace, and Bo (suppressed).
    await customer(client, w, "Chat", "chat-123@cevanos.invalid")
    await customer(client, w, "Post", "postmaster@shop.test")
    await customer(client, w, "Us", w["address"]["address"])
    ok(await client.post(SUP, headers=h, json={"email": "bo@shop.test"}), 201)
    c = await draft(client, w, track_opens=True)
    aud = ok(await client.get(f"{K}/{c['id']}/audience", headers=h))
    assert (aud["in_segment"], aud["not_mailable"], aud["suppressed"], aud["recipients"]) == (
        6, 3, 1, 2,
    )  # fmt: skip
    # Preview: Ana is the first; a sample when asked for nobody in particular.
    pre = ok(await client.post(f"{K}/{c['id']}/preview", headers=h, json={}))
    assert pre["contact_name"] == "Ana Silva" and pre["subject"] == "Hi Ana, our news"
    assert '<strong>New</strong> shoes: <a href="https://shop.test/new">see them</a>' in pre["html"]
    assert "Unsubscribe" in pre["text"] and "12 MG Road" in pre["html"]
    # A test goes to the Admin only, through the normal queue, with the HTML part.
    sent_to = ok(await client.post(f"{K}/{c['id']}/test", headers=h), 202)
    assert sent_to == {"sent_to": "admin@example.com"}
    [queued] = await sql(sm, w["ws"], "SELECT to_addresses, subject, body_html FROM hd_email_out "
                         "WHERE subject LIKE '[Test]%'")  # fmt: skip
    assert queued[0] == ["admin@example.com"] and queued[1] == "[Test] Hi Admin, our news"
    assert "<strong>New</strong>" in queued[2]

    started = ok(await client.post(f"{K}/{c['id']}/send", headers=h, json={"confirm": True}))
    assert started["state"] == "sending" and started["stats"]["recipients"] == 2
    assert (await client.patch(f"{K}/{c['id']}", headers=h, json={"name": "x"})).status_code == 409
    assert (await client.delete(f"{K}/{c['id']}", headers=h)).status_code == 409
    # A segment change after the start changes nothing: the recipients were copied.
    await customer(client, w, "Late", "late@shop.test")
    mailer = FakeMailer(w["domain"])
    result = await campaigns.run_due(sm, mailer, CONFIG)
    assert mine(mailer) == ["ana@shop.test", "cy@shop.test"] and result.finished >= 1
    email = next(e for e in mailer.sent if e.to == ["ana@shop.test"])
    headers = dict(email.headers)
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert headers["List-Unsubscribe"].startswith("<https://api.test/api/v1/public/unsubscribe/")
    assert email.subject == "Hi Ana, our news" and email.from_name == "Acme News"
    assert email.html is not None and "https://api.test/api/v1/public/open/" in email.html
    assert "https://portal.test/unsubscribe#token=" in email.text
    assert "ana@shop.test" not in headers["List-Unsubscribe"] + email.html  # never the address
    # Run again: nothing more is sent.
    again = FakeMailer(w["domain"])
    await campaigns.run_due(sm, again, CONFIG)
    assert again.sent == []
    done = ok(await client.get(f"{K}/{c['id']}", headers=h))
    assert done["state"] == "sent" and done["finished_at"] is not None
    assert done["segment_name"] == "Shop" and done["from_address"] == w["address"]["address"]
    stats = ok(await client.get(f"{K}/{c['id']}/stats", headers=h))
    assert (stats["recipients"], stats["sent"], stats["queued"]) == (2, 2, 0)
    # The audit log: counts, never addresses or the email's text.
    entries = await audit(client, w, "campaigns")
    actions = {e["action"] for e in entries}
    assert {"created", "test_sent", "sent", "finished"} <= actions
    text = json.dumps(entries)
    assert "shop.test" not in text and "New** shoes" not in text and "our news" not in text


async def test_a_recipient_is_never_sent_twice(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    # A worker claimed Ana and stopped before saying how it went.
    await sql(
        sm,
        w["ws"],
        "UPDATE hd_campaign_recipient SET status = 'sending', "
        "claimed_until = now() - interval '1 minute' WHERE email = 'ana@shop.test'",
    )
    # The mail server is down for Bo: tried again later; for Cy it refuses for good.
    down = FakeMailer(w["domain"], SendError("Could not reach the mail server."))
    await campaigns.run_due(sm, down, CONFIG)
    rows = dict(await sql(sm, w["ws"], "SELECT email, status FROM hd_campaign_recipient"))
    assert rows == {"ana@shop.test": "failed", "bo@shop.test": "queued", "cy@shop.test": "queued"}
    [(error,)] = await sql(sm, w["ws"], "SELECT error FROM hd_campaign_recipient "
                           "WHERE email = 'ana@shop.test'")  # fmt: skip
    assert "not sent again" in error
    await sql(sm, w["ws"], "UPDATE hd_campaign_recipient SET next_attempt_at = now() "
              "WHERE status = 'queued'")  # fmt: skip
    refused = FakeMailer(w["domain"], SendError("refused", permanent=True))
    await campaigns.run_due(sm, refused, CONFIG)
    rows = dict(await sql(sm, w["ws"], "SELECT email, status FROM hd_campaign_recipient"))
    assert set(rows.values()) == {"failed"}
    assert ok(await client.get(f"{K}/{c['id']}", headers=w["h"]))["state"] == "sent"
    failed = ok(await client.get(f"{K}/{c['id']}/recipients", headers=w["h"],
                                 params={"status": "failed"}))  # fmt: skip
    assert len(failed["recipients"]) == 3


async def test_limits_per_minute_per_day_and_per_campaign(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    slow = Config(Limits(max_recipients=100, daily_cap=1000, per_minute=1), CONFIG.links, False)
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, slow)
    await campaigns.run_due(sm, mailer, slow)  # the same minute: nothing more
    assert len(mailer.sent) == 1
    later = datetime.now(UTC) + timedelta(minutes=2)
    await campaigns.run_due(sm, mailer, slow, now=later)
    assert len(mailer.sent) == 2
    # Over the caps: refused before anything is copied.
    app = create_app(Settings(sign_in_limit=100_000, campaign_max_recipients=2,
                              campaign_daily_cap=4))  # fmt: skip
    app.state.sessionmaker = sm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as small:
        h = bearer(await sign_in(small, w["slug"], "admin@example.com"))
        big = await draft(small, w | {"h": h})
        r = await small.post(f"{K}/{big['id']}/send", headers=h, json={"confirm": True})
        assert r.status_code == 422 and "the most is 2" in r.json()["error"]["message"]
        ok(await small.post(SUP, headers=h, json={"email": "cy@shop.test"}), 201)
        aud = ok(await small.get(f"{K}/{big['id']}/audience", headers=h))
        assert (aud["recipients"], aud["sent_last_24h"], aud["queued"]) == (2, 2, 1)
        assert "daily limit" in aud["problem"]
        r = await small.post(f"{K}/{big['id']}/send", headers=h, json={"confirm": True})
        assert r.status_code == 422 and "daily limit" in r.json()["error"]["message"]
        assert ok(await small.get(f"{K}/{big['id']}", headers=h))["state"] == "draft"


async def test_schedule_cancel_pause_and_resume(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    h = w["h"]
    c = await draft(client, w)
    at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    s = ok(await client.post(f"{K}/{c['id']}/send", headers=h,
                             json={"confirm": True, "scheduled_at": at}))  # fmt: skip
    assert s["state"] == "scheduled" and s["stats"]["recipients"] == 0
    far = (datetime.now(UTC) + timedelta(days=100)).isoformat()
    other = await draft(client, w)
    r = await client.post(f"{K}/{other['id']}/send", headers=h,
                          json={"confirm": True, "scheduled_at": far})  # fmt: skip
    assert r.status_code == 422
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    assert mailer.sent == []  # not yet
    # Cancel a scheduled one: a draft again.
    back = ok(await client.post(f"{K}/{c['id']}/cancel", headers=h))
    assert back["state"] == "draft" and back["scheduled_at"] is None
    ok(await client.post(f"{K}/{c['id']}/send", headers=h,
                         json={"confirm": True, "scheduled_at": at}))  # fmt: skip
    # Its time comes: started and sent by the worker.
    await campaigns.run_due(sm, mailer, CONFIG, now=datetime.now(UTC) + timedelta(hours=3))
    assert len(mailer.sent) == 3
    assert ok(await client.get(f"{K}/{c['id']}", headers=h))["state"] == "sent"
    # A scheduled one whose address lost its check when its time came: failed, not sent.
    ok(await client.post(f"{K}/{other['id']}/send", headers=h,
                         json={"confirm": True, "scheduled_at": at}))  # fmt: skip
    await sql(sm, w["ws"], "UPDATE hd_email_address SET forwarding_verified_at = NULL")
    await campaigns.run_due(sm, mailer, CONFIG, now=datetime.now(UTC) + timedelta(hours=3))
    failed = ok(await client.get(f"{K}/{other['id']}", headers=h))
    assert failed["state"] == "failed" and "Verify" in failed["last_error"]
    assert len(mailer.sent) == 3
    # Pause and resume a sending one; cancel stops the rest.
    await sql(sm, w["ws"], "UPDATE hd_email_address SET forwarding_verified_at = now()")
    third = await draft(client, w)
    ok(await client.post(f"{K}/{third['id']}/send", headers=h, json={"confirm": True}))
    assert ok(await client.post(f"{K}/{third['id']}/pause", headers=h))["state"] == "paused"
    await campaigns.run_due(sm, mailer, CONFIG)
    assert len(mailer.sent) == 3  # paused: nothing goes
    assert (await client.post(f"{K}/{third['id']}/pause", headers=h)).status_code == 409
    assert ok(await client.post(f"{K}/{third['id']}/resume", headers=h))["state"] == "sending"
    stopped = ok(await client.post(f"{K}/{third['id']}/cancel", headers=h))
    assert stopped["state"] == "cancelled" and stopped["stats"]["cancelled"] == 3
    assert (await client.post(f"{K}/{third['id']}/cancel", headers=h)).status_code == 409
    actions = [e["action"] for e in await audit(client, w, "campaigns")]
    for action in ("scheduled", "cancelled", "started", "failed", "paused", "resumed"):
        assert action in actions


async def test_unsubscribe_by_page_and_one_click(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    by = {e.to[0]: token_of(e) for e in mailer.sent}
    page = ok(await client.get(f"/api/v1/public/unsubscribe/{by['ana@shop.test']}"))
    assert page == {"workspace_name": "Test", "unsubscribed": False}
    # The page's button (JSON) and the mail app's one-click (a form).
    done = ok(await client.post(f"/api/v1/public/unsubscribe/{by['ana@shop.test']}"))
    assert done["unsubscribed"] is True
    ok(await client.post(f"/api/v1/public/unsubscribe/{by['ana@shop.test']}"))  # again: same
    r = await client.post(f"/api/v1/public/unsubscribe/{by['bo@shop.test']}",
                          data={"List-Unsubscribe": "One-Click"})  # fmt: skip
    assert r.status_code == 200
    # An unknown link gets the same answer and reveals nothing.
    unknown = f"{w['ws']}.not-a-real-token-at-all"
    assert ok(await client.post(f"/api/v1/public/unsubscribe/{unknown}")) == done
    assert ok(await client.get(f"/api/v1/public/unsubscribe/{unknown}")) == {
        "workspace_name": None, "unsubscribed": False,
    }  # fmt: skip
    assert ok(await client.get(f"/api/v1/public/unsubscribe/{by['ana@shop.test']}"))["unsubscribed"]
    entries = ok(await client.get(SUP, headers=w["h"]))["entries"]
    assert sorted((e["email"], e["reason"], e["source"], e["campaign_name"]) for e in entries) == [
        ("ana@shop.test", "unsubscribed", "link", "November news"),
        ("bo@shop.test", "unsubscribed", "one_click", "November news"),
    ]  # fmt: skip
    stats = ok(await client.get(f"{K}/{c['id']}/stats", headers=w["h"]))
    assert stats["unsubscribed"] == 2
    unsubbed = ok(await client.get(f"{K}/{c['id']}/recipients", headers=w["h"],
                                   params={"status": "unsubscribed"}))  # fmt: skip
    assert sorted(r["email"] for r in unsubbed["recipients"]) == ["ana@shop.test", "bo@shop.test"]
    # The next campaign leaves them out; a ticket reply still reaches Ana (transactional).
    nxt = await draft(client, w)
    aud = ok(await client.get(f"{K}/{nxt['id']}/audience", headers=w["h"]))
    assert (aud["suppressed"], aud["recipients"]) == (2, 1)
    body = {
        "subject": "Refund",
        "channel": "email",
        "contact": {"email": "ana@shop.test", "name": "Ana"},
    }
    ticket_id = ok(await client.post("/api/v1/tickets", headers=w["h"], json=body), 201)["id"]
    ok(await client.post(f"/api/v1/tickets/{ticket_id}/messages", headers=w["h"],
                         json={"kind": "reply", "body": "Your refund is done."}), 201)  # fmt: skip
    rows = await sql(sm, w["ws"], "SELECT to_addresses FROM hd_email_out WHERE ticket_id = :t",
                     t=ticket_id)  # fmt: skip
    assert rows == [(["ana@shop.test"],)]


async def test_an_unsubscribe_replaces_an_earlier_manual_entry(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Ana was already on the list as `manual` when she unsubscribed. Her unsubscribe was
    dropped (the first entry stayed), so an Admin could remove her without her consent and
    mail her again. Now it becomes an unsubscribe; an Admin's entry never weakens one."""
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    by = {e.to[0]: token_of(e) for e in mailer.sent}
    manual = ok(await client.post(SUP, headers=w["h"], json={"email": "ana@shop.test"}), 201)
    ok(await client.post(f"/api/v1/public/unsubscribe/{by['ana@shop.test']}"))
    ana = {"email": "ana@shop.test"}
    [entry] = ok(await client.get(SUP, headers=w["h"], params=ana))["entries"]
    assert (entry["id"], entry["reason"]) == (manual["id"], "unsubscribed")
    assert entry["source"] == "link"
    r = await client.post(f"{SUP}/{entry['id']}/remove", headers=w["h"], json={})
    assert r.status_code == 409  # her consent is needed now
    # An Admin adding her again changes nothing: still her unsubscribe.
    assert (await client.post(SUP, headers=w["h"], json=ana)).status_code == 409
    [still] = ok(await client.get(SUP, headers=w["h"], params=ana))["entries"]
    assert still["reason"] == "unsubscribed"


async def test_opens_are_counted_when_tracked(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    tracked = await draft(client, w, track_opens=True)
    plain = await draft(client, w)
    for c in (tracked, plain):
        ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    with_pixel = [e for e in mailer.sent if e.html and "/public/open/" in e.html]
    assert len(with_pixel) == 3  # only the tracked campaign's emails have the image
    found = re.search(r"/public/open/([^\"]+)\"", with_pixel[0].html or "")
    assert found is not None
    for _ in range(3):
        r = await client.get(f"/api/v1/public/open/{found.group(1)}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
        assert r.headers["cache-control"].startswith("no-store")
    junk = await client.get("/api/v1/public/open/whatever-it-is")
    assert junk.status_code == 200 and junk.content == r.content
    stats = ok(await client.get(f"{K}/{tracked['id']}/stats", headers=w["h"]))
    assert (stats["opened"], stats["opens"]) == (1, 3)
    opened = ok(await client.get(f"{K}/{tracked['id']}/recipients", headers=w["h"],
                                 params={"status": "opened"}))  # fmt: skip
    [one] = opened["recipients"]
    assert one["open_count"] == 3 and one["first_opened_at"] is not None


def dsn(to: str, about: str, original_id: str, *, status: str = "5.1.1") -> bytes:
    return (
        f"From: Mail Delivery System <MAILER-DAEMON@mx.test>\r\nTo: {to}\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\nMessage-ID: <dsn-1@mx.test>\r\n"
        "MIME-Version: 1.0\r\nAuto-Submitted: auto-replied\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain\r\n\r\nYour message could not be delivered.\r\n"
        "--B\r\nContent-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; mx.test\r\n\r\n"
        f"Final-Recipient: rfc822; {about}\r\nAction: failed\r\nStatus: {status}\r\n\r\n"
        "--B\r\nContent-Type: text/rfc822-headers\r\n\r\n"
        f"From: news@x.test\r\nTo: {about}\r\nMessage-ID: {original_id}\r\n\r\n--B--\r\n"
    ).encode()


def arf(to: str, about: str, original_id: str) -> bytes:
    return (
        f"From: feedback@isp.test\r\nTo: {to}\r\nSubject: Complaint\r\n"
        "Message-ID: <arf-1@isp.test>\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=feedback-report; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain\r\n\r\nA complaint.\r\n"
        "--B\r\nContent-Type: message/feedback-report\r\n\r\n"
        f"Feedback-Type: abuse\r\nVersion: 1\r\nOriginal-Rcpt-To: {about}\r\n\r\n"
        "--B\r\nContent-Type: text/rfc822-headers\r\n\r\n"
        f"From: news@x.test\r\nMessage-ID: {original_id}\r\n\r\n--B--\r\n"
    ).encode()


async def test_bounces_and_spam_reports_come_back_through_email_in(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    ids = {e.to[0]: str(e.message_id) for e in mailer.sent}
    support = w["address"]["address"]
    tickets_before = await sql(sm, w["ws"], "SELECT count(*) FROM hd_ticket")
    got = await receive(sm, dsn(support, "ana@shop.test", ids["ana@shop.test"]))
    assert got.outcome is Outcome.bounce
    soft = await receive(sm, dsn(support, "bo@shop.test", ids["bo@shop.test"], status="4.2.2"))
    assert soft.outcome is Outcome.bounce  # recognised, but nothing changes
    spam = await receive(sm, arf(support, "cy@shop.test", ids["cy@shop.test"]))
    assert spam.outcome is Outcome.bounce
    # A bounce about some other email stays an ignored automatic email.
    other = await receive(sm, dsn(support, "zed@shop.test", "<unknown@x.test>"))
    assert other.outcome is Outcome.automatic
    assert await sql(sm, w["ws"], "SELECT count(*) FROM hd_ticket") == tickets_before
    rows = dict(await sql(sm, w["ws"], "SELECT email, status FROM hd_campaign_recipient"))
    assert rows == {"ana@shop.test": "bounced", "bo@shop.test": "sent", "cy@shop.test": "sent"}
    entries = ok(await client.get(SUP, headers=w["h"]))["entries"]
    assert sorted((e["email"], e["reason"], e["source"]) for e in entries) == [
        ("ana@shop.test", "hard_bounce", "bounce"), ("cy@shop.test", "complaint", "complaint"),
    ]  # fmt: skip
    stats = ok(await client.get(f"{K}/{c['id']}/stats", headers=w["h"]))
    assert (stats["sent"], stats["bounced"], stats["complained"]) == (3, 1, 1)
    # A hard bounce can be taken off (a full mailbox); a complaint needs the person's word.
    bounce = next(e for e in entries if e["reason"] == "hard_bounce")
    done(await client.post(f"{SUP}/{bounce['id']}/remove", headers=w["h"], json={}))


async def test_results_csv_and_paging(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    await customer(client, w, "=HYPERLINK(1)", "dee@shop.test")
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    await campaigns.run_due(sm, FakeMailer(w["domain"]), CONFIG)
    first = ok(await client.get(f"{K}/{c['id']}/recipients", headers=w["h"],
                                params={"limit": 3}))  # fmt: skip
    rest = ok(await client.get(f"{K}/{c['id']}/recipients", headers=w["h"],
                               params={"cursor": first["next_cursor"]}))  # fmt: skip
    emails = [r["email"] for r in first["recipients"] + rest["recipients"]]
    assert emails == ["ana@shop.test", "bo@shop.test", "cy@shop.test", "dee@shop.test"]
    assert rest["next_cursor"] is None
    bad = await client.get(f"{K}/{c['id']}/recipients", headers=w["h"], params={"status": "x"})
    assert bad.status_code == 422
    r = await client.get(f"{K}/{c['id']}/recipients/export", headers=w["h"])
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert "email,name,status" in body and "'=HYPERLINK(1)" in body
    assert body.count("@shop.test,") == 4
    listed = ok(await client.get(K, headers=w["h"]))
    assert listed["campaigns"][0]["stats"]["sent"] == 4
    assert "results_exported" in [e["action"] for e in await audit(client, w, "campaigns")]


async def test_only_admins_and_only_their_workspace(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await camp_desk(client, app_sessionmaker)
    c = await draft(client, w)
    sup = ok(await client.post(SUP, headers=w["h"], json={"email": "bo@shop.test"}), 201)
    cid, sid = c["id"], sup["id"]
    routes = [
        ("GET", K, None), ("POST", K, {"name": "x"}), ("GET", f"{K}/{cid}", None),
        ("PATCH", f"{K}/{cid}", {"name": "y"}), ("DELETE", f"{K}/{cid}", None),
        ("GET", f"{K}/{cid}/audience", None), ("POST", f"{K}/{cid}/preview", {}),
        ("POST", f"{K}/{cid}/test", None), ("POST", f"{K}/{cid}/send", {"confirm": True}),
        ("POST", f"{K}/{cid}/cancel", None), ("POST", f"{K}/{cid}/pause", None),
        ("POST", f"{K}/{cid}/resume", None), ("GET", f"{K}/{cid}/stats", None),
        ("GET", f"{K}/{cid}/recipients", None), ("GET", f"{K}/{cid}/recipients/export", None),
        ("GET", SETTINGS, None), ("PUT", SETTINGS, {"postal_address": POSTAL}),
        ("GET", SUP, None), ("POST", SUP, {"email": "zed@shop.test"}),
        ("POST", f"{SUP}/{sid}/remove", {}),
    ]  # fmt: skip
    for who in ("Sam", "Neha", "Meena"):  # supervisor, agent, light agent
        h = await as_(client, w, who)
        for method, url, body in routes:
            r = await client.request(method, url, headers=h, json=body)
            assert r.status_code == 403, (who, method, url, r.status_code)
    # An API key (even admin level) can't reach campaigns.
    key = ok(await client.post("/api/v1/api-keys", headers=w["h"],
                               json={"name": "Shop", "access": "admin"}), 201)["key"]  # fmt: skip
    r = await client.get(K, headers=bearer(key))
    assert r.status_code == 403
    # Another workspace's Admin sees none of it.
    other = await camp_desk(client, app_sessionmaker)
    oh = other["h"]
    assert ok(await client.get(K, headers=oh))["campaigns"] == []
    assert ok(await client.get(SUP, headers=oh))["entries"] == []
    for method, url, body in [*routes[2:15], ("POST", f"{SUP}/{sid}/remove", {})]:
        r = await client.request(method, url, headers=oh, json=body)
        assert r.status_code == 404, (method, url, r.status_code)
    # Its segment and address can't be used either.
    r = await client.post(K, headers=oh, json={"name": "x", "segment_id": w["segment"]["id"]})
    assert r.status_code == 422
    r = await client.post(K, headers=oh,
                          json={"name": "x", "from_address_id": w["address"]["id"]})  # fmt: skip
    assert r.status_code == 422
    # The same address on both lists is two entries (hashed per workspace).
    ok(await client.post(SUP, headers=oh, json={"email": "bo@shop.test"}), 201)
    assert len(ok(await client.get(SUP, headers=w["h"]))["entries"]) == 1


async def test_a_suppressed_or_erased_recipient_is_skipped_at_send_time(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    ok(await client.post(SUP, headers=w["h"], json={"email": "bo@shop.test"}), 201)
    await sql(sm, w["ws"], "UPDATE hd_contact SET erased_at = now() WHERE id = :c",
              c=w["people"]["Cy"])  # fmt: skip
    mailer = FakeMailer(w["domain"])
    result = await campaigns.run_due(sm, mailer, CONFIG)
    assert mine(mailer) == ["ana@shop.test"] and result.skipped >= 2
    stats = ok(await client.get(f"{K}/{c['id']}/stats", headers=w["h"]))
    assert (stats["sent"], stats["skipped"]) == (1, 2)


async def test_a_customer_merged_while_queued_is_skipped_as_merged_not_erased(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Bo is merged into Ana while the campaign is queued: his email is skipped (Ana gets
    hers), and the reason says merged. It said "The customer was erased."."""
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    people = w["people"]
    ok(await client.post(f"/api/v1/contacts/{people['Ana']}/merge", headers=w["h"],
                         json={"duplicate_id": people["Bo"], "confirm": True}))  # fmt: skip
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    assert sorted(mine(mailer)) == ["ana@shop.test", "cy@shop.test"]
    rows = ok(await client.get(f"{K}/{c['id']}/recipients", headers=w["h"],
                               params={"status": "skipped"}))["recipients"]  # fmt: skip
    assert [(r["email"], r["error"]) for r in rows] == [
        ("bo@shop.test", "The customer was merged into another customer.")]  # fmt: skip


async def test_a_draft_is_edited_and_deleted(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await camp_desk(client, app_sessionmaker)
    c = await draft(client, w)
    patch = {"subject": "New subject", "segment_id": None}
    changed = ok(await client.patch(f"{K}/{c['id']}", headers=w["h"], json=patch))
    assert changed["subject"] == "New subject" and changed["segment_id"] is None
    assert "Pick a segment to send to." in changed["problems"]
    r = await client.patch(f"{K}/{c['id']}", headers=w["h"], json={"name": None})
    assert r.status_code == 422
    r = await client.post(f"{K}/{c['id']}/preview", headers=w["h"],
                          json={"contact_id": str(UUID(int=1))})  # fmt: skip
    assert r.status_code == 404
    pre = ok(await client.post(f"{K}/{c['id']}/preview", headers=w["h"], json={}))
    assert pre["contact_name"] == "Alex Sample" and pre["contact_id"] is None
    # A customer's name can't add a link or markup to the HTML.
    sneaky, _ = await customer(client, w, "[Win](https://evil.test) <b>x</b>", "z@shop.test")
    pre = ok(await client.post(f"{K}/{c['id']}/preview", headers=w["h"],
                               json={"contact_id": sneaky}))  # fmt: skip
    ok(await client.patch(f"{K}/{c['id']}", headers=w["h"],
                          json={"body": "Hi {{customer.name}}"}))  # fmt: skip
    pre = ok(await client.post(f"{K}/{c['id']}/preview", headers=w["h"],
                               json={"contact_id": sneaky}))  # fmt: skip
    assert 'href="https://evil.test"' not in pre["html"] and "<b>x</b>" not in pre["html"]
    assert "&lt;b&gt;x&lt;/b&gt;" in pre["html"]
    done(await client.delete(f"{K}/{c['id']}", headers=w["h"]))
    assert (await client.get(f"{K}/{c['id']}", headers=w["h"])).status_code == 404
    assert "changed" in [e["action"] for e in await audit(client, w, "campaigns")]


async def test_too_many_bounces_pause_the_campaign(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    c = await draft(client, w)
    ok(await client.post(f"{K}/{c['id']}/send", headers=w["h"], json={"confirm": True}))
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    # Pretend 60 were sent and it is still sending.
    await sql(sm, w["ws"], "UPDATE hd_campaign SET state = 'sending' WHERE id = :id", id=c["id"])
    await sql(sm, w["ws"], "INSERT INTO hd_campaign_recipient (workspace_id, campaign_id, email, "
              "name, status, sent_at, bounced_at) SELECT :wid, :id, 'p' || g || '@shop.test', 'P', "
              "CASE WHEN g <= 3 THEN 'bounced' ELSE 'sent' END, now(), "
              "CASE WHEN g <= 3 THEN now() END FROM generate_series(1, 57) g",
              wid=w["ws"], id=c["id"])  # fmt: skip
    ana = next(e for e in mailer.sent if e.to == ["ana@shop.test"])
    await receive(sm, dsn(w["address"]["address"], "ana@shop.test", str(ana.message_id)))
    paused = ok(await client.get(f"{K}/{c['id']}", headers=w["h"]))
    assert paused["state"] == "paused" and "bounced" in paused["last_error"]
