"""The dispatcher every importer is reached through: what an Admin must give for each source,
and a clear refusal (never a crash) for a source that isn't offered or a credential that is
missing or unreadable."""

import asyncio
import json
from typing import Any

import pytest

from cedesk.helpdesk.domain.imports import AVAILABLE_SOURCES, SourceError
from cedesk.helpdesk.service.imports import connectors
from cedesk.helpdesk.service.imports.connectors import REQUIRED, build_connector, credentials_from


def build(source: str, settings: dict[str, str], creds: Any) -> Any:
    secret = creds if isinstance(creds, str) or creds is None else json.dumps(creds)
    return asyncio.run(build_connector(source, settings, secret))


def test_every_offered_source_says_what_it_needs() -> None:
    assert set(REQUIRED) == set(AVAILABLE_SOURCES)
    assert set(connectors.WHERE) == set(AVAILABLE_SOURCES)


@pytest.mark.parametrize("source", ["csv", "nonsense"])
def test_a_source_that_is_not_offered_is_refused(source: str) -> None:
    with pytest.raises(SourceError) as caught:
        build(source, {}, {"token": "x"})
    assert caught.value.permanent and "isn't available yet" in str(caught.value)


@pytest.mark.parametrize(
    ("source", "settings", "creds", "missing"),
    [
        ("intercom", {}, {}, "token"),
        ("zendesk", {"subdomain": "acme"}, {"email": "a@b.co"}, "token"),
        ("zendesk", {"subdomain": "acme"}, {"token": "t"}, "email"),
        ("zendesk", {"subdomain": "acme"}, {}, "client_id, client_secret"),
        ("zendesk", {"subdomain": "acme"}, {"client_id": "c"}, "client_secret"),
        ("freshdesk", {"domain": "acme"}, {}, "api_key"),
        ("gorgias", {"domain": "acme"}, {"api_key": "k"}, "email"),
        ("helpscout", {}, {"app_id": "i"}, "app_secret"),
    ],
)
def test_a_missing_credential_is_named(
    source: str, settings: dict[str, str], creds: dict[str, str], missing: str
) -> None:
    with pytest.raises(SourceError) as caught:
        build(source, settings, creds)
    assert caught.value.permanent and f"missing {missing}" in str(caught.value)


@pytest.mark.parametrize(
    ("source", "creds", "setting"),
    [
        ("zendesk", {"email": "a@b.co", "token": "t"}, "subdomain"),
        ("freshdesk", {"api_key": "k"}, "domain"),
        ("gorgias", {"email": "a@b.co", "api_key": "k"}, "domain"),
    ],
)
def test_an_account_source_needs_its_account_name(
    source: str, creds: dict[str, str], setting: str
) -> None:
    for settings in ({}, {setting: "   "}):
        with pytest.raises(SourceError) as caught:
            build(source, settings, creds)
        assert caught.value.permanent and setting in str(caught.value)


@pytest.mark.parametrize(
    "creds",
    [
        {"client_id": "c", "client_secret": "s"},  # OAuth client credentials (C0)
        {"access_token": "a"},  # an OAuth access token made elsewhere
        {"email": "a@b.co", "token": "t"},  # an API token, until 30 Apr 2027
    ],
)
def test_zendesk_takes_any_one_complete_sign_in(creds: dict[str, str]) -> None:
    assert build("zendesk", {"subdomain": "acme"}, creds).source == "zendesk"


def test_zendesk_help_text_says_how_to_make_an_oauth_client() -> None:
    where = connectors.WHERE["zendesk"]
    assert "OAuth clients" in where and "Confidential" in where and "read" in where


def test_credentials_that_cannot_be_read_are_a_permanent_error() -> None:
    for secret in (None, "", "not json", "[1, 2]", '{"token": 5}'):
        with pytest.raises(SourceError) as caught:
            credentials_from(secret)
        assert caught.value.permanent and "Connect again" in str(caught.value)


def test_credentials_are_trimmed() -> None:
    assert credentials_from(json.dumps({"token": "  abc  "})) == {"token": "abc"}


def test_an_error_never_contains_the_secret() -> None:
    with pytest.raises(SourceError) as caught:
        build("zendesk", {"subdomain": "acme"}, {"email": "a@b.co", "token": ""})
    assert "a@b.co" not in str(caught.value)
