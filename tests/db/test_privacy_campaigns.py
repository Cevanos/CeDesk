"""Privacy and campaigns (feature-gap plan phase 6): a customer's export holds the
campaign emails sent to them and their suppression entries; erasing them clears the
address and name from those emails but keeps their address on the suppression list as a
hash only, so they are never mailed again, even when they write in again later."""

import json

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service import campaigns
from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import sql
from tests.db.test_campaigns import CONFIG, SUP, FakeMailer, K, camp_desk, draft, token_of
from tests.db.test_contacts import customer
from tests.db.test_privacy import P, app_parts, client, store

pytestmark = pytest.mark.db
__all__ = ["app_parts", "client", "store"]  # fixtures


async def test_export_and_erase_cover_campaigns(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await camp_desk(client, sm)
    h, ana = w["h"], w["people"]["Ana"]
    c = await draft(client, w, track_opens=True)
    ok(await client.post(f"{K}/{c['id']}/send", headers=h, json={"confirm": True}))
    mailer = FakeMailer(w["domain"])
    await campaigns.run_due(sm, mailer, CONFIG)
    sent = next(e for e in mailer.sent if e.to == ["ana@shop.test"])
    ok(await client.post(f"/api/v1/public/unsubscribe/{token_of(sent)}"))

    made = ok(await client.post(f"{P}/export", headers=h, json={"contact_id": ana}), 201)
    data = json.loads((await client.get(f"{P}/requests/{made['id']}/file", headers=h)).content)
    [email] = data["campaign_emails"]
    assert (email["campaign"], email["email"], email["status"]) == (
        "November news", "ana@shop.test", "sent",
    )  # fmt: skip
    assert email["unsubscribed_at"] is not None
    [entry] = data["suppression_list"]
    assert (entry["email"], entry["reason"], entry["campaign"]) == (
        "ana@shop.test", "unsubscribed", "November news",
    )  # fmt: skip

    # A second campaign is queued for her when she is erased: it never goes.
    again = {"confirm_consent": True, "justification": "She asked again by phone today"}
    removed = await client.post(
        f"{SUP}/{entry_id(await suppressions(client, h))}/remove", headers=h, json=again
    )
    assert removed.status_code == 204
    nxt = await draft(client, w)
    ok(await client.post(f"{K}/{nxt['id']}/send", headers=h, json={"confirm": True}))
    erase = {"contact_id": ana, "confirm_email": "ana@shop.test"}
    done = ok(await client.post(f"{P}/erase", headers=h, json=erase), 201)
    assert done["counts"]["campaign_emails"] == 2 and done["counts"]["suppression_kept"] == 1
    rows = await sql(sm, w["ws"], "SELECT email, name, status, unsubscribe_hash, open_hash "
                     "FROM hd_campaign_recipient WHERE contact_id = :c ORDER BY created_at",
                     c=ana)  # fmt: skip
    assert [tuple(r) for r in rows] == [
        (None, "Erased customer", "sent", None, None),
        (None, "Erased customer", "skipped", None, None),
    ]  # fmt: skip
    # The old link now finds nothing (and still answers the same).
    assert (
        ok(await client.get(f"/api/v1/public/unsubscribe/{token_of(sent)}"))["workspace_name"]
        is None
    )
    # Her address stays on the list as a hash only, and can't be taken off.
    listed = ok(await client.get(SUP, headers=h, params={"reason": "erased"}))["entries"]
    assert [e["email"] for e in listed] == [None]
    assert ok(await client.get(SUP, headers=h, params={"email": "ana@shop.test"}))["entries"]
    r = await client.post(f"{SUP}/{listed[0]['id']}/remove", headers=h, json=again)
    assert r.status_code == 409
    # She writes in again as a new customer: no campaign goes to her.
    await customer(client, w, "Ana Again", "ana@shop.test")
    third = await draft(client, w)
    aud = ok(await client.get(f"{K}/{third['id']}/audience", headers=h))
    assert (aud["in_segment"], aud["suppressed"], aud["recipients"]) == (3, 1, 2)
    after = FakeMailer(w["domain"])
    await campaigns.run_due(sm, after, CONFIG)
    assert "ana@shop.test" not in [e.to[0] for e in after.sent]
    # The audit log names her by id only.
    entries = ok(await client.get("/api/v1/audit?area=campaigns&limit=200", headers=h))
    assert "ana@shop.test" not in json.dumps(entries)


async def suppressions(client: AsyncClient, h: dict[str, str]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = ok(await client.get(SUP, headers=h))["entries"]
    return found


def entry_id(entries: list[dict[str, object]]) -> str:
    [entry] = [e for e in entries if e["reason"] == "unsubscribed"]
    return str(entry["id"])
