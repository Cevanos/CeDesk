"""Which connector serves each source, and what an Admin must give to connect to it.

`build_connector` is what `framework.make_importers` calls for every source but CSV. It
reads the stored credentials (a JSON object of strings, sealed in the database), checks the
Admin gave what that source needs, and hands them to the source's module. A bad account
name or a missing credential is a permanent `SourceError`, which the API turns into a 422
and a running import into a failed run.

Each source module (`intercom`, `zendesk`, `freshdesk`, `gorgias`, `helpscout`, `hubspot`,
`zoho`) offers
`make(settings, credentials, *, transport=None, guard=None) -> SourceConnector`.
"""

import importlib
import json
from collections.abc import Mapping
from typing import Any

import httpx

from cedesk.egress import BadAddress, Guard
from cedesk.helpdesk.domain.imports import AVAILABLE_SOURCES, SourceConnector, SourceError, label

# source -> (credentials the Admin must give, setting the Admin must give or None)
REQUIRED: Mapping[str, tuple[tuple[str, ...], str | None]] = {
    "intercom": (("token",), None),
    "zendesk": (("email", "token"), "subdomain"),
    "freshdesk": (("api_key",), "domain"),
    "gorgias": (("email", "api_key"), "domain"),
    "helpscout": (("app_id", "app_secret"), None),
    "hubspot": (("token",), None),
    "zoho": (("client_id", "client_secret", "refresh_token"), "region"),
}
# what the key in the Settings screen asks for, so the message can name it
WHERE = {
    "intercom": "an access token (Developer Hub > your app > Authentication)",
    "zendesk": "your email and an API token (Admin Center > Apps and integrations > APIs)",
    "freshdesk": "your API key (Profile settings)",
    "gorgias": "your email and an API key (Settings > REST API)",
    "helpscout": "an app id and secret (Your Profile > My Apps)",
    "hubspot": (
        "a private app's access token (Settings > Integrations > Private apps) with the "
        "scopes tickets, crm.objects.contacts.read, crm.objects.owners.read, "
        "sales-email-read and files"
    ),
    "zoho": (
        "a Self Client's client id and secret and a refresh token (api-console.zoho.com) "
        "with the scopes Desk.tickets.READ, Desk.contacts.READ, Desk.basic.READ and "
        "Desk.articles.READ"
    ),
}


def credentials_from(secret: str | None) -> dict[str, str]:
    """The stored credentials: a JSON object whose values are text."""
    try:
        found = json.loads(secret or "")
    except ValueError:
        found = None
    if not isinstance(found, dict) or not all(isinstance(v, str) for v in found.values()):
        raise SourceError("The saved credentials can't be read. Connect again.", permanent=True)
    return {str(k): v.strip() for k, v in found.items()}


async def build_connector(
    source: str,
    settings: Mapping[str, Any],
    secret: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    if source not in AVAILABLE_SOURCES:
        raise SourceError(f"Importing from {label(source)} isn't available yet.", permanent=True)
    needed, setting = REQUIRED[source]
    credentials = credentials_from(secret)
    missing = [k for k in needed if not credentials.get(k)]
    if missing:
        raise SourceError(
            f"{label(source)} needs {WHERE[source]}: missing {', '.join(missing)}.", permanent=True
        )
    plain = {str(k): str(v) for k, v in settings.items()}
    if setting is not None and not plain.get(setting, "").strip():
        raise SourceError(f"{label(source)} needs the account name ('{setting}').", permanent=True)
    module = importlib.import_module(f"cedesk.helpdesk.service.imports.{source}")
    try:
        connector: SourceConnector = module.make(
            plain, credentials, transport=transport, guard=guard
        )
    except BadAddress as exc:
        raise SourceError(str(exc), permanent=True) from None
    return connector
