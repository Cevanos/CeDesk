"""Every offered source builds through the dispatcher into a connector that speaks the
`SourceConnector` contract, and a bad account name is refused before any connection."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from cedesk.helpdesk.domain.imports import AVAILABLE_SOURCES, SourceError
from cedesk.helpdesk.service.imports.connectors import build_connector


def build(source: str, settings: dict[str, str], creds: Any) -> Any:
    return asyncio.run(build_connector(source, settings, json.dumps(creds)))


GOOD: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "intercom": ({"region": "eu"}, {"token": "tok-1234"}),
    "zendesk": ({"subdomain": "acme"}, {"email": "a@b.co", "token": "tok-1234"}),
    "freshdesk": ({"domain": "acme"}, {"api_key": "key-1234"}),
    "gorgias": ({"domain": "acme"}, {"email": "a@b.co", "api_key": "key-1234"}),
    "helpscout": ({}, {"app_id": "id-1", "app_secret": "secret-1234"}),
    "hubspot": ({}, {"token": "pat-1234"}),
    "zoho": (
        {"region": "in"},
        {"client_id": "id-1", "client_secret": "s-1", "refresh_token": "r-1"},
    ),
}


@pytest.mark.parametrize("source", sorted(AVAILABLE_SOURCES))
def test_each_offered_source_builds_through_the_dispatcher(source: str) -> None:
    async def allow(host: str) -> None:
        return None

    settings, creds = GOOD[source]
    connector = asyncio.run(
        build_connector(
            source,
            settings,
            json.dumps(creds),
            transport=httpx.MockTransport(lambda r: httpx.Response(500)),
            guard=allow,
        )
    )
    assert connector.source == source
    assert set(connector.status_map.values()) <= {"open", "pending", "resolved"}
    for name in ("check", "discover", "groups", "page"):
        assert callable(getattr(connector, name))


@pytest.mark.parametrize("bad", ["evil.com", "a.b", "10.0.0.1", "acme@evil.com", "a:80"])
@pytest.mark.parametrize("source", ["zendesk", "freshdesk", "gorgias"])
def test_a_bad_account_name_is_a_permanent_error_not_a_connection(source: str, bad: str) -> None:
    key = "subdomain" if source == "zendesk" else "domain"
    creds = GOOD[source][1]
    with pytest.raises(SourceError) as caught:
        build(source, {key: bad}, creds)
    assert caught.value.permanent
