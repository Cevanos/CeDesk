"""Self-service sign-up through the real API and database, as `cedesk_app`: the whole
flow, the races (double verify, parallel guesses, an address taken meanwhile), the limits
and the worker's sign-up lane."""

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.helpdesk.domain import signup as rules
from cedesk.helpdesk.service import signup_mail
from cedesk.helpdesk.service.auth import create_workspace
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.mail import OutgoingEmail
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import PASSWORD, Sm, bearer, sign_in

pytestmark = pytest.mark.db

SENDER = Sender("no-reply@example.com", "CeDesk")
Client = Callable[..., AsyncClient]


def _app(sm: Sm, **settings: Any) -> Any:
    base: dict[str, Any] = {
        "signup_enabled": True,
        "mail_from_address": "no-reply@example.com",
        "sign_in_limit": 100_000,
        "signup_per_ip_hour": 100_000,
        "signup_workspaces_per_ip_day": 100_000,
    }
    app = create_app(Settings(**{**base, **settings}))
    app.state.sessionmaker = sm
    return app


@pytest.fixture
async def make_client(app_sessionmaker: Sm) -> AsyncIterator[Client]:
    """A client with sign-up on, from its own address (each test counts on its own)."""
    opened: list[AsyncClient] = []

    def make(ip: str | None = None, **settings: Any) -> AsyncClient:
        ip = ip or f"198.51.100.{uuid4().int % 250 + 1}"
        transport = ASGITransport(app=_app(app_sessionmaker, **settings), client=(ip, 1234))
        client = AsyncClient(transport=transport, base_url="http://t")
        opened.append(client)
        return client

    yield make
    for client in opened:
        await client.aclose()


@pytest.fixture
async def client(make_client: Client) -> AsyncClient:
    return make_client()


def _form(**change: str) -> dict[str, str]:
    tag = uuid4().hex[:10]
    return {
        "workspace": f"su-{tag}",
        "workspace_name": "Acme Support",
        "name": "Priya",
        "email": f"priya-{tag}@example.com",
        "password": PASSWORD,
        **change,
    }


async def _start(client: AsyncClient, form: dict[str, str]) -> str:
    r = await client.post("/api/v1/auth/signup", json=form)
    assert r.status_code == 202, r.text
    signup_id: str = r.json()["signup_id"]
    return signup_id


