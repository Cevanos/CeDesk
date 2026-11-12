"""A Zoho Desk import end to end (md M1-M10 for Zoho), with RLS on: the real connector against
a recorded-shape Zoho (`tests/unit/test_import_zoho.py`'s fake), through the API and the
worker. Status types become statuses, threads and comments become messages, a file is copied,
articles come in, the secrets never show, and a second run makes nothing twice."""

from collections.abc import Mapping
from typing import Any

import httpx
from httpx import AsyncClient

from cedesk.helpdesk.service.imports import framework, zoho
from tests.db.helpers import Sm, ok
from tests.db.test_imports import SETTINGS, I, desk, im, run_state, sql, work
from tests.unit.test_import_zoho import REFRESH, SECRET, allow, route

__all__ = ["im"]  # the fixture, used by name

CREDENTIALS = {"client_id": "1000.CLIENT", "client_secret": SECRET, "refresh_token": REFRESH}


def use_fake_zoho(c: AsyncClient) -> None:
    async def build(name: str, settings: Mapping[str, Any], secret: str | None) -> Any:
        assert name == "zoho" and secret is not None and REFRESH in secret
        return zoho.make(
            dict(settings), CREDENTIALS, transport=httpx.MockTransport(route), guard=allow
        )

    app = c.desk_app  # type: ignore[attr-defined]
    app.state.importers = framework.make_importers(
        SETTINGS.secret_key.get_secret_value(), app.state.store, build=build
    )


async def test_a_zoho_desk_import_end_to_end(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    use_fake_zoho(im)
    w = await desk(im, sm)
    r = await im.post(
        f"{I}/connections",
        headers=w["admin"],
        json={"source": "zoho", "settings": {"region": "in"}, "credentials": CREDENTIALS},
    )
    conn = ok(r, 201)
    assert SECRET not in r.text and REFRESH not in r.text
    assert conn["label"] == "Acme Support (desk.zoho.in)"

    dry = ok(await im.post(f"{I}/connections/{conn['id']}/dry-run", headers=w["admin"], json={}))
    assert dry["will_create"]["tickets"] == 2 and dry["will_create"]["articles"] == 2
    assert any("aren't imported" in x for x in dry["warnings"])

    body = {"kinds": ["contacts", "tickets", "articles"], "team_map": {"d1": w["team"]}}
    started = ok(
        await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"], json=body), 202
    )
    await work(im, sm)
    run = await run_state(im, w, started["id"])
    assert run["state"] == "done", run
    rows = await sql(
        sm, w["ws"],
        "SELECT source_id, status, priority, team_id FROM hd_ticket WHERE source = 'zoho' "
        "ORDER BY source_id",
    )  # fmt: skip
    assert [(r["source_id"], r["status"], r["priority"]) for r in rows] == [
        ("z1", "pending", "high"), ("z2", "resolved", "medium")]  # fmt: skip
    assert str(rows[0]["team_id"]) == w["team"]
    kinds = await sql(
        sm, w["ws"],
        "SELECT m.kind FROM hd_message m JOIN hd_ticket t ON t.id = m.ticket_id "
        "WHERE t.source_id = 'z1' AND m.kind <> 'system' ORDER BY m.created_at",
    )  # fmt: skip
    assert [k["kind"] for k in kinds] == ["customer", "note", "note", "agent", "agent"]
    files = await sql(sm, w["ws"], "SELECT name, size FROM hd_attachment")
    assert [(f["name"], f["size"]) for f in files] == [("label.pdf", len(b"%PDF-label"))]
    articles = await sql(sm, w["ws"], "SELECT title FROM hd_article")
    assert [a["title"] for a in articles] == ["Refunds"]

    # A finished run closes the connection (its secrets are deleted): connect again.
    conn2 = ok(
        await im.post(
            f"{I}/connections",
            headers=w["admin"],
            json={"source": "zoho", "settings": {"region": "in"}, "credentials": CREDENTIALS},
        ),
        201,
    )
    again = ok(
        await im.post(f"{I}/connections/{conn2['id']}/run", headers=w["admin"], json=body), 202
    )
    await work(im, sm)
    assert (await run_state(im, w, again["id"]))["state"] == "done"
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket WHERE source = 'zoho'")) == 2
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_attachment")) == 1
    assert len(await sql(sm, w["ws"], "SELECT 1 FROM hd_article")) == 1
