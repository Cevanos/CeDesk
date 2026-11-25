"""Help center custom domain (feature-gap plan 4.2) with RLS on: an Admin registers a domain
and proves it with a TXT record; only a verified domain is looked up or allowed a certificate;
the global directory is written by a trigger, never by the app; a domain nobody can still prove
goes back to pending."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cedesk.dnscheck import LookupFailed
from cedesk.helpdesk.domain import help_domain as rules
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.service import help_domain as service
from cedesk.main import create_app
from cedesk.ratelimit import SlidingWindowLimiter
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in
from tests.db.report_helpers import sql

pytestmark = pytest.mark.db

D = "/api/v1/help-center/domain"
KEY = "k" * 32


class FakeDns:
    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}
        self.down: set[str] = set()
        self.boom: set[str] = set()
        self.before_answer: Callable[[], Awaitable[None]] | None = None

    async def txt(self, name: str) -> list[str]:
        if self.before_answer is not None:
            hook, self.before_answer = self.before_answer, None
            await hook()
        if name in self.boom:
            raise RuntimeError("resolver blew up")
        if name in self.down:
            raise LookupFailed("timed out")
        return self.records.get(name, [])


def build(sm: Sm, dns: FakeDns, **settings: Any) -> FastAPI:
    app = create_app(Settings(**{"sign_in_limit": 100_000, **settings}))
    app.state.sessionmaker = sm
    app.state.dns = dns
    return app


@pytest.fixture
async def dns() -> FakeDns:
    return FakeDns()


@pytest.fixture(autouse=True)
async def no_domains(owner_engine: Any) -> None:
    """Every test starts with no domains anywhere: a verified domain is unique across all
    workspaces, and the tests below all use the same names. (TRUNCATE is not subject to
    row-level security and fires no triggers, so both tables are emptied together.)"""
    async with owner_engine.begin() as conn:
        await conn.execute(text("TRUNCATE hd_help_domain, help_domain_directory"))


@pytest.fixture
async def client(app_sessionmaker: Sm, dns: FakeDns) -> AsyncIterator[AsyncClient]:
    app = build(app_sessionmaker, dns, help_cname_target="help.cedesk.test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    await add(client, admin, "neha@example.com", "agent", "Neha")
    async with sm() as s, s.begin():
        ws = await auth_repo.workspace_for_slug(s, slug)
    assert ws is not None
    return {"slug": slug, "ws": ws, "admin": bearer(admin),
            "neha": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


def prove(dns: FakeDns, domain: str, token_from: dict[str, Any]) -> None:
    """Publishes the workspace's token at the domain (a second workspace's token is added to
    the same name, as two owners of one domain would)."""
    name = rules.txt_name(domain)
    dns.records.setdefault(name, []).append(rules.txt_value(_token(token_from)))


def _token(answer: dict[str, Any]) -> str:
    (txt,) = [s for s in answer["dns"] if s["type"] == "TXT"]
    return str(txt["value"]).removeprefix("cedesk-verify=")


async def register(client: AsyncClient, w: dict[str, Any], domain: str) -> dict[str, Any]:
    made: dict[str, Any] = ok(await client.put(D, headers=w["admin"], json={"domain": domain}))
    return made


async def check(client: AsyncClient, w: dict[str, Any]) -> Any:
    return await client.post(f"{D}/check", headers=w["admin"])


async def directory(sm: Sm, w: dict[str, Any]) -> list[tuple[str, UUID]]:
    rows = await sql(sm, w["ws"], "SELECT domain, workspace_id FROM help_domain_directory")
    return [(r["domain"], r["workspace_id"]) for r in rows]


async def test_an_admin_registers_a_domain_and_gets_the_records_to_publish(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    assert ok(await client.get(D, headers=w["admin"])) is None  # none yet
    made = await register(client, w, " Help.Example.COM ")
    assert made["domain"] == "help.example.com" and made["status"] == "pending"
    assert made["verified_at"] is None and made["failed_checks"] == 0
    txt, cname = made["dns"]
    assert txt["name"] == "_cedesk-verify.help.example.com"
    assert txt["value"].startswith("cedesk-verify=") and len(_token(made)) >= 20
    assert cname == {"type": "CNAME", "name": "help.example.com", "value": "help.cedesk.test"}
    assert ok(await client.get(D, headers=w["admin"]))["domain"] == "help.example.com"

    again = await register(client, w, "help.example.com")  # the same domain: nothing changes
    assert _token(again) == _token(made)
    other = await register(client, w, "docs.example.com")  # another: a new token, pending
    assert other["domain"] == "docs.example.com" and _token(other) != _token(made)

    for call in (client.get(D, headers=w["neha"]),
                 client.put(D, headers=w["neha"], json={"domain": "x.example.com"}),
                 client.delete(D, headers=w["neha"]), client.post(f"{D}/check", headers=w["neha"]),
                 client.get(D)):  # fmt: skip
        assert (await call).status_code in (401, 403)
    for bad in ("localhost", "192.168.0.1", "https://help.example.com", "help.example.com:80",
                "*.example.com", "help.example.com/x", ""):  # fmt: skip
        assert (await client.put(D, headers=w["admin"], json={"domain": bad})).status_code == 422


async def test_the_platforms_own_hosts_cannot_be_registered(
    app_sessionmaker: Sm, dns: FakeDns
) -> None:
    app = build(app_sessionmaker, dns, app_url="https://app.cedesk.test",
                portal_url="https://portal.cedesk.test", api_url="https://api.cedesk.test",
                help_cname_target="help.cedesk.test")  # fmt: skip
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        w = await desk(client, app_sessionmaker)
        for host in ("app.cedesk.test", "x.app.cedesk.test", "portal.cedesk.test",
                     "api.cedesk.test", "help.cedesk.test"):  # fmt: skip
            r = await client.put(D, headers=w["admin"], json={"domain": host})
            assert r.status_code == 422 and "platform" in r.json()["error"]["message"], host
        assert (await client.put(D, headers=w["admin"], json={"domain": "cedesk.test"})
                ).status_code == 200  # the parent is not one of its hosts  # fmt: skip


async def test_a_domain_is_verified_by_the_token_in_a_txt_record(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await register(client, w, "help.example.com")
    assert (await check(client, w)).status_code == 200
    miss = ok(await check(client, w))
    assert miss["status"] == "pending" and "_cedesk-verify.help.example.com" in miss["last_error"]
    assert miss["failed_checks"] == 2 and miss["last_checked_at"]
    assert await directory(app_sessionmaker, w) == []

    dns.records["_cedesk-verify.help.example.com"] = ["cedesk-verify=" + "x" * 32]  # not ours
    assert ok(await check(client, w))["status"] == "pending"
    dns.down.add("_cedesk-verify.help.example.com")  # DNS not answering: said, not counted
    before = ok(await client.get(D, headers=w["admin"]))["failed_checks"]
    unknown = ok(await check(client, w))
    assert unknown["status"] == "pending" and "DNS didn't answer" in unknown["last_error"]
    assert unknown["failed_checks"] == before
    dns.down.clear()

    prove(dns, "help.example.com", made)
    good = ok(await check(client, w))
    assert good["status"] == "verified" and good["verified_at"] and good["last_error"] is None
    assert good["failed_checks"] == 0
    assert await directory(app_sessionmaker, w) == [("help.example.com", w["ws"])]
    assert ok(await check(client, w))["status"] == "verified"  # checking again is harmless


async def test_the_public_finds_a_workspace_only_by_a_verified_host(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await register(client, w, "help.example.com")
    host = "/api/v1/public-hosts/"
    assert (await client.get(host + "help.example.com")).status_code == 404  # pending
    prove(dns, "help.example.com", made)
    ok(await check(client, w))
    found = ok(await client.get(host + "help.example.com"))
    assert found == {"workspace": w["slug"], "default_locale": "en"}
    assert ok(await client.get(host + "HELP.Example.com.")) == found  # case and the root dot
    cached = await client.get(host + "help.example.com")
    assert cached.headers["cache-control"] == "public, max-age=60"
    assert "cache-control" not in (await client.get(host + "docs.example.com")).headers
    for other in ("docs.example.com", "example.com", "192.168.0.1", "not_a_host", "x"):
        assert (await client.get(host + other)).status_code == 404, other

    ok(await client.put("/api/v1/help-center/settings", headers=w["admin"],
                        json={"default_locale": "hi"}))  # fmt: skip
    assert ok(await client.get(host + "help.example.com"))["default_locale"] == "hi"
    assert (await client.delete(D, headers=w["admin"])).status_code == 204
    assert (await client.get(host + "help.example.com")).status_code == 404  # gone with it
    assert await directory(app_sessionmaker, w) == []
    assert (await client.delete(D, headers=w["admin"])).status_code == 404
    assert (await check(client, w)).status_code == 404


async def test_changing_the_domain_drops_the_proof_and_the_directory_entry(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    w = await desk(client, app_sessionmaker)
    prove(dns, "help.example.com", await register(client, w, "help.example.com"))
    ok(await check(client, w))
    assert len(await directory(app_sessionmaker, w)) == 1
    moved = await register(client, w, "docs.example.com")
    assert moved["status"] == "pending" and moved["verified_at"] is None
    assert await directory(app_sessionmaker, w) == []
    assert (await client.get("/api/v1/public-hosts/help.example.com")).status_code == 404


async def test_the_first_workspace_to_prove_a_domain_wins_and_others_may_still_claim_it(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    first, second = await desk(client, app_sessionmaker), await desk(client, app_sessionmaker)
    a = await register(client, first, "help.example.com")
    b = await register(client, second, "help.example.com")  # a pending claim blocks no one
    assert _token(a) != _token(b)
    prove(dns, "help.example.com", a)
    prove(dns, "help.example.com", b)  # one DNS name holding both tokens
    assert ok(await check(client, first))["status"] == "verified"
    refused = await check(client, second)
    assert refused.status_code == 409 and "already proven" in refused.json()["error"]["message"]
    mine = ok(await client.get(D, headers=second["admin"]))
    assert mine["status"] == "pending" and "already proven" in mine["last_error"]
    assert await directory(app_sessionmaker, first) == [("help.example.com", first["ws"])]
    found = ok(await client.get("/api/v1/public-hosts/help.example.com"))
    assert found["workspace"] == first["slug"]

    # The owner lets it go: the other can now prove it.
    assert (await client.delete(D, headers=first["admin"])).status_code == 204
    assert ok(await check(client, second))["status"] == "verified"
    assert await directory(app_sessionmaker, second) == [("help.example.com", second["ws"])]


async def test_workspaces_never_see_each_others_domains(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    first, second = await desk(client, app_sessionmaker), await desk(client, app_sessionmaker)
    await register(client, first, "help.example.com")
    assert ok(await client.get(D, headers=second["admin"])) is None
    assert (await client.delete(D, headers=second["admin"])).status_code == 404
    assert (await check(client, second)).status_code == 404
    assert ok(await client.get(D, headers=first["admin"]))["domain"] == "help.example.com"


async def test_a_verified_domain_goes_back_to_pending_after_three_failed_daily_checks(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await register(client, w, "help.example.com")
    prove(dns, "help.example.com", made)
    ok(await check(client, w))
    name = "_cedesk-verify.help.example.com"
    record = [rules.txt_value(_token(made))]
    dns.records[name] = []  # the customer removed the record

    # Pressing Check shows the problem but never counts it: a few clicks while DNS is being
    # moved must not take a domain down.
    for _ in range(5):
        shown = ok(await check(client, w))
        assert shown["status"] == "verified" and shown["failed_checks"] == 0
        assert "_cedesk-verify.help.example.com" in shown["last_error"]
    assert len(await directory(app_sessionmaker, w)) == 1

    day = datetime.now(UTC)

    async def daily() -> dict[str, Any]:
        nonlocal day
        day += timedelta(hours=25)
        await service.recheck_everywhere(app_sessionmaker, dns, now=day)
        return ok(await client.get(D, headers=w["admin"]))  # type: ignore[no-any-return]

    first = await daily()
    assert first["status"] == "verified" and first["failed_checks"] == 1
    dns.records[name] = record  # back: a check proves it and the count starts over
    assert ok(await check(client, w))["failed_checks"] == 0
    dns.records[name] = []
    for expected in (1, 2):
        again = await daily()
        assert again["status"] == "verified" and again["failed_checks"] == expected
    assert len(await directory(app_sessionmaker, w)) == 1  # two missed days: still served
    demoted = await daily()
    assert demoted["status"] == "pending" and demoted["verified_at"] is None
    assert await directory(app_sessionmaker, w) == []
    assert (await client.get("/api/v1/public-hosts/help.example.com")).status_code == 404
    dns.records[name] = record  # and it can be proven again
    assert ok(await check(client, w))["status"] == "verified"
    assert len(await directory(app_sessionmaker, w)) == 1


async def test_a_check_for_a_domain_changed_meanwhile_is_ignored(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    """DNS is read outside any transaction: if the Admin registers another domain while it is
    being read, the old answer must not verify the new one."""
    w = await desk(client, app_sessionmaker)
    made = await register(client, w, "help.example.com")
    prove(dns, "help.example.com", made)

    async def swap() -> None:
        await register(client, w, "docs.example.com")

    dns.before_answer = swap
    answer = ok(await check(client, w))
    assert answer["domain"] == "docs.example.com" and answer["status"] == "pending"
    assert await directory(app_sessionmaker, w) == []


async def test_the_app_cannot_write_the_directory_but_a_trigger_does(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await register(client, w, "help.example.com")
    for statement in (
        "INSERT INTO help_domain_directory (domain, workspace_id) VALUES ('evil.example.com', "
        "'00000000-0000-4000-8000-000000000000')",
        "UPDATE help_domain_directory SET domain = 'evil.example.com'",
        "DELETE FROM help_domain_directory",
        "TRUNCATE help_domain_directory",
    ):
        with pytest.raises(DBAPIError):
            await sql(app_sessionmaker, w["ws"], statement)
    # Even as the workspace itself the app can't mark a domain verified without the unique
    # index and trigger agreeing: a row it verifies appears in the directory, nothing else does.
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_help_domain SET status = 'verified', "
              "verified_at = now()")  # fmt: skip
    assert await directory(app_sessionmaker, w) == [("help.example.com", w["ws"])]


async def test_the_daily_pass_rechecks_verified_domains_and_isolates_failures(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    gone, kept, broken = [await desk(client, app_sessionmaker) for _ in range(3)]
    for w, host in ((gone, "gone.example.com"), (kept, "kept.example.com"),
                    (broken, "broken.example.com")):  # fmt: skip
        prove(dns, host, await register(client, w, host))
        ok(await check(client, w))
    dns.records["_cedesk-verify.gone.example.com"] = []  # the customer let it go
    dns.boom.add("_cedesk-verify.broken.example.com")  # a resolver bug on one domain

    sm = app_sessionmaker
    assert await service.recheck_everywhere(sm, dns) == 0  # all were checked a moment ago
    day = datetime.now(UTC)
    for n, expected_status in enumerate(("verified", "verified", "pending"), start=1):
        day += timedelta(hours=25)
        checked = await service.recheck_everywhere(sm, dns, now=day)
        assert checked == 2  # the broken one raised and was skipped; the pass went on
        row = ok(await client.get(D, headers=gone["admin"]))
        assert row["status"] == expected_status and row["failed_checks"] == n
    assert ok(await client.get(D, headers=kept["admin"]))["status"] == "verified"
    assert ok(await client.get(D, headers=broken["admin"]))["status"] == "verified"
    served = {d for d, _ in await directory(sm, kept)}
    assert served == {"kept.example.com", "broken.example.com"}  # the lapsed one is not served

    # Out of time budget: nothing new is started, the rest wait for the next pass.
    day += timedelta(hours=25)
    assert await service.recheck_everywhere(sm, dns, now=day, budget_seconds=-1) == 0
    assert await service.recheck_everywhere(sm, dns, now=day) == 1  # kept (broken still raises)


async def test_the_tls_check_answers_only_for_a_verified_domain_and_only_with_the_key(
    app_sessionmaker: Sm, dns: FakeDns
) -> None:
    app = build(app_sessionmaker, dns, tls_ask_key=KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        w = await desk(client, app_sessionmaker)
        made = await register(client, w, "help.example.com")
        ask = "/api/v1/internal/tls-ask"
        pending = await client.get(ask, params={"domain": "help.example.com", "key": KEY})
        assert pending.status_code == 404  # not proven yet
        prove(dns, "help.example.com", made)
        ok(await check(client, w))
        yes = await client.get(ask, params={"domain": "Help.Example.com", "key": KEY})
        assert yes.status_code == 200
        for domain in ("other.example.com", "", "192.168.0.1", "example.com"):
            assert (await client.get(ask, params={"domain": domain, "key": KEY})).status_code == 404
        assert "tls-ask" not in str((await client.get("/openapi.json")).json()["paths"])

        # Wrong keys count against the caller's own budget (here 3 are allowed), so the key
        # can't be guessed; the right key is never counted, and the sign-in budget (here
        # just one try) is never touched.
        app.state.tls_attempts = SlidingWindowLimiter(3, 300)
        app.state.attempts = SlidingWindowLimiter(1, 300)
        wrong = {"domain": "help.example.com", "key": "w" * 32}
        assert (await client.get(ask, params=wrong)).status_code == 404
        assert (await client.get(ask, params={"domain": "help.example.com"})).status_code == 404
        assert (await client.get(ask, params=wrong)).status_code == 404
        assert (await client.get(ask, params=wrong)).status_code == 429
        assert (await client.get(ask, params={"domain": "help.example.com", "key": KEY})
                ).status_code == 200  # the right key is never counted  # fmt: skip
        await sign_in(client, w["slug"], "admin@example.com")  # still allowed: its own budget


async def test_without_a_key_there_is_no_tls_check_at_all(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    w = await desk(client, app_sessionmaker)
    prove(dns, "help.example.com", await register(client, w, "help.example.com"))
    ok(await check(client, w))
    for key in ("", KEY):
        r = await client.get("/api/v1/internal/tls-ask",
                             params={"domain": "help.example.com", "key": key})  # fmt: skip
        assert r.status_code == 404


async def test_domain_changes_are_in_the_audit_log(
    client: AsyncClient, app_sessionmaker: Sm, dns: FakeDns
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await register(client, w, "help.example.com")
    prove(dns, "help.example.com", made)
    ok(await check(client, w))
    dns.records["_cedesk-verify.help.example.com"] = []
    day = datetime.now(UTC)
    for _ in range(3):  # three missed days
        day += timedelta(hours=25)
        await service.recheck_everywhere(app_sessionmaker, dns, now=day)
    await register(client, w, "docs.example.com")
    assert (await client.delete(D, headers=w["admin"])).status_code == 204
    page = ok(await client.get("/api/v1/audit", headers=w["admin"],
                               params={"area": "help_domain", "limit": 200}))  # fmt: skip
    actions = [e["action"] for e in reversed(page["entries"])]
    assert actions == ["created", "verified", "demoted", "changed", "deleted"]
    assert _token(made) not in str(page)  # the token is never written to the log