async def _code(sm: Sm, signup_id: str) -> str:
    """The code in the newest email of this sign-up (before the lane wipes it)."""
    async with sm() as s:
        body = (
            await s.execute(
                text(
                    "SELECT body_text FROM auth_email_out WHERE signup_id = :id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"id": UUID(signup_id)},
            )
        ).scalar_one()
    found = re.search(r"\b(\d{6})\b", body)
    assert found, "no code in the email"
    return found.group(1)


async def _verify(client: AsyncClient, signup_id: str, code: str, **extra: str) -> Any:
    return await client.post(
        "/api/v1/auth/signup/verify", json={"signup_id": signup_id, "code": code, **extra}
    )


async def _signup_row(sm: Sm, signup_id: str) -> Any:
    async with sm() as s:
        return (
            (
                await s.execute(
                    text("SELECT * FROM workspace_signup WHERE id = :id"),
                    {"id": UUID(signup_id)},
                )
            )
            .mappings()
            .one()
        )


async def test_signup_creates_the_workspace_and_its_admin_signs_in(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    form = _form()
    signup_id = await _start(client, form)
    # Nothing is created before the code.
    r = await client.post(
        "/api/v1/auth/sign-in",
        json={"workspace": form["workspace"], "email": form["email"], "password": PASSWORD},
    )
    assert r.status_code == 401

    r = await _verify(client, signup_id, await _code(app_sessionmaker, signup_id))
    assert r.status_code == 200, r.text
    assert r.json() == {"workspace": form["workspace"]}

    token = await sign_in(client, form["workspace"], form["email"])
    me = (await client.get("/api/v1/me", headers=bearer(token))).json()
    assert me["member"]["role"] == "admin"
    assert me["member"]["name"] == "Priya"
    assert me["workspace"]["name"] == "Acme Support"

    row = await _signup_row(app_sessionmaker, signup_id)
    assert row["status"] == "completed"
    assert row["password_hash"] is None  # dropped once the member has its own copy
    assert row["workspace_id"] is not None

    # The first audit entries say who made it; the cloud plan was set.
    entries = (await client.get("/api/v1/audit", headers=bearer(token))).json()["entries"]
    areas = {(e["area"], e["actor_name"]) for e in entries}
    assert ("workspace", "Self-service sign-up") in areas
    assert ("plan", "Self-service sign-up") in areas
    usage = (await client.get("/api/v1/usage", headers=bearer(token))).json()
    assert usage["plan"] == "free"


async def test_verify_twice_or_in_parallel_makes_one_workspace(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    form = _form()
    signup_id = await _start(client, form)
    code = await _code(app_sessionmaker, signup_id)
    answers = await asyncio.gather(*(_verify(client, signup_id, code) for _ in range(8)))
    assert [r.status_code for r in answers] == [200] * 8
    assert {r.json()["workspace"] for r in answers} == {form["workspace"]}
    async with app_sessionmaker() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM workspace_directory WHERE slug = :slug"),
                {"slug": form["workspace"]},
            )
        ).scalar_one()
    assert count == 1
    # Later, the same code still answers the same; a wrong one doesn't.
    assert (await _verify(client, signup_id, code)).status_code == 200
    wrong = "000000" if code != "000000" else "111111"
    assert (await _verify(client, signup_id, wrong)).status_code == 422


async def test_wrong_codes_use_up_the_tries_even_in_parallel(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    signup_id = await _start(client, _form())
    code = await _code(app_sessionmaker, signup_id)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    answers = await asyncio.gather(*(_verify(client, signup_id, wrong) for _ in range(12)))
    statuses = sorted(r.status_code for r in answers)
    assert statuses.count(422) == rules.CODE_TRIES  # exactly 5 guesses were compared
    assert statuses.count(410) == 12 - rules.CODE_TRIES
    assert (await _signup_row(app_sessionmaker, signup_id))["code_tries"] == rules.CODE_TRIES
    # Now even the right code is refused: ask for a new one.
    r = await _verify(client, signup_id, code)
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "code_expired"


async def test_an_address_taken_meanwhile_keeps_the_code_for_another(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    form = _form()
    signup_id = await _start(client, form)
    code = await _code(app_sessionmaker, signup_id)
    await create_workspace(
        app_sessionmaker,
        name="Faster",
        slug=form["workspace"],
        admin_email="other@example.com",
        admin_name="Other",
        admin_password=PASSWORD,
        seed=False,
    )
    r = await _verify(client, signup_id, code)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "slug_taken"
    # Rolled back: the try isn't used up, and the same code works with a new address.
    assert (await _signup_row(app_sessionmaker, signup_id))["code_tries"] == 0
    new_slug = f"{form['workspace']}-2"
    r = await _verify(client, signup_id, code, workspace=new_slug)
    assert r.status_code == 200, r.text
    assert r.json() == {"workspace": new_slug}
    await sign_in(client, new_slug, form["email"])


async def test_taken_and_reserved_addresses_are_refused_at_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    taken = f"tk-{uuid4().hex[:10]}"
    await create_workspace(
        app_sessionmaker, name="T", slug=taken, admin_email="a@example.com",
        admin_name="A", admin_password=PASSWORD, seed=False,
    )  # fmt: skip
    r = await client.post("/api/v1/auth/signup", json=_form(workspace=taken))
    assert r.status_code == 409 and r.json()["error"]["code"] == "slug_taken"
    r = await client.post("/api/v1/auth/signup", json=_form(workspace="settings"))
    assert r.status_code == 422


async def test_a_new_signup_for_the_email_replaces_the_waiting_one(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    form = _form()
    first = await _start(client, form)
    first_code = await _code(app_sessionmaker, first)
    second = await _start(client, {**form, "workspace": form["workspace"] + "-b"})
    r = await _verify(client, first, first_code)
    assert r.status_code == 410 and r.json()["error"]["code"] == "signup_expired"
    assert (await _signup_row(app_sessionmaker, first))["password_hash"] is None
    r = await _verify(client, second, await _code(app_sessionmaker, second))
    assert r.status_code == 200


async def test_three_code_emails_an_hour_per_address(client: AsyncClient) -> None:
    form = _form()
    for _ in range(rules.CODES_PER_HOUR):
        await _start(client, form)
    r = await client.post("/api/v1/auth/signup", json=form)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


async def test_parallel_signups_for_one_email_leave_one_waiting(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    form = _form()
    answers = await asyncio.gather(
        *(client.post("/api/v1/auth/signup", json=form) for _ in range(6))
    )
    assert sorted(r.status_code for r in answers).count(202) == rules.CODES_PER_HOUR
    async with app_sessionmaker() as s:
        waiting = (
            await s.execute(
                text(
                    "SELECT count(*) FROM workspace_signup "
                    "WHERE email = :email AND status = 'pending'"
                ),
                {"email": form["email"]},
            )
        ).scalar_one()
    assert waiting == 1


async def test_signups_per_address_per_hour(make_client: Client) -> None:
    client = make_client(signup_per_ip_hour=2)
    for _ in range(2):
        await _start(client, _form())
    r = await client.post("/api/v1/auth/signup", json=_form())
    assert r.status_code == 429
    # Another address is not affected.
    await _start(make_client(signup_per_ip_hour=2), _form())


async def test_resend_waits_a_minute_and_replaces_the_code(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    signup_id = await _start(client, _form())
    old = await _code(app_sessionmaker, signup_id)
    r = await client.post("/api/v1/auth/signup/resend", json={"signup_id": signup_id})
    assert r.status_code == 429 and int(r.headers["retry-after"]) <= 61

    monkeypatch.setattr(rules, "RESEND_AFTER", timedelta(0))
    r = await client.post("/api/v1/auth/signup/resend", json={"signup_id": signup_id})
    assert r.status_code == 202, r.text
    new = await _code(app_sessionmaker, signup_id)
    if new != old:
        assert (await _verify(client, signup_id, old)).status_code == 422
    assert (await _verify(client, signup_id, new)).status_code == 200


async def test_unknown_signup_is_gone(client: AsyncClient) -> None:
    r = await _verify(client, str(uuid4()), "123456")
    assert r.status_code == 410 and r.json()["error"]["code"] == "signup_expired"
    r = await client.post("/api/v1/auth/signup/resend", json={"signup_id": str(uuid4())})
    assert r.status_code == 410


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.sleep(0.01)
        self.sent.append(email)


async def _drain(sm: Sm) -> None:
    """Sends what earlier tests queued, so this test counts only its own emails."""
    while (await signup_mail.send_due(sm, RecordingMailer(), SENDER, concurrency=50)).claimed:
        pass


async def test_the_lane_sends_each_code_once_and_wipes_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    await _drain(app_sessionmaker)
    ids = [await _start(client, _form()) for _ in range(5)]
    mailer = RecordingMailer()
    # Two lanes at once (two workers): SKIP LOCKED gives each email to one of them.
    await asyncio.gather(
        signup_mail.send_due(app_sessionmaker, mailer, SENDER, concurrency=3),
        signup_mail.send_due(app_sessionmaker, mailer, SENDER, concurrency=3),
    )
    assert len(mailer.sent) == 5
    assert len({e.to[0] for e in mailer.sent}) == 5
    async with app_sessionmaker() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT status, body_text FROM auth_email_out WHERE signup_id = ANY(:ids)"
                    ),
                    {"ids": [UUID(i) for i in ids]},
                )
            )
            .mappings()
            .all()
        )
    assert {r["status"] for r in rows} == {"sent"}
    assert all(not re.search(r"\d{6}", r["body_text"]) for r in rows)
    again = await signup_mail.send_due(app_sessionmaker, mailer, SENDER, concurrency=3)
    assert again.claimed == 0
