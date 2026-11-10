"""Rate limiter, the start-up refusal outside dev, and CORS origins parsing."""

import pytest
from pydantic import SecretStr

from cedesk.main import create_app
from cedesk.ratelimit import SlidingWindowLimiter
from cedesk.settings import Env, Settings

GOOD_KEY = SecretStr("k" * 40)
GOOD_DB = "postgresql+asyncpg://cedesk_app:9f3e1c@db:5432/cedesk"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_limiter_allows_up_to_the_limit_then_waits() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(3, 60, clock)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == 61
    assert limiter.hit("b") is None  # other addresses are not affected
    clock.t += 30
    assert limiter.hit("a") == 31  # refused attempts are not counted
    clock.t += 31
    assert limiter.hit("a") is None  # the window slid past the first attempts


def test_limiter_forgets_idle_addresses() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(5, 10, clock)
    for i in range(999):
        limiter.hit(f"10.0.0.{i}")
    clock.t += 11
    limiter.hit("last")  # the 1000th call sweeps
    assert list(limiter._hits) == ["last"]


def test_dev_allows_dev_values() -> None:
    assert Settings(env=Env.dev).unsafe() == []


@pytest.mark.parametrize("env", [Env.staging, Env.production])
def test_outside_dev_the_dev_values_are_refused(env: Env) -> None:
    problems = Settings(env=env, cors_origins="*").unsafe()
    assert len(problems) == 3
    with pytest.raises(RuntimeError, match="Refusing to start"):
        create_app(Settings(env=env))
    assert Settings(env=env, secret_key=SecretStr("short"), database_url=GOOD_DB).unsafe()
    ok = Settings(env=env, secret_key=GOOD_KEY, database_url=GOOD_DB)
    assert ok.unsafe() == []
    create_app(ok)


def test_cors_origins_parsing() -> None:
    s = Settings(cors_origins=" https://app.example.com/ , http://localhost:3000,, ")
    assert s.allowed_origins() == ["https://app.example.com", "http://localhost:3000"]
    assert Settings(cors_origins="").allowed_origins() == []
