"""The Calendly app (md PL7, W19), with RLS on: install with only the booking link (no
credential; only calendly.com links), the saved reply /book it adds and keeps in step,
a workspace's own /book kept (Calendly's becomes /book1), uninstall removing only
Calendly's reply, audited; other apps still need their credential; tenancy."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/apps"
R = "/api/v1/saved-replies"
LINK = "https://calendly.com/acme/30min"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"h": bearer(token), "ws": UUID(me["workspace"]["id"]),
            "agent": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def replies(client: AsyncClient, w: dict[str, Any]) -> dict[str, Any]:
    return {r["shortcut"]: r for r in ok(await client.get(R, headers=w["h"]))}


async def audit(client: AsyncClient, w: dict[str, Any]) -> list[Any]:
    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "apps"}))
    return [(e["action"], e["detail"]) for e in page["entries"]]


async def test_installing_calendly_adds_book(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    [info] = [a for a in ok(await client.get(A, headers=w["h"])) if a["app"] == "calendly"]
    assert (info["credential"], info["settings_fields"], info["card"]) == (None, ["link"], "book")

    bad_links = ["http://calendly.com/acme", "https://calendly.com.evil.test/acme",
                 "https://evil.test/calendly.com/acme", "https://calendly.com/",
                 "https://calendly.com/acme/30min?x=<b>", "javascript:alert(1)", "",
                 "https://user@calendly.com/acme", "https://calendly.com/a/b/c"]  # fmt: skip
    for link in bad_links:
        r = await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": link}})
        assert r.status_code == 422, link
    for extra in ({"secret": "abc"}, {"signing_secret": "x" * 32},
                  {"settings": {"link": LINK, "colour": "red"}}):  # fmt: skip
        body = {"settings": {"link": LINK}} | extra
        assert (await client.put(f"{A}/calendly", headers=w["h"], json=body)).status_code == 422
    body = {"settings": {"link": LINK}}
    assert (await client.put(f"{A}/calendly", headers=w["agent"], json=body)).status_code == 403
    assert "book" not in await replies(client, w)

    got = ok(await client.put(f"{A}/calendly", headers=w["h"], json=body))
    assert (got["installed"], got["secret_hint"], got["settings"]) == (True, None, {"link": LINK})
    book = (await replies(client, w))["book"]
    assert book["source"] == "calendly" and LINK in book["body"]
    shown = ok(await client.get(R, headers=w["agent"], params={"q": "bo"}))
    assert [r["shortcut"] for r in shown] == ["book"]  # agents can use it

    newer = "https://calendly.com/acme/15min/"
    ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": newer}}))
    after = await replies(client, w)
    assert after["book"]["id"] == book["id"] and newer.rstrip("/") in after["book"]["body"]
    assert [r for r in after.values() if r["source"] == "calendly"] == [after["book"]]

    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    assert "book" not in await replies(client, w)
    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 404
    assert await audit(client, w) == [
        ("uninstalled", {"saved_replies_removed": ["/book"]}),
        ("changed", {"settings": {"link": "https://calendly.com/acme/15min"},
                     "saved_reply": "/book"}),
        ("installed", {"settings": {"link": LINK}, "saved_reply": "/book"}),
    ]  # fmt: skip


async def test_a_book_of_your_own_is_kept(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    own = {"shortcut": "book", "body": "Call us on 080 1234"}
    mine = ok(await client.post(R, headers=w["h"], json=own), 201)
    ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": LINK}}))
    got = await replies(client, w)
    assert got["book"] == mine  # untouched
    assert got["book1"]["source"] == "calendly" and LINK in got["book1"]["body"]

    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    got = await replies(client, w)
    assert "book1" not in got and got["book"] == mine  # only Calendly's went


async def test_other_apps_still_need_their_credential(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for app, body in (("stripe", {}), ("slack_alerts", {"secret": ""}),
                      ("jira", {"settings": {"site": "acme", "email": "a@acme.test",
                                             "project": "SUP"}})):  # fmt: skip
        r = await client.put(f"{A}/{app}", headers=w["h"], json=body)
        assert r.status_code == 422 and "Paste the" in r.json()["error"]["message"], app


async def test_another_workspace_is_untouched(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": LINK}}))
    assert "book" in await replies(client, w)
    assert "book" not in await replies(client, other)
    assert (await client.delete(f"{A}/calendly", headers=other["h"])).status_code == 404
    assert "book" in await replies(client, w)  # still there
