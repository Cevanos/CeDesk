"""Ticket followers (feature-gap plan 1.5) with RLS on: follow and unfollow (audited),
automatic follow on assignment and @mention, notifications for public replies and status
changes (never for your own change, never twice for the assignee), the rights, and
workspace isolation."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service import customer_replies
from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, bell, desk, sql, ticket

pytestmark = pytest.mark.db


def F(t: str) -> str:
    return f"{T}/{t}/followers"


async def names(client: AsyncClient, h: dict[str, str], t: str) -> list[str]:
    return [f["name"] for f in ok(await client.get(F(t), headers=h))["followers"]]


async def test_follow_unfollow_and_automatic_follows(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    assert await names(client, w["h"], t) == ["Neha"]  # assigned: follows
    meena = await as_(client, w, "Meena")  # a light agent may follow
    got = ok(await client.post(F(t), headers=meena, json={"member_id": w["ids"]["Meena"]}))
    assert got["following"] is True and [f["name"] for f in got["followers"]] == ["Neha", "Meena"]
    again = ok(await client.post(F(t), headers=meena, json={"member_id": w["ids"]["Meena"]}))
    assert len(again["followers"]) == 2  # no change
    neha = await as_(client, w, "Neha")
    note = {"kind": "note", "body": "@Ravi look", "mention_ids": [w["ids"]["Ravi"]]}
    ok(await client.post(f"{T}/{t}/messages", headers=neha, json=note), 201)
    assert await names(client, w["h"], t) == ["Neha", "Meena", "Ravi"]  # mentioned: follows
    got = ok(await client.delete(f"{F(t)}/{w['ids']['Meena']}", headers=meena))
    assert got["following"] is False and "Meena" not in [f["name"] for f in got["followers"]]
    assert (await client.delete(f"{F(t)}/{w['ids']['Meena']}", headers=meena)).status_code == 404
    entries = [(e["action"], e["detail"]["member"]) for e in await audit(client, w, "tickets")]
    assert entries[:2] == [("follower_removed", "Meena"), ("follower_added", "Meena")]


async def test_who_may_add_whom(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    meena = await as_(client, w, "Meena")
    r = await client.post(F(t), headers=meena, json={"member_id": w["ids"]["Ravi"]})
    assert r.status_code == 403  # a light agent can't change the ticket
    r = await client.delete(f"{F(t)}/{w['ids']['Neha']}", headers=meena)
    assert r.status_code == 403
    neha = await as_(client, w, "Neha")
    ok(await client.post(F(t), headers=neha, json={"member_id": w["ids"]["Sam"]}))
    got = ok(await client.get(F(t), headers=neha))["followers"]
    assert {f["name"]: f["added_by_name"] for f in got} == {"Neha": None, "Sam": "Neha"}
    r = await client.post(F(t), headers=neha, json={"member_id": str(uuid4())})
    assert r.status_code == 422
    await sql(app_sessionmaker, w["ws"], "UPDATE member SET is_active = false WHERE id = :m",
              m=w["ids"]["Ravi"])  # fmt: skip
    r = await client.post(F(t), headers=neha, json={"member_id": w["ids"]["Ravi"]})
    assert r.status_code == 422 and "deactivated" in r.json()["error"]["message"]


async def test_followers_hear_replies_and_status_changes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    number = ok(await client.get(f"{T}/{t}", headers=w["h"]))["number"]
    sam = await as_(client, w, "Sam")
    ok(await client.post(F(t), headers=sam, json={"member_id": w["ids"]["Sam"]}))
    neha = await as_(client, w, "Neha")

    ok(await client.post(f"{T}/{t}/messages", headers=neha,
                         json={"kind": "reply", "body": "On it"}), 201)  # fmt: skip
    ok(await client.post(f"{T}/{t}/messages", headers=neha,
                         json={"kind": "note", "body": "internal"}), 201)  # fmt: skip
    ok(await client.patch(f"{T}/{t}", headers=neha, json={"status": "pending"}))
    ok(await client.patch(f"{T}/{t}", headers=neha, json={"priority": "high"}))  # not status
    await customer_replies.customer_reply(app_sessionmaker, w["ws"], UUID(t), "Thanks, any news?")

    label = f"T-{number}"
    assert await bell(client, w, "Sam", "following") == [
        f"Refund replied on {label}: Refund",
        f"{label} is now Pending (Neha): Refund",
        f"Neha replied on {label}: Refund",
    ]
    # Neha made those changes, and hears of the customer once (as the assignee).
    assert await bell(client, w, "Neha", "following") == []
    assert await bell(client, w, "Neha", "customer_reply") == [f"Refund replied on {label}: Refund"]


async def test_another_workspace_cant_see_or_change_followers(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    theirs = await ticket(client, them, "Theirs", "Billing", "Neha")
    assert (await client.get(F(t), headers=them["h"])).status_code == 404
    r = await client.post(F(t), headers=them["h"], json={"member_id": them["ids"]["Sam"]})
    assert r.status_code == 404
    r = await client.delete(f"{F(t)}/{w['ids']['Neha']}", headers=them["h"])
    assert r.status_code == 404
    # Their member on our ticket: not a member here.
    r = await client.post(F(t), headers=w["h"], json={"member_id": them["ids"]["Sam"]})
    assert r.status_code == 422
    assert await names(client, w["h"], t) == ["Neha"]
    assert await names(client, them["h"], theirs) == ["Neha"]
