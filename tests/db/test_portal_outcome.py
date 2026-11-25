"""Customer resolution portal (differentiator plan #9) with RLS on: a customer's ticket
shows who owns it (a first name or the team, never an email), what it waits on, and the
checked outcome from the resolution receipt (plan #5); the workspace can hide names;
nothing internal leaks; another customer's ticket stays 404.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, ok, sign_in
from tests.db.test_portal_tickets import P, T, customer, desk

pytestmark = pytest.mark.db

INTERNAL = {"assignee_name", "team_name", "receipt_pending", "receipt_result",
            "receipt_verified", "show_owner"}  # fmt: skip


async def portal_ticket(client: AsyncClient, me: dict[str, str], tid: str) -> dict[str, Any]:
    found: dict[str, Any] = ok(await client.get(f"{P}/tickets/{tid}", headers=me))
    listed = ok(await client.get(f"{P}/tickets", headers=me))["tickets"]
    row = next(t for t in listed if t["id"] == tid)
    for key in ("owner_first_name", "pending_step", "outcome"):
        assert row[key] == found[key], key  # the list and the ticket agree
    assert not INTERNAL & set(found) and not INTERNAL & set(row)
    return found


async def test_owner_pending_step_and_outcome(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    admin_token = await sign_in(client, w["slug"], "admin@example.com")
    priyanka = await add(client, admin_token, "priyanka@example.com", "agent", "Priyanka Rao")
    ok(await client.put("/api/v1/resolution-receipts", headers=w["admin"],
                        json={"mode": "optional"}))  # fmt: skip
    me = await customer(client, app_sessionmaker, w, "ana@example.org")
    tid = ok(await client.post(f"{P}/tickets", headers=me, json={"message": "Refund please"}),
             201)["id"]  # fmt: skip

    # Nobody yet.
    found = await portal_ticket(client, me, tid)
    assert (found["owner_first_name"], found["pending_step"], found["outcome"]) == (None,) * 3
    # A team (routing hands it to Neha), then someone else: first name only.
    ok(await client.patch(f"{T}/{tid}", headers=w["admin"], json={"team_id": w["team"]}))
    assert (await portal_ticket(client, me, tid))["owner_first_name"] == "Neha"
    ok(await client.patch(f"{T}/{tid}", headers=w["admin"], json={"assignee_id": None}))
    assert (await portal_ticket(client, me, tid))["owner_first_name"] == "Billing"
    ok(await client.patch(f"{T}/{tid}", headers=w["admin"], json={"assignee_id": priyanka}))
    assert (await portal_ticket(client, me, tid))["owner_first_name"] == "Priyanka"
    # Waiting on the customer.
    ok(await client.patch(f"{T}/{tid}", headers=w["admin"], json={"status": "pending"}))
    assert (await portal_ticket(client, me, tid))["pending_step"] == "Waiting on you"

    # Resolved with a receipt not yet checked: the pending item shows, the outcome doesn't.
    receipt = {"action_taken": "Refunded", "result": "Refund issued",
               "pending_item": "Bank settlement"}  # fmt: skip
    ok(await client.post(f"{T}/{tid}/resolve", headers=w["admin"],
                         json={"resolution": receipt}))  # fmt: skip
    found = await portal_ticket(client, me, tid)
    assert (found["pending_step"], found["outcome"]) == ("Bank settlement", None)
    # Checked by the agent: the outcome shows.
    ok(await client.patch(f"{T}/{tid}/resolution", headers=w["admin"], json={"verified": "agent"}))
    found = await portal_ticket(client, me, tid)
    assert (found["owner_first_name"], found["pending_step"], found["outcome"]) == (
        "Priyanka", "Bank settlement", "Refund issued",
    )  # fmt: skip

    # The workspace hides names: the team instead.
    settings = ok(await client.get("/api/v1/portal-settings", headers=w["admin"]))
    assert settings["show_owner"] is True
    body = {"allowed_domains": [], "csat_survey": True, "web_form": False, "show_owner": False}
    assert ok(await client.put("/api/v1/portal-settings", headers=w["admin"],
                               json=body))["show_owner"] is False  # fmt: skip
    assert (await portal_ticket(client, me, tid))["owner_first_name"] == "Billing"
    # Saved again without show_owner (an older screen): kept off (review fix).
    body.pop("show_owner")
    assert ok(await client.put("/api/v1/portal-settings", headers=w["admin"],
                               json=body))["show_owner"] is False  # fmt: skip
    audit = ok(await client.get("/api/v1/audit?area=portal", headers=w["admin"]))["entries"]
    assert "show_owner" in audit[0]["detail"]["changes"]

    # Reopened: an old receipt's pending item and outcome no longer show; nor after a
    # resolve without a new receipt (review fix).
    ok(await client.patch(f"{T}/{tid}", headers=w["admin"], json={"status": "open"}))
    found = await portal_ticket(client, me, tid)
    assert (found["pending_step"], found["outcome"]) == (None, None)
    ok(await client.post(f"{T}/{tid}/resolve", headers=w["admin"]))
    found = await portal_ticket(client, me, tid)
    assert (found["status"], found["pending_step"], found["outcome"]) == ("resolved", None, None)

    # Another customer still can't see it.
    other = await customer(client, app_sessionmaker, w, "bo@example.org")
    assert (await client.get(f"{P}/tickets/{tid}", headers=other)).status_code == 404
    assert ok(await client.get(f"{P}/tickets", headers=other))["tickets"] == []
