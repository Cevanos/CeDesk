"""Self-service sign-up without a database: the rules, the settings checks, the routes
when sign-up is off, the CAPTCHA check and the worker's sign-up lane (with fakes)."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from cedesk.captcha import VERIFY_URL, Turnstile
from cedesk.helpdesk.domain import signup as rules
from cedesk.helpdesk.service import signup as signup_service
from cedesk.helpdesk.service import signup_mail
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.mail import OutgoingEmail, SendError
from cedesk.main import create_app
from cedesk.settings import Edition, Env, MailBackend, Settings

ROOT = Path(__file__).resolve().parents[2]
NO_DB = "postgresql+asyncpg://x:y@127.0.0.1:1/none"  # never reached by these tests
PROD = {
    "env": Env.production,
    "secret_key": SecretStr("k" * 40),
    "database_url": "postgresql+asyncpg://cedesk_app:real@db:5432/cedesk",
}


# Rules


@pytest.mark.parametrize(
    ("slug", "ok"),
    [
        ("acme", True),
        ("acme-support", True),
        ("a1b", True),
        ("ab", False),  # too short
        ("-acme", False),
        ("acme-", False),
        ("Acme", False),  # cleaned before, so upper case here is refused
        ("ac--me", False),
        ("x" * 41, False),
        ("admin", False),  # reserved
        ("settings", False),  # an agent app page
        ("signup", False),
    ],
)
def test_slug_problem(slug: str, ok: bool) -> None:
    assert (rules.slug_problem(slug) is None) is ok


def test_reserved_names_cover_every_agent_app_page() -> None:
    """The frontend's AGENT_AREAS (src/lib/public-routes.ts) can never be a workspace."""
    agent_areas = {
        "account", "agents", "api", "archive", "customers", "dev", "forgot-password",
        "login", "rate", "reports", "reset-password", "settings", "sso", "tickets",
    }  # fmt: skip
    assert agent_areas <= rules.RESERVED
    assert all(rules.SLUG.match(name) for name in rules.RESERVED)


def test_clean_slug_and_email() -> None:
    assert rules.clean_slug("  Acme ") == "acme"
    assert rules.normalise_email(" Priya@Example.COM ") == "priya@example.com"
    assert rules.normalise_email("not-an-email") is None


def test_code_is_six_digits_and_typed_codes_are_cleaned() -> None:
    for _ in range(50):
        assert re.fullmatch(r"\d{6}", rules.new_code())
    assert rules.clean_code("123 456") == "123456"
    assert rules.clean_code("123-456") == "123456"
    assert rules.clean_code("12345") is None
    assert rules.clean_code("abcdef") is None


def test_code_hash_is_bound_to_the_signup_the_email_and_the_key() -> None:
    a, b = uuid4(), uuid4()
    base = rules.code_hash("key", a, "p@example.com", "123456")
    assert len(base) == 32
    assert base == rules.code_hash("key", a, "p@example.com", "123456")
    assert base != rules.code_hash("key", b, "p@example.com", "123456")
    assert base != rules.code_hash("key", a, "q@example.com", "123456")
    assert base != rules.code_hash("other", a, "p@example.com", "123456")
    assert base != rules.code_hash("key", a, "p@example.com", "123457")


def test_ip_hash_hides_the_address() -> None:
    assert rules.ip_hash("key", None) is None
    assert rules.ip_hash("key", "") is None
    hashed = rules.ip_hash("key", "203.0.113.9")
    assert hashed is not None and len(hashed) == 32
    assert b"203.0.113.9" not in hashed
    assert hashed != rules.ip_hash("key", "203.0.113.10")


def test_mail_retries_end_well_within_the_code_lifetime() -> None:
    waits = [rules.mail_retry_wait(n) for n in range(1, 6)]
    assert waits == [5, 20, 60, None, None]
    assert sum(w for w in waits if w) < rules.CODE_LIFETIME.total_seconds()


def test_code_email_holds_the_code_and_says_it_expires() -> None:
    body = rules.code_email("123456")
    assert "123456" in body
    assert "10 minutes" in body
    assert "123456" not in rules.SUBJECT


def test_code_email_has_nothing_typed_on_the_form() -> None:
    """The form can name a stranger's email: what it says must not reach their inbox."""
    body = rules.code_email("123456")
    assert body.startswith("Hello,\n")
    assert "Hi " not in body


@pytest.mark.parametrize(
    "name",
    [
        "Priya", "Priya Sharma", "Acme Support", "J.R. Smith & Co.", "O'Brien", "Zoë Müller",
        "Café 24/7", "Team #1 (EU)", "प्रिया", "Acme Inc.", "St. Louis Help",
    ],
)  # fmt: skip
def test_ordinary_names_are_allowed(name: str) -> None:
    assert rules.name_problem(name, "Your name") is None


