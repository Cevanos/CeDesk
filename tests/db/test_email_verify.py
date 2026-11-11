"""Checking support email addresses (md C10) with RLS on: the forwarding check (a code
emailed to the address verifies it when it comes back in, and never makes a ticket) and
the DNS check (SPF, DKIM, DMARC through a fake DNS)."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.dnscheck import LookupFailed
from cedesk.helpdesk.domain.email_verify import code_in
from cedesk.helpdesk.service import email as email_service
from cedesk.helpdesk.service.email import send_due
from cedesk.helpdesk.service.email_in import Outcome, receive
from cedesk.mail import OutgoingEmail, SendError
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in
from tests.db.test_email_in import email

pytestmark = pytest.mark.db

A = "/api/v1/email-addresses"
TOKEN = "t" * 40


class FakeDns:
    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}
        self.down: set[str] = set()
        self.asked: list[str] = []

    async def txt(self, name: str) -> list[str]:
        self.asked.append(name)
        if name in self.down:
            raise LookupFailed("timed out")
        return self.records.get(name, [])


@pytest.fixture
async def dns() -> FakeDns:
    return FakeDns()


@pytest.fixture
async def client(app_sessionmaker: Sm, dns: FakeDns) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        sign_in_limit=100_000,
        mail_forward_to="in@inbound.cedesk.test",
        mail_spf_include="amazonses.com",
        inbound_email_token=TOKEN,
    )
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    app.state.dns = dns
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    await add(client, token, "neha@example.com", "agent", "Neha")
    h = bearer(token)
    support = f"support@{slug}.test"
    made = ok(await client.post(A, headers=h, json={"address": support, "name": "Acme"}), 201)
    return {"slug": slug, "h": h, "ws": UUID(token.split(".")[0]), "id": made["id"],
            "address": support, "url": f"{A}/{made['id']}",
            "neha": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def queued_subjects(sm: Sm, w: dict[str, Any]) -> list[str]:
    async with workspace_session(sm, w["ws"]) as s:
        rows = await s.execute(text("SELECT subject, from_address, to_addresses FROM hd_email_out "
                                    "ORDER BY created_at"))  # fmt: skip
    found = rows.mappings().all()
    assert all(r["from_address"] == w["address"] == r["to_addresses"][0] for r in found)
    return [str(r["subject"]) for r in found]


async def tickets(sm: Sm, w: dict[str, Any]) -> int:
    async with workspace_session(sm, w["ws"]) as s:
        return int(await s.scalar(text("SELECT count(*) FROM hd_ticket")) or 0)


async def test_the_forwarding_check_verifies_the_address(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    before = ok(await client.get(A, headers=w["h"]))[0]
    assert (before["forwarding_verified"], before["forward_to"], before["dns"]) == (
        False, "in@inbound.cedesk.test", None,
    )  # fmt: skip
    check = f"{w['url']}/forwarding-check"
    assert (await client.post(check, headers=w["neha"])).status_code == 403
    sent = ok(await client.post(check, headers=w["h"]), 202)
    assert sent["forwarding_check_sent_at"] is not None and not sent["forwarding_verified"]
    assert (await client.post(check, headers=w["h"])).status_code == 409  # wait a minute
    [subject] = await queued_subjects(app_sessionmaker, w)
    code = code_in(subject)
    assert code is not None

    # A wrong code: no ticket, not verified.
    wrong = email(to=w["address"], sender=w["address"],
                  subject=f"Fwd: CeDesk forwarding check {'x' * 43}")  # fmt: skip
    assert (await receive(app_sessionmaker, wrong)).outcome is Outcome.verification
    assert ok(await client.get(A, headers=w["h"]))[0]["forwarding_verified"] is False

    # It comes back in (from the address itself, maybe marked automatic by a forwarder).
    back = email(to=w["address"], sender=f"Acme <{w['address']}>", subject=f"Fwd: {subject}",
                 extra={"Auto-Submitted": "auto-forwarded"})  # fmt: skip
    # Through the mail service's webhook, as in production.
    got = ok(await client.post("/api/v1/inbound/email", content=back, headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "message/rfc822"}))  # fmt: skip
    assert got == {"outcome": "verification", "ticket_id": None}
    after = ok(await client.get(A, headers=w["h"]))[0]
    assert after["forwarding_verified"] is True and after["forwarding_verified_at"]
    assert await tickets(app_sessionmaker, w) == 0  # never a ticket
    # Delivered again: the code is used up; still no ticket.
    assert (await receive(app_sessionmaker, back)).outcome is Outcome.verification
    assert await tickets(app_sessionmaker, w) == 0


async def test_a_code_only_works_for_its_own_workspace_and_for_7_days(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    ok(await client.post(f"{w['url']}/forwarding-check", headers=w["h"]), 202)
    [subject] = await queued_subjects(app_sessionmaker, w)
    # Sent to another workspace's address: that workspace has no such code.
    await receive(app_sessionmaker, email(to=other["address"], subject=subject))
    assert ok(await client.get(A, headers=w["h"]))[0]["forwarding_verified"] is False
    assert ok(await client.get(A, headers=other["h"]))[0]["forwarding_verified"] is False
    # Eight days later: too old.
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_email_address SET verify_sent_at = "
                             "now() - interval '8 days'"))  # fmt: skip
    await receive(app_sessionmaker, email(to=w["address"], subject=subject))
    assert ok(await client.get(A, headers=w["h"]))[0]["forwarding_verified"] is False


async def test_the_dns_check(client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns) -> None:
    w = await desk(client, app_sessionmaker)
    domain = w["address"].split("@")[1]
    url = f"{w['url']}/dns-check"
    assert (await client.post(url, headers=w["neha"])).status_code == 403

    # Nothing published yet; no DKIM selector given.
    found = ok(await client.post(url, headers=w["h"]))["dns"]
    assert found["domain"] == domain
    assert [found[k]["status"] for k in ("spf", "dkim", "dmarc")] == [
        "missing", "not_checked", "missing",
    ]  # fmt: skip
    assert dns.asked == [domain, f"_dmarc.{domain}"]  # no selector: DKIM not looked up

    # Published, with the server's mail service in SPF and a DKIM key at s1.
    dns.records = {
        domain: ["v=spf1 include:amazonses.com ~all"],
        f"s1._domainkey.{domain}": ["v=DKIM1; k=rsa; p=MIGfMA0G"],
        f"_dmarc.{domain}": ["v=DMARC1; p=quarantine"],
    }
    bad = await client.patch(w["url"], headers=w["h"], json={"dkim_selector": "bad selector"})
    assert bad.status_code == 422
    ok(await client.patch(w["url"], headers=w["h"], json={"dkim_selector": " S1 "}))
    found = ok(await client.post(url, headers=w["h"]))
    assert found["dkim_selector"] == "s1"
    assert [found["dns"][k]["status"] for k in ("spf", "dkim", "dmarc")] == ["ok"] * 3
    assert found["dns"]["spf"]["record"] == "v=spf1 include:amazonses.com ~all"
    # Kept: the list shows the last check.
    assert ok(await client.get(A, headers=w["h"]))[0]["dns"]["dkim"]["status"] == "ok"

    # SPF without the mail service; DNS down for DMARC.
    dns.records[domain] = ["v=spf1 include:_spf.google.com ~all"]
    dns.down = {f"_dmarc.{domain}"}
    found = ok(await client.post(url, headers=w["h"]))["dns"]
    assert (found["spf"]["status"], found["spf"]["note"]) == (
        "wrong", "Add include:amazonses.com to it.",
    )  # fmt: skip
    assert found["dmarc"]["status"] == "error" and found["dkim"]["status"] == "ok"
    # The mail service one level down (redirect, then include) counts.
    dns.records[domain] = ["v=spf1 redirect=_spf.acme.test"]
    dns.records["_spf.acme.test"] = ["v=spf1 include:amazonses.com include:_spf.acme.test -all"]
    dns.down = set()
    found = ok(await client.post(url, headers=w["h"]))["dns"]
    assert found["spf"]["status"] == "ok"
    # Clearing the selector.
    cleared = ok(await client.patch(w["url"], headers=w["h"], json={"dkim_selector": ""}))
    assert cleared["dkim_selector"] is None


async def test_the_dns_check_stops_when_its_time_is_up(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No lookup starts once the check has used its time: those parts say "try again"."""
    w = await desk(client, app_sessionmaker)
    monkeypatch.setattr(email_service, "DNS_BUDGET_SECONDS", -1)
    found = ok(await client.post(f"{w['url']}/dns-check", headers=w["h"]))["dns"]
    assert [found[k]["status"] for k in ("spf", "dmarc")] == ["error", "error"]
    assert dns.asked == []


class RefusingMailer:
    """A mail service that won't send from a domain it hasn't verified."""

    async def send(self, email: OutgoingEmail) -> None:
        raise SendError("554 Email address is not verified.", permanent=True)


async def test_a_check_email_that_could_not_be_sent_shows_why(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sent = ok(await client.post(f"{w['url']}/forwarding-check", headers=w["h"]), 202)
    assert (sent["forwarding_check_email"], sent["forwarding_check_error"]) == ("pending", None)
    await send_due(app_sessionmaker, RefusingMailer())  # the worker's pass
    now = ok(await client.get(A, headers=w["h"]))[0]
    assert (now["forwarding_check_email"], now["forwarding_check_error"]) == (
        "failed", "554 Email address is not verified.",
    )  # fmt: skip
    assert now["forwarding_verified"] is False
