"""The mobile agent app's backend (md P8), with RLS on: the queue (mine first, then waiting
chats, never resolved, merged or someone else's, last message without notes, team scope),
and the one-tap reply (accepts a waiting chat first; a light agent's text becomes a note;
Idempotency-Key)."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.tickets import Channel
from cedesk.helpdesk.service.tickets import NewTicket, create_from_channel
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

Q = "/api/v1/me/queue"
T = "/api/v1/tickets"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    ids = {
        n: await add(client, token, f"{n.lower()}@example.com", r, n)
        for n, r in (("Neha", "agent"), ("Ravi", "agent"), ("Meena", "light_agent"))
    }
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    heads = {n: bearer(await sign_in(client, slug, f"{n.lower()}@example.com"))
             for n in ("Neha", "Ravi", "Meena")}  # fmt: skip
    return {"slug": slug, "h": bearer(token), "ids": ids, "as": heads,
            "ws": UUID(me["workspace"]["id"])}  # fmt: skip


async def ticket(client: AsyncClient, w: dict[str, Any], subject: str, who: str | None) -> str:
    body = {"subject": subject, "description": f"About {subject}",
            "contact": {"email": "priya@example.org", "name": "Priya"},
            "assignee_id": w["ids"][who] if who else None}  # fmt: skip
    return str(ok(await client.post(T, headers=w["h"], json=body), 201)["id"])


async def waiting_chat(sm: Sm, w: dict[str, Any]) -> str:
    view = await create_from_channel(sm, w["ws"], NewTicket(
        subject="Chat from the widget", contact_email="asha@example.org", contact_name="Asha",
        description="Is anyone there?", channel=Channel.chat, accepted=False,
        handover={"reason": "asked for a human"}))  # fmt: skip
    return str(view.ticket["id"])


async def test_the_queue(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    mine = await ticket(client, w, "Mine", "Neha")
    chat = await waiting_chat(sm, w)
    nobody = await ticket(client, w, "Nobody's", None)
    await ticket(client, w, "Ravi's", "Ravi")
    done = await ticket(client, w, "Done", "Neha")
    ok(await client.post(f"{T}/{done}/resolve", headers=w["h"]))
    ok(await client.post(f"{T}/{mine}/messages", headers=w["as"]["Neha"],
                         json={"kind": "note", "body": "internal only"}), 201)  # fmt: skip

    got = ok(await client.get(Q, headers=w["as"]["Neha"]))
    assert [i["id"] for i in got][:1] == [mine]
    assert {i["id"] for i in got} == {mine, chat, nobody}  # not Ravi's, not resolved
    by_id = {i["id"]: i for i in got}
    assert by_id[mine]["mine"] is True and by_id[nobody]["mine"] is False
    assert [i["id"] for i in got][1] == chat and by_id[chat]["waiting_to_accept"] is True
    assert (by_id[mine]["last_kind"], by_id[mine]["last_body"]) == ("customer", "About Mine")
    assert by_id[chat]["contact_name"] == "Asha"
    assert len(ok(await client.get(Q, headers=w["as"]["Neha"], params={"limit": 1}))) == 1

    async with workspace_session(sm, w["ws"]) as s:  # a merged ticket isn't in the queue
        await s.execute(text("UPDATE hd_ticket SET merged_into_id = :m WHERE id = :id"),
                        {"m": UUID(mine), "id": UUID(nobody)})  # fmt: skip
    assert nobody not in {i["id"] for i in ok(await client.get(Q, headers=w["as"]["Neha"]))}


async def test_a_team_only_role_sees_its_team(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    team = ok(
        await client.post(
            "/api/v1/teams", headers=w["h"], json={"name": "Billing", "auto_assign": False}
        ),
        201,
    )["id"]
    role = ok(await client.post("/api/v1/roles", headers=w["h"], json={
        "name": "Billing only", "scope": "team_only", "team_id": team}), 201)  # fmt: skip
    ok(await client.patch(f"/api/v1/members/{w['ids']['Ravi']}", headers=w["h"],
                          json={"role": "custom", "role_id": role["id"]}))  # fmt: skip
    ours = await ticket(client, w, "Billing question", None)
    ok(await client.patch(f"{T}/{ours}", headers=w["h"], json={"team_id": team}))
    await ticket(client, w, "Other team's", None)
    ravi = bearer(await sign_in(client, w["slug"], "ravi@example.com"))
    assert [i["id"] for i in ok(await client.get(Q, headers=ravi))] == [ours]


async def test_the_mobile_reply(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    chat = await waiting_chat(sm, w)
    url = f"{T}/{chat}/mobile-reply"
    got = ok(await client.post(url, headers=w["as"]["Neha"], json={"body": "Hi, I'm here"}), 201)
    assert (got["saved_as"], got["accepted"], got["message"]["kind"]) == ("reply", True, "agent")
    t = ok(await client.get(f"{T}/{chat}", headers=w["h"]))
    assert t["assignee_id"] == w["ids"]["Neha"] and t["accepted"] is True
    lines = [m["body"] for m in t["messages"] if m["kind"] == "system"]
    assert "Neha joined the chat (mobile)" in lines

    again = ok(await client.post(url, headers=w["as"]["Neha"], json={"body": "Still here"}), 201)
    assert again["accepted"] is False  # already accepted

    normal = await ticket(client, w, "Email question", "Neha")
    murl = f"{T}/{normal}/mobile-reply"
    note = ok(await client.post(murl, headers=w["as"]["Meena"], json={"body": "Seen it"}), 201)
    assert (note["saved_as"], note["message"]["kind"]) == ("note", "note")  # a light agent

    key = {"Idempotency-Key": "tap-1"}
    one = ok(
        await client.post(murl, headers={**w["as"]["Neha"], **key}, json={"body": "Done"}), 201
    )
    two = await client.post(murl, headers={**w["as"]["Neha"], **key}, json={"body": "Done"})
    assert two.json()["message"]["id"] == one["message"]["id"]  # a double tap sends once
    replies = [m for m in ok(await client.get(f"{T}/{normal}", headers=w["h"]))["messages"]
               if m["body"] == "Done"]  # fmt: skip
    assert len(replies) == 1
    r = await client.post(f"{T}/{UUID(int=6)}/mobile-reply", headers=w["as"]["Neha"],
                          json={"body": "x"})  # fmt: skip
    assert r.status_code == 404
