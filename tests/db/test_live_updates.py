"""Live updates (md C11, P7) with RLS on: events are written with the change they describe,
carry ids only, reach only the people who may see the ticket, and a stream ticket opens one
stream once.

The test database is shared and other features write events too (adding a member writes a
`security.alert` to the outbox, and every ticket change writes its own), so these tests ask
for the kinds and tickets they are about and never assert that a table is empty or an exact
list."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import stream as rules
from cedesk.helpdesk.repo import cevanos as cevanos_repo
from cedesk.helpdesk.repo import stream as repo
from cedesk.helpdesk.service import stream
from cedesk.helpdesk.service.errors import NotSignedIn
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def desk(c: AsyncClient, sm: Sm) -> dict[str, Any]:
    """An Admin, an agent (Neha) and two teams."""
    slug = await new_workspace(sm)
    token = await sign_in(c, slug, "admin@example.com")
    admin = bearer(token)
    neha = await add(c, token, "neha@example.com", "agent", "Neha")
    teams = {}
    for name in ("Billing", "Shipping"):
        made = await c.post("/api/v1/teams", headers=admin, json={"name": name, "member_ids": []})
        teams[name] = ok(made, 201)["id"]
    return {
        "slug": slug, "token": token, "admin": admin, "ws": UUID(token.split(".")[0]),
        "neha": neha, "teams": teams,
    }  # fmt: skip


async def make_ticket(c: AsyncClient, w: dict[str, Any], name: str, **extra: Any) -> str:
    body = {"subject": name, "contact": {"email": f"{name.lower()}@example.org", "name": name}}
    return str(ok(await c.post(T, headers=w["admin"], json={**body, **extra}), 201)["id"])


async def rows(sm: Sm, w: dict[str, Any], where: str = "true", **params: Any) -> list[Any]:
    """Events of this workspace matching `where` (our own fixed text), oldest first."""
    sql = (
        "SELECT seq, kind, ticket_id, team_id, target_member_id, target_contact_id, payload "
        "FROM hd_event_log WHERE " + where + " ORDER BY seq"
    )
    async with workspace_session(sm, w["ws"]) as s:
        result = await s.execute(text(sql), params)
        return list(result.mappings().all())


# Written with the change


async def test_a_new_ticket_publishes_its_ids_and_nothing_else(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ticket = await make_ticket(client, w, "Refundplease")
    made = await rows(app_sessionmaker, w, "kind = 'ticket.created' AND ticket_id = :t", t=ticket)
    assert len(made) == 1
    event = made[0]
    assert set(event["payload"]) == {"ticket_id", "number"}
    assert event["target_member_id"] is None and event["target_contact_id"] is None  # for agents
    assert "Refundplease" not in json.dumps(event["payload"])  # never the subject


async def test_a_note_is_for_agents_and_a_reply_is_for_the_customer_too(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    ticket = await make_ticket(client, w, "Parcel")
    url = f"{T}/{ticket}/messages"
    ok(await client.post(url, headers=w["admin"], json={"kind": "note", "body": "Internal"}), 201)
    ok(await client.post(url, headers=w["admin"], json={"kind": "reply", "body": "Hello"}), 201)

    async def seen(kind: str, *, customer: bool) -> int:
        found = await rows(
            sm, w,
            "kind = 'message.created' AND ticket_id = :t AND payload->>'message_kind' = :k "
            f"AND target_contact_id IS {'NOT NULL' if customer else 'NULL'}",
            t=ticket, k=kind,
        )  # fmt: skip
        return len(found)

    assert await seen("note", customer=False) == 1  # agents see a note...
    assert await seen("note", customer=True) == 0  # ...the customer never hears of it
    assert await seen("agent", customer=False) == 1
    assert await seen("agent", customer=True) == 1  # a reply is theirs too
    async with workspace_session(sm, w["ws"]) as s:
        contact = await s.scalar(
            text("SELECT contact_id FROM hd_ticket WHERE id = :t"), {"t": ticket}
        )
    mine = await rows(sm, w, "target_contact_id = :c AND ticket_id = :t", c=contact, t=ticket)
    assert mine and all(r["kind"] in rules.CUSTOMER_KINDS for r in mine)


async def test_members_and_security_alerts_are_never_published(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """`desk` added an agent, which writes a `security.alert` to the outbox for the Admins."""
    w = await desk(client, app_sessionmaker)
    await make_ticket(client, w, "Anything")
    kinds = {r["kind"] for r in await rows(app_sessionmaker, w)}
    assert kinds  # something was published for the ticket...
    assert kinds <= rules.AGENT_KINDS | rules.CUSTOMER_KINDS  # ...and only listed kinds
    assert not {k for k in kinds if k.startswith("security")}


async def test_a_notification_is_published_to_the_one_it_is_for(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ticket = await make_ticket(client, w, "Assigned", assignee_id=w["neha"])
    found = await rows(app_sessionmaker, w, "kind = 'notification' AND ticket_id = :t", t=ticket)
    assert [str(r["target_member_id"]) for r in found] == [w["neha"]]
    assert found[0]["payload"]["notification"] == "assigned"


async def test_an_event_kind_that_is_not_listed_cannot_be_written(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ticket = UUID(await make_ticket(client, w, "Listed"))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        with pytest.raises(ValueError, match="Not a published kind"):
            await repo.publish_for_ticket(s, ticket, agent=("security.alert", {}))
        with pytest.raises(ValueError, match="Not a published kind"):
            await repo.publish_for_ticket(s, ticket, agent=None, customer=("chat.closed", {}))


async def test_the_state_of_a_chat_is_published_when_it_changes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    ticket = UUID(await make_ticket(client, w, "Chat"))
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(
            text(
                "INSERT INTO hd_cevanos_chat (workspace_id, ticket_id, conversation_id) "
                "VALUES (:ws, :t, :c)"
            ),
            {"ws": w["ws"], "t": ticket, "c": f"conv-{ticket}"},
        )
        now = datetime.now(UTC)
        await cevanos_repo.update_chat(s, ticket, next_poll_at=now)  # not a state change
        await cevanos_repo.update_chat(s, ticket, taken_over_at=now)
        await cevanos_repo.update_chat(s, ticket, closed_at=now)
        await cevanos_repo.update_chat(s, ticket, closed_at=None)
    chat = [r["kind"] for r in await rows(sm, w, "kind LIKE 'chat.%' AND ticket_id = :t", t=ticket)]
    assert chat == ["chat.taken_over", "chat.closed", "chat.reopened"]


# Who may read what


async def test_a_role_limited_to_a_team_reads_that_teams_events_and_their_own_notifications(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    billing, shipping = w["teams"]["Billing"], w["teams"]["Shipping"]
    mine = await make_ticket(client, w, "Billingone", team_id=billing)
    theirs = await make_ticket(client, w, "Shippingone", team_id=shipping)
    told = await make_ticket(client, w, "Told", team_id=shipping, assignee_id=w["neha"])

    async def ticket_ids(team_only: UUID | None) -> set[str]:
        async with workspace_session(sm, w["ws"]) as s:
            found = await repo.read_agent(
                s, w["ws"], UUID(w["neha"]), team_only,
                after=0, floor=0, limit=500, overlap=False,
            )  # fmt: skip
        return {str(r["ticket_id"]) for r in found if r["ticket_id"]}

    everything = await ticket_ids(None)
    assert {mine, theirs, told} <= everything
    limited = await ticket_ids(UUID(billing))
    assert mine in limited  # their team's ticket
    assert theirs not in limited  # another team's: not even that it exists
    assert told in limited  # Neha was told about it: that notification is hers


async def test_a_customer_reads_only_their_own_tickets_events(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    ann, bob = await make_ticket(client, w, "Ann"), await make_ticket(client, w, "Bob")
    for t in (ann, bob):
        body = {"kind": "reply", "body": "Hello"}
        ok(await client.post(f"{T}/{t}/messages", headers=w["admin"], json=body), 201)
    async with workspace_session(sm, w["ws"]) as s:
        contact = await s.scalar(text("SELECT contact_id FROM hd_ticket WHERE id = :t"), {"t": ann})
        found = await repo.read_customer(
            s, w["ws"], contact, after=0, floor=0, limit=500, overlap=False
        )
    assert found and {str(r["ticket_id"]) for r in found} == {ann}


async def test_one_workspace_never_sees_anothers_events(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    one, two = await desk(client, sm), await desk(client, sm)
    await make_ticket(client, one, "Private")
    assert await rows(sm, one, "kind = 'ticket.created'")
    async with workspace_session(sm, two["ws"]) as s:  # the other workspace asks for it all
        stolen = await repo.read_agent(
            s, one["ws"], UUID(two["neha"]), None, after=0, floor=0, limit=500, overlap=False
        )
    assert list(stolen) == []  # row-level security, whatever workspace id is asked for


# Stream tickets


async def test_a_stream_ticket_opens_one_stream_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    made = ok(await client.post("/api/v1/stream/ticket", headers=w["admin"]), 201)
    assert made["expires_in"] == 60 and made["ticket"].startswith(f"{w['ws']}.")
    who = await stream.open_agent(sm, made["ticket"])
    assert who.kind == "agent" and who.workspace_id == w["ws"] and who.team_only is None
    with pytest.raises(NotSignedIn):  # a ticket works once
        await stream.open_agent(sm, made["ticket"])


async def test_a_ticket_that_is_wrong_expired_or_for_a_finished_sign_in_is_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    for bad in ("nonsense", f"{w['ws']}.nothing-like-a-ticket-at-all", "x" * 300):
        with pytest.raises(NotSignedIn):
            await stream.open_agent(sm, bad)

    expired = ok(await client.post("/api/v1/stream/ticket", headers=w["admin"]), 201)["ticket"]
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(
            text("UPDATE hd_stream_ticket SET expires_at = now() - interval '1 second'")
        )
    with pytest.raises(NotSignedIn):
        await stream.open_agent(sm, expired)

    signed_out = ok(await client.post("/api/v1/stream/ticket", headers=w["admin"]), 201)["ticket"]
    assert (await client.post("/api/v1/auth/sign-out", headers=w["admin"])).status_code == 204
    with pytest.raises(NotSignedIn):  # the sign-in behind it ended
        await stream.open_agent(sm, signed_out)


async def test_the_stream_route_refuses_a_bad_ticket_before_it_streams(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    r = await client.get("/api/v1/stream", params={"ticket": f"{w['ws']}.not-a-real-ticket-0000"})
    assert r.status_code == 401
    assert (await client.get("/api/v1/stream", params={"ticket": "short"})).status_code == 422
    assert (await client.post("/api/v1/stream/ticket")).status_code == 401  # needs a sign-in


# The stream, end to end


async def test_a_stream_sends_what_happened_after_the_last_id_it_was_given(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    first = await make_ticket(client, w, "Firstone")
    last_seen = (await rows(sm, w, "ticket_id = :t", t=first))[-1]["seq"]
    second = await make_ticket(client, w, "Secondone")
    ticket = ok(await client.post("/api/v1/stream/ticket", headers=w["admin"]), 201)["ticket"]
    who = await stream.open_agent(sm, ticket)

    frames = [f async for f in stream.run(sm, who, last_event_id=last_seen, max_polls=2, poll=0.01)]
    text_out = "".join(frames)
    assert frames[0] == "retry: 3000\n\n"
    assert second in text_out and first not in text_out  # only what came after
    assert "Secondone" not in text_out  # and ids only, never the subject
    assert "event: reset" not in text_out


async def test_a_last_event_id_that_was_pruned_gets_a_reset(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await make_ticket(client, w, "Anyone")
    ticket = ok(await client.post("/api/v1/stream/ticket", headers=w["admin"]), 201)["ticket"]
    who = await stream.open_agent(sm, ticket)
    frames = [f async for f in stream.run(sm, who, last_event_id=2_000_000_000, max_polls=1)]
    assert "event: reset\ndata: {}\n\n" in frames


# Keeping the log small


async def test_events_older_than_a_day_are_pruned_and_newer_ones_stay(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    old, new = await make_ticket(client, w, "Oldone"), await make_ticket(client, w, "Newone")
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(
            text("UPDATE hd_event_log SET created_at = :at WHERE ticket_id = :t"),
            {"at": datetime.now(UTC) - timedelta(hours=25), "t": old},
        )
    assert await stream.prune_everywhere(sm) >= 1
    assert await rows(sm, w, "ticket_id = :t", t=old) == []
    assert await rows(sm, w, "ticket_id = :t", t=new) != []
