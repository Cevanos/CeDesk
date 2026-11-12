"""A HubSpot import end to end (md M1-M10 for HubSpot), with RLS on: the real connector
against a recorded-shape HubSpot (`tests/unit/test_import_hubspot.py`'s fake), through the
API and the worker. Stages become statuses, emails and notes become messages, a file is
copied, the token never shows, a second run makes nothing twice, and a sync brings changes."""

from collections.abc import Mapping
from typing import Any

import httpx
from httpx import AsyncClient

from cedesk.helpdesk.service.imports import framework, hubspot
from tests.db.helpers import Sm, ok
from tests.db.test_imports import SETTINGS, I, desk, im, run_state, sql, work
from tests.unit.test_import_hubspot import TOKEN, allow, route

__all__ = ["im"]  # the fixture, used by name


def use_fake_hubspot(c: AsyncClient) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return route(request)

    async def build(name: str, settings: Mapping[str, Any], secret: str | None) -> Any:
        assert name == "hubspot" and secret is not None and TOKEN in secret
        return hubspot.make(
            {}, {"token": TOKEN}, transport=httpx.MockTransport(wrapped), guard=allow
        )

    app = c.desk_app  # type: ignore[attr-defined]
    app.state.importers = framework.make_importers(
        SETTINGS.secret_key.get_secret_value(), app.state.store, build=build
    )
    return seen


async def test_a_hubspot_import_end_to_end(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    seen = use_fake_hubspot(im)
    w = await desk(im, sm)
    r = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "hubspot", "settings": {}, "credentials": {"token": TOKEN}},
    )
    conn = ok(r, 201)
    assert TOKEN not in r.text and conn["label"] == "HubSpot account 1234567"

    dry = ok(await im.post(f"{I}/connections/{conn['id']}/dry-run", headers=w["admin"], json={}))
    assert dry["will_create"]["tickets"] == 3 and dry["already_imported"].get("tickets", 0) == 0
    assert any("aren't imported" in x for x in dry["warnings"])

    started = ok(
        await im.post(
            f"{I}/connections/{conn['id']}/run",
            headers=w["admin"],
            json={"kinds": ["contacts", "tickets"], "team_map": {"77": w["team"]}},
        ),
        202,
    )
    await work(im, sm)
    run = await run_state(im, w, started["id"])
    assert run["state"] == "done", run
    rows = await sql(
        sm, w["ws"],
        "SELECT t.source_id, t.status, t.priority, t.channel, t.team_id, t.tags, "
        "c.email FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id "
        "WHERE t.source = 'hubspot' ORDER BY t.source_id",
    )  # fmt: skip
    got = {r["source_id"]: r for r in rows}
    assert {k: r["status"] for k, r in got.items()} == {
        "101": "pending", "102": "resolved", "103": "open"}  # fmt: skip
    assert (got["101"]["priority"], got["103"]["priority"]) == ("high", "urgent")
    assert str(got["103"]["team_id"]) == w["team"] and got["101"]["team_id"] is None
    assert (
        got["101"]["tags"][0] == "imported-hubspot" and got["101"]["email"] == "priya@example.org"
    )
    messages = await sql(
        sm, w["ws"],
        "SELECT m.kind, m.body FROM hd_message m JOIN hd_ticket t ON t.id = m.ticket_id "
        "WHERE t.source_id = '101' AND m.kind <> 'system' ORDER BY m.created_at",
    )  # fmt: skip
    assert [m["kind"] for m in messages] == ["customer", "note", "agent"]
    files = await sql(sm, w["ws"], "SELECT name, size FROM hd_attachment")
    assert [(f["name"], f["size"]) for f in files] == [("label.pdf", len(b"%PDF-label"))]
    assert all(TOKEN not in str(x) for x in await sql(sm, w["ws"], "SELECT * FROM hd_message"))

    # Again (a finished run closes the connection, so connect again): nothing is made twice.
    conn2 = ok(
        await im.post(
            f"{I}/connections",
            headers=w["admin"],
            json={"source": "hubspot", "settings": {}, "credentials": {"token": TOKEN}},
        ),
        201,
    )
    again = ok(
        await im.post(
            f"{I}/connections/{conn2['id']}/run",
            headers=w["admin"],
            json={"kinds": ["contacts", "tickets"]},
        ),
        202,
    )
    await work(im, sm)
    assert (await run_state(im, w, again["id"]))["state"] == "done"
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket WHERE source = 'hubspot'")) == 3
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_attachment")) == 1
    assert {r.url.host for r in seen} <= {"api.hubapi.com", "cdn.hubspot.example"}


async def test_a_hubspot_sync_brings_a_change(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    use_fake_hubspot(im)
    w = await desk(im, sm)
    conn = ok(
        await im.post(
            f"{I}/connections",
            headers=w["admin"],
            json={"source": "hubspot", "settings": {}, "credentials": {"token": TOKEN}},
        ),
        201,
    )
    started = ok(
        await im.post(
            f"{I}/connections/{conn['id']}/run",
            headers=w["admin"],
            json={"kinds": ["tickets"], "sync": True},
        ),
        202,
    )
    await work(im, sm)
    assert (await run_state(im, w, started["id"]))["sync_on"] is True
    before = await sql(sm, w["ws"], "SELECT status FROM hd_ticket WHERE source_id = '101'")
    assert before[0]["status"] == "pending"
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)  # HubSpot: 101 moved to the Closed stage
    after = await sql(sm, w["ws"], "SELECT status FROM hd_ticket WHERE source_id = '101'")
    assert after[0]["status"] == "resolved"
    look = (await run_state(im, w, started["id"]))["counts"]["sync"]
    # A status-only change is applied; "updated" counts tickets that got new messages.
    assert look["created"] == 0 and look["new_messages"] == 0
    assert ok(await im.post(f"{I}/runs/{started['id']}/sync-stop", headers=w["admin"]))
