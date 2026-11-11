"""deploy/: every setting env.example offers reaches a container. docker-compose passes
settings by name, so one added to env.example but not to the compose file is silently
ignored (12 Nov: CEDESK_MAIL_FORWARD_TO and CEDESK_MAIL_SPF_INCLUDE never reached the api)."""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
NAME = re.compile(r"^#?\s*(CEDESK_[A-Z0-9_]+)=", re.MULTILINE)

# Read by the API while answering requests (not only by the worker).
API_READS = (
    "CEDESK_SECRET_KEY", "CEDESK_CORS_ORIGINS", "CEDESK_APP_URL", "CEDESK_MAIL_FROM_ADDRESS",
    "CEDESK_INBOUND_EMAIL_TOKEN", "CEDESK_MAIL_FORWARD_TO", "CEDESK_MAIL_SPF_INCLUDE",
    "CEDESK_SCAN_BACKEND", "CEDESK_CEVANOS_URL",
)  # fmt: skip


def service(compose: str, name: str) -> str:
    """The text of one service's block."""
    start = compose.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z]+:\n", compose[start + 1 :])
    return compose[start : start + 1 + following.start()] if following else compose[start:]


def test_every_setting_in_env_example_reaches_a_container() -> None:
    offered = set(NAME.findall((DEPLOY / "env.example").read_text()))
    compose = (DEPLOY / "docker-compose.yml").read_text()
    assert len(offered) > 20
    assert sorted(n for n in offered if "${" + n not in compose) == []


def test_the_api_gets_what_it_reads() -> None:
    api = service((DEPLOY / "docker-compose.yml").read_text(), "api")
    assert sorted(n for n in API_READS if f"{n}: " not in api) == []
