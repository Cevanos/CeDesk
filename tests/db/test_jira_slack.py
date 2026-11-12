"""The Jira app and Slack alerts (md X10, W20, PL7), with RLS on and a fake Jira and Slack:
installing checks the credential; issues are created or linked from tickets, with a
system line and no customer text sent; Slack gets one line per Urgent or High ticket after
the rules ran, and nothing for the rest."""

import base64
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient

from cedesk.crypto import Sealer
from cedesk.helpdesk.service import apps
from cedesk.helpdesk.service.app_post import AppPost
from cedesk.helpdesk.service.delivery import deliver_due
from cedesk.helpdesk.service.slack_alerts import SlackAlertHandler
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/apps"
T = "/api/v1/tickets"
TOKEN = "atl-token-1234"
HOOK = "https://hooks.slack.com/services/T01/B02/abcd"
SEALER = Sealer(Settings().secret_key.get_secret_value())
JIRA_SETTINGS = {"site": "acme", "email": "Ops@Acme.com", "project": "sup"}


class Fake:
    """A fake Jira (acme.atlassian.net) and Slack (hooks.slack.com)."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.slack_gone = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if request.url.host == "acme.atlassian.net":
            auth = base64.b64encode(f"ops@acme.com:{TOKEN}".encode()).decode()
            if request.headers.get("Authorization") != f"Basic {auth}":
                return httpx.Response(401)
            if path.endswith("/myself") or path.endswith("/project/SUP"):
                return httpx.Response(200, json={"ok": True})
            if path.endswith("/issue") and request.method == "POST":
                return httpx.Response(201, json={"id": "10001", "key": "SUP-12"})
            if path.endswith("/issue/SUP-7"):
                return httpx.Response(200, json={"key": "SUP-7"})
            return httpx.Response(404)
        if request.url.host == "hooks.slack.com":
            if self.slack_gone or path != "/services/T01/B02/abcd":
                return httpx.Response(404, text="no_service")
            return httpx.Response(200, text="ok")
        return httpx.Response(404)

    def slack(self) -> list[str]:
        return [json.loads(r.content)["text"] for r in self.seen if r.url.host == "hooks.slack.com"]


def install_fakes(client: AsyncClient) -> tuple[Fake, Any]:
    fake = Fake()

    def post(host: str, headers: Mapping[str, str], label: str) -> AppPost:
        async def public(name: str) -> None:
            return None

        return AppPost(host, headers=headers, label=label, guard=public,
                       transport=httpx.MockTransport(fake))  # fmt: skip

    client._transport.app.state.app_post = post  # type: ignore[attr-defined]
    return fake, post


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    await add(client, token, "neha@example.com", "agent", "Neha")
    await add(client, token, "meena@example.com", "light_agent", "Meena")
    me = ok(await client.get("/api/v1/me", headers=h))
    return {"slug": slug, "h": h, "ws": UUID(me["workspace"]["id"]),
            "neha": bearer(await sign_in(client, slug, "neha@example.com")),
            "meena": bearer(await sign_in(client, slug, "meena@example.com"))}  # fmt: skip


async def ticket(
    client: AsyncClient, w: dict[str, Any], subject: str, priority: str = "medium"
) -> Any:
    body = {"subject": subject, "priority": priority, "description": "My card was charged twice",
            "contact": {"email": "priya@example.org", "name": "Priya"}}  # fmt: skip
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def test_jira_issues_from_tickets(client: AsyncClient, app_sessionmaker: Sm) -> None:
    fake, _ = install_fakes(client)
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund stuck")
    url = f"{T}/{t['id']}/jira"
    r = await client.post(url, headers=w["neha"], json={})
    assert r.status_code == 409  # not installed yet

    for settings, secret in ((JIRA_SETTINGS, "wrong"), (JIRA_SETTINGS | {"project": "NOPE"}, TOKEN),
                             (JIRA_SETTINGS | {"site": "acme.evil.com"}, TOKEN),
                             (JIRA_SETTINGS | {"email": "nobody"}, TOKEN)):  # fmt: skip
        r = await client.put(
            f"{A}/jira", headers=w["h"], json={"settings": settings, "secret": secret}
        )
        assert r.status_code == 422, (settings, r.text)
    made = ok(await client.put(f"{A}/jira", headers=w["h"],
                               json={"settings": JIRA_SETTINGS, "secret": TOKEN}))  # fmt: skip
    assert made["settings"] == {"site": "acme", "email": "ops@acme.com", "project": "SUP"}

    issue = ok(await client.post(url, headers=w["neha"], json={"description": "Payout bug"}), 201)
    assert (issue["key"], issue["made_here"]) == ("SUP-12", True)
    assert issue["url"] == "https://acme.atlassian.net/browse/SUP-12"
    sent = json.loads(next(r for r in fake.seen if r.method == "POST").content)["fields"]
    assert (sent["project"], sent["summary"]) == ({"key": "SUP"}, "Refund stuck")
    text = json.dumps(sent["description"])
    assert "Payout bug" in text and f"/tickets/{t['id']}" in text
    assert "charged twice" not in text  # the customer's words stay in CeDesk

    linked = ok(await client.post(f"{url}/link", headers=w["neha"], json={"key": "sup-7"}), 201)
    assert (linked["key"], linked["made_here"]) == ("SUP-7", False)
    assert (
        await client.post(f"{url}/link", headers=w["neha"], json={"key": "SUP-7"})
    ).status_code == 409
    assert (
        await client.post(f"{url}/link", headers=w["neha"], json={"key": "SUP-404"})
    ).status_code == 422
    assert (
        await client.post(f"{url}/link", headers=w["neha"], json={"key": "nope"})
    ).status_code == 422
    assert (await client.post(url, headers=w["meena"], json={})).status_code == 403  # light agent
    assert [i["key"] for i in ok(await client.get(url, headers=w["meena"]))] == ["SUP-12", "SUP-7"]

    assert (await client.delete(f"{url}/SUP-7", headers=w["neha"])).status_code == 204
    assert (await client.delete(f"{url}/SUP-7", headers=w["neha"])).status_code == 404
    lines = [m["body"] for m in ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))["messages"]
             if m["kind"] == "system"]  # fmt: skip
    assert {"Jira issue SUP-12 created by Neha", "Jira issue SUP-7 linked by Neha",
            "Jira issue SUP-7 unlinked by Neha"} <= set(lines)  # fmt: skip


async def test_slack_alerts_for_urgent_and_high(client: AsyncClient, app_sessionmaker: Sm) -> None:
    fake, post = install_fakes(client)
    w = await desk(client, app_sessionmaker)
    sm = app_sessionmaker
    handler = SlackAlertHandler(sm, SEALER, post, apps.slack_bound, app_url="https://app.test")
    for bad in ("https://evil.com/services/x", "http://hooks.slack.com/services/x"):
        r = await client.put(f"{A}/slack_alerts", headers=w["h"], json={"secret": bad})
        assert r.status_code == 422, bad
    ok(await client.put(f"{A}/slack_alerts", headers=w["h"], json={"secret": HOOK}))
    assert fake.slack() == [apps.SLACK_TEST_LINE]  # installing posts a test line

    urgent = await ticket(client, w, "Payment failed <!channel>", "urgent")
    calm = await ticket(client, w, "Question about sizes")
    await deliver_due(sm, [handler])
    alerts = fake.slack()[1:]
    assert len(alerts) == 1 and alerts[0].startswith(
        ":red_circle: Urgent: <https://app.test/tickets/"
    )
    assert f"|T-{urgent['number']}>" in alerts[0] and "&lt;!channel&gt;" in alerts[0]

    ok(await client.patch(f"{T}/{calm['id']}", headers=w["h"], json={"priority": "high"}))
    ok(
        await client.patch(
            f"{T}/{urgent['id']}", headers=w["h"], json={"subject": "Payment failed"}
        )
    )
    await deliver_due(sm, [handler])
    alerts = fake.slack()[1:]
    assert len(alerts) == 2 and "High:" in alerts[1] and "Question about sizes" in alerts[1]

    fake.slack_gone = True  # the webhook was deleted in Slack: fails at once, listed
    gone = await ticket(client, w, "Site down", "urgent")
    result = await deliver_due(sm, [handler])
    assert result.failed == 1
    failed = ok(await client.get("/api/v1/outbox/failed", headers=w["h"]))
    assert any(e["ticket_id"] == gone["id"] for e in failed["events"])

    other = await desk(client, sm)  # no Slack app: nothing posted
    before = len(fake.slack())
    await ticket(client, other, "Also urgent", "urgent")
    await deliver_due(sm, [handler])
    assert len(fake.slack()) == before