@pytest.mark.parametrize(
    "name",
    [
        "Sam,\n\nYour account will be closed",  # line breaks: a message of its own
        "Sam\r\nBcc: x@example.com",
        "Sam\u2028Visit us",  # Unicode line separator
        "Sam\u202egnp.exe",  # right-to-left override
        "Sam\u200b",  # zero-width space
        "Visit https://evil.example/login",
        "go to www.evil-site.com now",
        "evil.example/login",
        "Confirm at acme-pay.com",
        "mail help@evil.example",
        "hxxp://x",
    ],
)
def test_names_carrying_a_message_are_refused(name: str) -> None:
    assert rules.name_problem(name, "Your name") is not None


# Settings


def test_signup_off_by_default_and_never_a_problem_when_off() -> None:
    settings = Settings(**PROD)
    assert settings.signup_enabled is False
    assert settings.signup_problems() == []


def test_signup_on_outside_dev_needs_a_sender_and_on_cloud_a_captcha() -> None:
    settings = Settings(**PROD, signup_enabled=True, edition=Edition.cloud)
    problems = " ".join(settings.signup_problems())
    assert "CEDESK_MAIL_FROM_ADDRESS" in problems
    assert "CEDESK_SIGNUP_CAPTCHA_SECRET" in problems

    ready = Settings(
        **PROD,
        signup_enabled=True,
        edition=Edition.cloud,
        mail_from_address="no-reply@example.com",
        signup_captcha_site_key="site",
        signup_captcha_secret=SecretStr("secret"),
    )
    assert ready.signup_problems() == []


def test_self_hosted_may_go_without_a_captcha_but_not_half_of_one() -> None:
    base = {**PROD, "signup_enabled": True, "edition": Edition.self_hosted}
    base["mail_from_address"] = "no-reply@example.com"
    assert Settings(**base).signup_problems() == []
    half = Settings(**base, signup_captcha_site_key="site")
    assert "set both" in " ".join(half.signup_problems())


def test_dev_has_no_signup_problems() -> None:
    assert Settings(signup_enabled=True, edition=Edition.cloud).signup_problems() == []


def test_the_worker_needs_smtp_for_signup_codes() -> None:
    settings = Settings(**PROD, signup_enabled=True, mail_backend=MailBackend.none)
    problems = " ".join(settings.mail_problems())
    assert "sign-up codes need smtp" in problems
    assert "sign-up codes need a sender" in problems
    off = Settings(**PROD, mail_backend=MailBackend.none)
    assert "sign-up" not in " ".join(off.mail_problems())


def test_api_refuses_to_start_with_unsafe_signup_settings() -> None:
    with pytest.raises(RuntimeError, match="CEDESK_SIGNUP_CAPTCHA_SECRET"):
        create_app(
            Settings(
                **PROD,
                signup_enabled=True,
                edition=Edition.cloud,
                mail_from_address="no-reply@example.com",
            )
        )


def test_api_refuses_a_default_plan_that_does_not_exist() -> None:
    with pytest.raises(RuntimeError, match="CEDESK_SIGNUP_DEFAULT_PLAN"):
        create_app(Settings(signup_enabled=True, signup_default_plan="platinum"))
    # Not on the cloud edition, or sign-up off: the plan isn't used, so not checked.
    create_app(
        Settings(signup_enabled=True, signup_default_plan="platinum", edition=Edition.self_hosted)
    )
    create_app(Settings(signup_default_plan="platinum"))


# Routes (no database: sign-up off, or input refused before any query)


def _client(**settings: Any) -> TestClient:
    return TestClient(create_app(Settings(database_url=NO_DB, **settings)))


SIGNUP = {
    "workspace": "acme",
    "workspace_name": "Acme Support",
    "name": "Priya",
    "email": "priya@example.com",
    "password": "correct-horse-battery",
}


def test_signup_off_hides_every_route_but_the_config() -> None:
    with _client() as client:
        r = client.get("/api/v1/auth/signup-config")
        assert r.status_code == 200
        assert r.json() == {"enabled": False, "captcha_site_key": None}
        for path, body in (
            ("/api/v1/auth/signup", SIGNUP),
            ("/api/v1/auth/signup/verify", {"signup_id": str(uuid4()), "code": "123456"}),
            ("/api/v1/auth/signup/resend", {"signup_id": str(uuid4())}),
        ):
            r = client.post(path, json=body)
            assert r.status_code == 404, path
            assert r.json()["error"]["code"] == "NotFound"


