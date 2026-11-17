"""The API's idea of the visitor's address behind a proxy (deploy/docker-compose.yml's
--forwarded-allow-ips). A client can't choose it by sending its own X-Forwarded-For."""

import re
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from cedesk.helpdesk.api.deps import client_ip

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"


def trusted() -> str:
    found = re.search(r"CEDESK_FORWARDED_ALLOW_IPS:-([^}]+)\}", COMPOSE.read_text())
    assert found, "deploy/docker-compose.yml must set a default list, not *"
    return found.group(1)


def seen_as(peer: str, forwarded: str | None) -> str | None:
    app = FastAPI()

    @app.get("/ip")
    async def ip(request: Request) -> dict[str, str | None]:
        return {"ip": client_ip(request)}

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=trusted())
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    with TestClient(wrapped, client=(peer, 50000)) as client:  # type: ignore[arg-type]
        return client.get("/ip", headers=headers).json()["ip"]  # type: ignore[no-any-return]


def test_the_default_is_a_list_of_proxies_not_everyone() -> None:
    assert "*" not in trusted()


@pytest.mark.parametrize("proxy", ["172.18.0.5", "127.0.0.1", "10.1.2.3"])
def test_behind_our_proxy_the_visitors_address_is_used(proxy: str) -> None:
    assert seen_as(proxy, "203.0.113.9") == "203.0.113.9"


def test_an_address_the_client_wrote_itself_is_ignored() -> None:
    # The client sent "X-Forwarded-For: 1.2.3.4"; the proxy appended the real address.
    assert seen_as("172.18.0.5", "1.2.3.4, 203.0.113.9") == "203.0.113.9"


def test_a_client_reaching_the_api_directly_cannot_pretend() -> None:
    assert seen_as("198.51.100.7", "1.2.3.4") == "198.51.100.7"
