"""Resolution receipts (differentiator plan #5) through the real API with RLS on:

- off by default: resolving works as before, and a receipt sent is refused;
- optional: a receipt is saved with the resolve, shown on the ticket, and the
  `ticket.resolved` event carries its verified state but none of its text;
- required (for every team or listed teams): resolving without one is refused through
  every door (resolve, a status change, a bulk change);
- the receipt can be changed for 7 days while resolved; a reopen and a new resolve
  replace it (audited without its text);
- Verify reads the Shopify order or Stripe payment (fake providers) and never writes;
- the Resolutions report counts and its CSV; privacy export holds it, erase blanks it.
"""

import json
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, events, lines, sql, ticket
from tests.db.test_apps import SHOP_TOKEN, STRIPE_KEY, install_fakes, put_app
from tests.db.test_apps import desk as shop_desk

pytestmark = pytest.mark.db

S = "/api/v1/resolution-receipts"
RECEIPT = {
    "request": "Refund for a broken mug",
    "action_taken": "Refunded the mug in full",
    "result": "Refund issued",
    "pending_item": "Bank settlement",
}


async def setting(client: AsyncClient, w: dict[str, Any], mode: str,
                  teams: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    body = {"mode": mode, "required_teams": teams or []}
    found: dict[str, Any] = ok(await client.put(S, headers=w["h"], json=body))
    return found


async def resolve(client: AsyncClient, w: dict[str, Any], tid: str,
                  receipt: dict[str, Any] | None = None) -> Any:  # fmt: skip
    body = {"resolution": receipt} if receipt is not None else None
    return await client.post(f"{T}/{tid}/resolve", headers=w["h"], json=body)


async def test_off_by_default_and_a_receipt_is_then_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    assert ok(await client.get(S, headers=w["h"]))["mode"] == "off"
    one = await ticket(client, w, "One", "Billing", "Neha")
    done = ok(await resolve(client, w, one))
    assert done["status"] == "resolved" and done["resolution"] is None
    assert (await client.get(f"{T}/{one}/resolution", headers=w["h"])).status_code == 404
    resolved = [e for e in await events(app_sessionmaker, w["ws"], one)
                if e["event"] == "ticket.resolved"]  # fmt: skip
    assert resolved[0]["payload"]["resolution"] is None

    two = await ticket(client, w, "Two", "Billing", "Neha")
    r = await resolve(client, w, two, RECEIPT)
    assert r.status_code == 422 and "off" in r.json()["error"]["message"]
    # Agents can't read or change the setting.
    neha = await as_(client, w, "Neha")
    assert (await client.get(S, headers=neha)).status_code == 403
    assert (await client.put(S, headers=neha, json={"mode": "optional"})).status_code == 403


async def test_optional_receipt_saved_shown_and_sent_without_its_text(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await setting(client, w, "optional")
    tid = await ticket(client, w, "Mug", "Billing", "Neha")
    msg = {"kind": "reply", "body": "Refunded, sorry about the mug."}
    ok(await client.post(f"{T}/{tid}/messages", headers=w["h"], json=msg), 201)
    done = ok(await resolve(client, w, tid, RECEIPT))
    receipt = done["resolution"]
    assert receipt["action_taken"] == "Refunded the mug in full"
    assert receipt["verified"] == "unverified" and receipt["verified_at"] is None
    assert receipt["customer_notified"] is True  # a reply went out just before
    assert receipt["pending_item"] == "Bank settlement"
    assert receipt["resolved_by_name"] == "Admin"
    assert ok(await client.get(f"{T}/{tid}/resolution", headers=w["h"])) == receipt
    assert "Resolution receipt added by Admin" in await lines(client, w, tid)
    [event] = [e for e in await events(app_sessionmaker, w["ws"], tid)
               if e["event"] == "ticket.resolved"]  # fmt: skip
    assert event["payload"]["resolution"] == {
        "verified": "unverified", "verified_at": None, "customer_notified": True,
        "pending": True,
    }  # fmt: skip
    assert "mug" not in json.dumps(event["payload"]["resolution"]).lower()

    # Without a reply, and without a receipt (optional): both fine.
    quiet = await ticket(client, w, "Quiet", "Shipping", "Ravi")
    told = ok(await resolve(client, w, quiet, {"action_taken": "Checked", "verified": "agent"}))
    assert told["resolution"]["customer_notified"] is False
    assert told["resolution"]["verified_at"] is not None
    bare = await ticket(client, w, "Bare", "Shipping", "Ravi")
    assert ok(await resolve(client, w, bare))["resolution"] is None

    # Bad receipts: no action, too long, an app check typed in by hand.
    nope = await ticket(client, w, "Nope", "Billing", "Neha")
    for bad in ({"action_taken": "   "}, {"action_taken": "x" * 501},
                {"action_taken": "Did it", "verified": "shopify"}):  # fmt: skip
        assert (await resolve(client, w, nope, bad)).status_code == 422
    # Already resolved: a receipt sent to resolve is refused (change it with PATCH).
    assert (await resolve(client, w, tid, RECEIPT)).status_code == 409


async def test_required_for_a_team_through_every_door(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    listed = {"mode": "optional", "required_teams": [w["teams"]["Billing"]]}
    bad = await client.put(S, headers=w["h"], json=listed)
    assert bad.status_code == 422  # teams only with required
    unknown = {"mode": "required", "required_teams": [str(UUID(int=1))]}
    assert (await client.put(S, headers=w["h"], json=unknown)).status_code == 422
    found = await setting(client, w, "required", [w["teams"]["Billing"]])
    assert found["required_teams"] == [w["teams"]["Billing"]]
    entry = (await audit(client, w, "resolutions"))[0]
    assert entry["action"] == "setting_changed"

    billing = [await ticket(client, w, f"B{n}", "Billing", "Neha") for n in range(3)]
    r = await resolve(client, w, billing[0])
    assert r.status_code == 422 and "receipt" in r.json()["error"]["message"]
    patch = {"status": "resolved"}
    assert (await client.patch(f"{T}/{billing[1]}", headers=w["h"], json=patch)).status_code == 422
    got = ok(await client.post(f"{T}/bulk", headers=w["h"], json={
        "ticket_ids": billing, "action": "set_status", "status": "resolved"}))  # fmt: skip
    assert (got["succeeded"], got["failed"]) == (0, 3)
    for tid in billing:
        assert ok(await client.get(f"{T}/{tid}", headers=w["h"]))["status"] == "open"
    assert ok(await resolve(client, w, billing[0], RECEIPT))["status"] == "resolved"

    # Shipping isn't listed: no receipt needed.
    shipping = await ticket(client, w, "S", "Shipping", "Ravi")
    assert ok(await resolve(client, w, shipping))["status"] == "resolved"
    # Required for every team.
    await setting(client, w, "required")
    other = await ticket(client, w, "S2", "Shipping", "Ravi")
    assert (await resolve(client, w, other)).status_code == 422


async def test_change_within_seven_days_and_a_new_resolve_replaces_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await setting(client, w, "optional")
    tid = await ticket(client, w, "Mug", "Billing", "Neha")
    ok(await resolve(client, w, tid, RECEIPT))
    url = f"{T}/{tid}/resolution"
    neha = await as_(client, w, "Neha")
    changed = ok(await client.patch(url, headers=neha,
                                    json={"pending_item": None, "verified": "agent"}))  # fmt: skip
    assert changed["pending_item"] is None and changed["verified"] == "agent"
    assert changed["verified_at"] is not None
    assert changed["action_taken"] == RECEIPT["action_taken"]  # not sent: kept
    assert (await client.patch(url, headers=neha, json={"verified": "stripe"})).status_code == 422
    assert (await client.patch(url, headers=neha, json={"action_taken": ""})).status_code == 422
    entry = (await audit(client, w, "resolutions"))[0]
    assert entry["action"] == "changed"
    assert entry["detail"] == {"fields": ["pending_item", "verified", "verified_at"]}

    # Older than 7 days: closed for changes.
    await sql(app_sessionmaker, w["ws"],
              "UPDATE hd_ticket SET resolved_at = now() - interval '8 days' WHERE id = :t",
              t=tid)  # fmt: skip
    assert (await client.patch(url, headers=neha, json={"result": "x"})).status_code == 409

    # Reopened: no changes; resolved again with a new receipt: it replaces the old one.
    ok(await client.patch(f"{T}/{tid}", headers=w["h"], json={"status": "open"}))
    assert (await client.patch(url, headers=neha, json={"result": "x"})).status_code == 409
    again = ok(await resolve(client, w, tid, {"action_taken": "Sent a new mug"}))
    assert again["resolution"]["action_taken"] == "Sent a new mug"
    assert again["resolution"]["pending_item"] is None
    assert len(await sql(app_sessionmaker, w["ws"], "SELECT 1 FROM hd_resolution")) == 1
    replaced = (await audit(client, w, "resolutions"))[0]
    assert replaced["action"] == "replaced" and replaced["detail"]["old"]["verified"] == "agent"
    assert "mug" not in json.dumps(replaced).lower()


async def test_verify_reads_the_order_or_payment_and_never_writes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    fake = install_fakes(client)
    w = await shop_desk(client, app_sessionmaker)
    ok(await put_app(client, w, "shopify", SHOP_TOKEN, {"shop": "acme"}))
    ok(await put_app(client, w, "stripe", STRIPE_KEY))
    await setting(client, w, "optional")
    tid = w["ticket"]
    url = f"{T}/{tid}/resolution/verify"
    # No receipt yet.
    assert (await client.post(url, headers=w["neha"], json={"order_id": "11"})).status_code == 404
    ok(await resolve(client, w, tid, {"action_taken": "Refunded order #1001"}))
    fake.seen.clear()

    found = ok(await client.post(url, headers=w["neha"], json={"order_id": "11"}))
    assert found["verified"] == "shopify" and found["verified_at"] is not None
    assert found["result"] == "Order #1001: payment paid, not fulfilled, total 1299.00 INR"
    paid = ok(await client.post(url, headers=w["neha"], json={"payment_id": "ch_1"}))
    assert paid["verified"] == "stripe"
    assert paid["result"] == "Payment of 499.00 INR: succeeded"
    assert {r.method for r in fake.seen} == {"GET"}  # read-only

    # Not one of the customer's orders (#1002 belongs to someone else); both or neither id.
    assert (await client.post(url, headers=w["neha"], json={"order_id": "12"})).status_code == 404
    both = {"order_id": "11", "payment_id": "ch_1"}
    assert (await client.post(url, headers=w["neha"], json=both)).status_code == 422
    assert (await client.post(url, headers=w["neha"], json={})).status_code == 422
    # The provider down: a plain 409, nothing changed.
    fake.down = True
    assert (await client.post(url, headers=w["neha"], json={"order_id": "11"})).status_code == 409
    assert ok(await client.get(f"{T}/{tid}/resolution", headers=w["h"]))["verified"] == "stripe"
    entry = (await audit(client, w, "resolutions"))[0]
    assert (entry["action"], entry["detail"]) == ("verified", {"verified": "stripe"})


async def test_report_counts_closed_with_receipt_and_verified(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await setting(client, w, "optional")
    a = await ticket(client, w, "A", "Billing", "Neha")
    b = await ticket(client, w, "B", "Billing", "Neha")
    c = await ticket(client, w, "C", "Shipping", "Ravi")
    await ticket(client, w, "Open", "Shipping", "Ravi")
    ok(await resolve(client, w, a, RECEIPT))
    ok(await resolve(client, w, b, {"action_taken": "Checked", "verified": "agent"}))
    ok(await resolve(client, w, c))

    report = ok(await client.get("/api/v1/reports/resolutions", headers=w["h"]))
    total = report["total"]
    assert (total["resolved"], total["with_receipt"], total["verified"]) == (3, 2, 1)
    assert (total["verified_agent"], total["pending"]) == (1, 1)
    teams = {t["name"]: t for t in report["teams"]}
    assert (teams["Billing"]["resolved"], teams["Billing"]["with_receipt"]) == (2, 2)
    assert (teams["Shipping"]["resolved"], teams["Shipping"]["with_receipt"]) == (1, 0)
    agents = {x["name"]: x["verified"] for x in report["agents"]}
    assert agents == {"Neha": 1, "Ravi": 0}
    only = ok(await client.get(f"/api/v1/reports/resolutions?team_id={w['teams']['Shipping']}",
                               headers=w["h"]))  # fmt: skip
    assert only["total"]["resolved"] == 1

    r = await client.get("/api/v1/reports/resolutions?format=csv", headers=w["h"])
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "With receipt" in r.text and "Billing" in r.text
    # Agents don't see reports; an unknown team is 404.
    neha = await as_(client, w, "Neha")
    assert (await client.get("/api/v1/reports/resolutions", headers=neha)).status_code == 403
    missing = f"/api/v1/reports/resolutions?team_id={UUID(int=1)}"
    assert (await client.get(missing, headers=w["h"])).status_code == 404


async def test_privacy_export_holds_the_receipt_and_erase_blanks_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await setting(client, w, "optional")
    tid = await ticket(client, w, "Ana", "Billing", "Neha")
    ok(await resolve(client, w, tid, RECEIPT))
    email = "ana@example.org"
    made = ok(await client.post("/api/v1/privacy/export", headers=w["h"],
                                json={"email": email}), 201)  # fmt: skip
    r = await client.get(f"/api/v1/privacy/requests/{made['id']}/file", headers=w["h"])
    [exported] = json.loads(r.content)["tickets"]
    assert exported["resolution"]["action_taken"] == RECEIPT["action_taken"]
    assert exported["resolution"]["pending_item"] == "Bank settlement"

    contact = ok(await client.get(f"{T}/{tid}", headers=w["h"]))["contact"]["id"]
    erase = {"contact_id": contact, "confirm_email": email}
    done = ok(await client.post("/api/v1/privacy/erase", headers=w["h"], json=erase), 201)
    assert done["counts"]["resolution_receipts"] == 1
    [row] = await sql(app_sessionmaker, w["ws"],
                      "SELECT request, action_taken, result, pending_item, verified "
                      "FROM hd_resolution")  # fmt: skip
    assert tuple(row) == (None, "(erased)", None, None, "unverified")