def test_signup_config_when_on() -> None:
    with _client(
        signup_enabled=True,
        signup_captcha_site_key="site-key",
        signup_captcha_secret=SecretStr("secret"),
    ) as client:
        r = client.get("/api/v1/auth/signup-config")
    assert r.json() == {"enabled": True, "captcha_site_key": "site-key"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"workspace": "admin"}, "reserved"),
        ({"workspace": "ac--me"}, "two dashes"),
        ({"password": "short"}, "at least"),
        ({"email": "a@b"}, "valid email"),
    ],
)
def test_signup_refuses_bad_input_before_any_database_work(
    change: dict[str, str], message: str
) -> None:
    with _client(signup_enabled=True) as client:
        r = client.post("/api/v1/auth/signup", json={**SIGNUP, **change})
    assert r.status_code == 422, r.text
    assert message in r.json()["error"]["message"]


def test_verify_refuses_a_code_that_is_not_six_digits_before_any_database_work() -> None:
    with _client(signup_enabled=True) as client:
        r = client.post(
            "/api/v1/auth/signup/verify", json={"signup_id": str(uuid4()), "code": "12ab"}
        )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_code"


def test_openapi_file_lists_the_signup_routes() -> None:
    paths = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text("utf-8"))["paths"]
    for path in (
        "/api/v1/auth/signup-config",
        "/api/v1/auth/signup",
        "/api/v1/auth/signup/verify",
        "/api/v1/auth/signup/resend",
    ):
        assert path in paths


# CAPTCHA


async def test_turnstile_passes_only_on_success_and_fails_closed() -> None:
    seen: list[httpx.Request] = []

    def answer(success: bool, status: int = 200) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, json={"success": success})

        return httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=answer(True)) as client:
        assert await Turnstile("secret", client).passed("token", "203.0.113.9")
    sent = seen[-1]
    assert str(sent.url) == VERIFY_URL
    assert b"secret=secret" in sent.content and b"remoteip=203.0.113.9" in sent.content

    async with httpx.AsyncClient(transport=answer(False)) as client:
        assert not await Turnstile("secret", client).passed("token", None)
    async with httpx.AsyncClient(transport=answer(True, 500)) as client:
        assert not await Turnstile("secret", client).passed("token", None)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        assert not await Turnstile("secret", client).passed("token", None)
    # No token: refused without asking Cloudflare.
    count = len(seen)
    async with httpx.AsyncClient(transport=answer(True)) as client:
        assert not await Turnstile("secret", client).passed("", None)
    assert len(seen) == count


# The worker's sign-up lane, with the database calls replaced


class FakeDb:
    """Stands in for the sessionmaker: `sm()` gives a session whose `begin()` is a no-op."""

    def __call__(self) -> "FakeDb":
        return self

    async def __aenter__(self) -> "FakeDb":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> "FakeDb":
        return self


class FakeMailer:
    def __init__(self, fail: dict[str, SendError] | None = None, delay: float = 0) -> None:
        self.fail = fail or {}
        self.delay = delay
        self.sent: list[OutgoingEmail] = []
        self.busy = 0
        self.most_at_once = 0

    async def send(self, email: OutgoingEmail) -> None:
        self.busy += 1
        self.most_at_once = max(self.most_at_once, self.busy)
        try:
            await asyncio.sleep(self.delay)
            error = self.fail.get(email.to[0])
            if error is not None:
                raise error
            self.sent.append(email)
        finally:
            self.busy -= 1


@pytest.fixture
def lane_repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"due": [], "sent": [], "retry": [], "failed": []}

    async def claim_due(s: Any, limit: int, lease: int) -> list[dict[str, Any]]:
        rows, state["due"] = state["due"][:limit], state["due"][limit:]
        return rows

    async def mark_sent(s: Any, email_id: Any) -> None:
        state["sent"].append(email_id)

    async def mark_retry(s: Any, email_id: Any, error: str, at: Any) -> None:
        state["retry"].append(email_id)

    async def mark_failed(s: Any, email_id: Any, error: str) -> None:
        state["failed"].append(email_id)

    for name, fn in (
        ("claim_due", claim_due),
        ("mark_sent", mark_sent),
        ("mark_retry", mark_retry),
        ("mark_failed", mark_failed),
    ):
        monkeypatch.setattr(signup_mail.repo, name, fn)
    return state


def _row(to: str, attempts: int = 1) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "to_address": to,
        "subject": rules.SUBJECT,
        "body_text": rules.code_email("123456"),
        "attempts": attempts,
    }


SENDER = Sender("no-reply@example.com", "CeDesk")


async def test_lane_sends_retries_and_gives_up(lane_repo: dict[str, Any]) -> None:
    ok, temporary, refused, last_try = (
        _row("ok@example.com"),
        _row("later@example.com"),
        _row("refused@example.com"),
        _row("tired@example.com", attempts=4),
    )
    lane_repo["due"] = [ok, temporary, refused, last_try]
    mailer = FakeMailer(
        fail={
            "later@example.com": SendError("421"),
            "refused@example.com": SendError("550", permanent=True),
            "tired@example.com": SendError("421"),
        }
    )
    result = await signup_mail.send_due(FakeDb(), mailer, SENDER, concurrency=10)  # type: ignore[arg-type]
    assert (result.claimed, result.sent, result.retrying, result.failed) == (4, 1, 1, 2)
    assert lane_repo["sent"] == [ok["id"]]
    assert lane_repo["retry"] == [temporary["id"]]
    assert set(lane_repo["failed"]) == {refused["id"], last_try["id"]}
    email = mailer.sent[0]
    assert email.from_address == "no-reply@example.com"
    assert email.to == ["ok@example.com"] and "123456" in email.text


async def test_lane_sends_at_most_concurrency_at_once(lane_repo: dict[str, Any]) -> None:
    lane_repo["due"] = [_row(f"p{i}@example.com") for i in range(30)]
    mailer = FakeMailer(delay=0.01)
    result = await signup_mail.send_due(FakeDb(), mailer, SENDER, concurrency=4)  # type: ignore[arg-type]
    assert result.sent == 30
    assert mailer.most_at_once == 4


async def test_an_unexpected_mailer_error_is_retried_not_lost(lane_repo: dict[str, Any]) -> None:
    class Broken:
        async def send(self, email: OutgoingEmail) -> None:
            raise RuntimeError("bug")

    row = _row("p@example.com")
    lane_repo["due"] = [row]
    result = await signup_mail.send_due(FakeDb(), Broken(), SENDER, concurrency=1)  # type: ignore[arg-type]
    assert result.retrying == 1 and lane_repo["retry"] == [row["id"]]


async def test_the_lane_survives_errors_and_wakes_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes: list[str] = []

    async def send_due(*args: Any, **kwargs: Any) -> signup_mail.LaneResult:
        passes.append("pass")
        if len(passes) == 1:
            raise RuntimeError("database down")
        return signup_mail.LaneResult()

    async def nothing(*args: Any) -> None:
        return None

    monkeypatch.setattr(signup_mail, "send_due", send_due)
    monkeypatch.setattr(signup_mail, "check_health", nothing)
    monkeypatch.setattr(signup_mail, "housekeeping", nothing)
    monkeypatch.setattr(signup_mail, "ERROR_PAUSE_SECONDS", 0.01)
    monkeypatch.setattr(signup_mail, "POLL_SECONDS", 30.0)  # only a wake-up can be quick
    wake = asyncio.Event()
    lane = asyncio.create_task(
        signup_mail.run_lane(FakeDb(), FakeMailer(), SENDER, concurrency=1, wake=wake)  # type: ignore[arg-type]
    )
    try:
        for _ in range(100):
            if len(passes) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(passes) == 2  # the failed pass did not stop the lane
        wake.set()  # the API queued a code
        for _ in range(100):
            if len(passes) >= 3:
                break
            await asyncio.sleep(0.01)
        assert len(passes) == 3  # woken at once, not after the 30 s poll
    finally:
        lane.cancel()
        with pytest.raises(asyncio.CancelledError):
            await lane


async def test_the_lane_runs_once_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def send_due(*args: Any, **kwargs: Any) -> signup_mail.LaneResult:
        calls.append(1)
        return signup_mail.LaneResult()

    async def nothing(*args: Any) -> None:
        return None

    monkeypatch.setattr(signup_mail, "send_due", send_due)
    monkeypatch.setattr(signup_mail, "check_health", nothing)
    monkeypatch.setattr(signup_mail, "housekeeping", nothing)
    await signup_mail.run_lane(FakeDb(), FakeMailer(), SENDER, concurrency=1, once=True)  # type: ignore[arg-type]
    assert calls == [1]


def test_signup_off_answers_404_even_to_a_bad_body() -> None:
    """Off must look like no such route: not a 422 that gives the routes away."""
    with _client() as client:
        for path in (
            "/api/v1/auth/signup",
            "/api/v1/auth/signup/verify",
            "/api/v1/auth/signup/resend",
        ):
            for body in ({}, {"junk": 1}):
                r = client.post(path, json=body)
                assert r.status_code == 404, (path, body, r.text)
            r = client.post(
                path, content=b"{not json", headers={"content-type": "application/json"}
            )
            assert r.status_code == 404, path


def test_signup_on_still_checks_the_body() -> None:
    with _client(signup_enabled=True) as client:
        r = client.post("/api/v1/auth/signup", json={})
    assert r.status_code == 422


class _Pg(Exception):
    def __init__(self, constraint: str | None) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


def _integrity(constraint: str | None) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, _Pg(constraint))


def test_only_a_taken_address_counts_as_slug_taken() -> None:
    assert signup_service._slug_clash(_integrity("workspace_directory_pkey"))
    assert not signup_service._slug_clash(_integrity("member_workspace_id_email_key"))
    assert not signup_service._slug_clash(_integrity(None))
