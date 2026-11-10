"""Outbox delivery (md 3.2 step 7) with RLS on: handlers get the events they want once each,
one ticket's events in order, failures retry with backoff, and after the last try the
event is listed for Admins, who can send it again.

The test database is shared, so handlers here only act on their own workspace's events
(others' are accepted and ignored) and each test makes its events due by hand."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.delivery import MAX_ATTEMPTS
from cedesk.helpdesk.service.delivery import Event, HandlerError, deliver_due
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


class Recorder:
    def __init__(self, ws: UUID, name: str = "rec", events: frozenset[str] | None = None,
                 fail: dict[str, HandlerError | Exception] | None = None) -> None:  # fmt: skip
        self.ws, self.name = ws, name
        self.events = events or frozenset({"ticket.created", "message.created",
                                           "ticket.updated", "ticket.resolved"})  # fmt: skip
        self.fail = fail or {}  # subject -> error, for tickets whose subject matches
        self.got: list[tuple[str, str, str]] = []  # (event, subject, key)

    async def handle(self, event: Event, effect_key: str) -> None:
        if event.workspace_id != self.ws:
            return
        subject = event.payload["ticket"]["subject"]
        if subject in self.fail:
            raise self.fail[subject]
        self.got.append((event.event, subject, effect_key))


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    await add(client, token, "neha@example.com", "agent", "Neha")
    return {"admin": bearer(token), "ws": UUID(token.split(".")[0]),
            "neha": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def ticket(client: AsyncClient, w: dict[str, Any], subject: str) -> str:
    body = {"subject": subject, "contact": {"email": "ana@shop.test", "name": "Ana"}}
    return str(ok(await client.post(T, headers=w["admin"], json=body), 201)["id"])


async def due_now(sm: Sm, ws: UUID) -> None:
    async with workspace_session(sm, ws) as s:
        await s.execute(text("UPDATE hd_outbox SET next_attempt_at = now() "
                             "WHERE published_at IS NULL AND failed_at IS NULL"))  # fmt: skip


async def rows(sm: Sm, ws: UUID) -> list[Any]:
    async with workspace_session(sm, ws) as s:
        sql = (
            "SELECT id, event, payload->'ticket'->>'subject' AS subject, attempts, "
            "published_at, failed_at, last_error FROM hd_outbox ORDER BY seq"
        )
        result = await s.execute(text(sql))
        return list(result.mappings().all())


async def test_each_handler_gets_what_it_wants_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Order late")
    ok(await client.post(f"{T}/{t}/messages", headers=w["admin"],
                         json={"kind": "reply", "body": "On its way"}), 201)  # fmt: skip
    created = Recorder(w["ws"], "created-only", frozenset({"ticket.created"}))
    everything = Recorder(w["ws"])
    await deliver_due(app_sessionmaker, [created, everything])
    assert [e for e, _, _ in created.got] == ["ticket.created"]
    assert [e for e, _, _ in everything.got] == ["ticket.created", "message.created"]
    first = (await rows(app_sessionmaker, w["ws"]))[0]
    assert created.got[0][2] == f"{first['id']}:created-only"  # the effect's key
    assert all(r["published_at"] is not None for r in await rows(app_sessionmaker, w["ws"]))

    await deliver_due(app_sessionmaker, [created, everything])  # nothing again
    assert len(everything.got) == 2


async def test_without_handlers_events_are_marked_delivered(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "Hello")
    result = await deliver_due(app_sessionmaker, [])
    assert result.delivered >= 1
    assert all(r["published_at"] is not None for r in await rows(app_sessionmaker, w["ws"]))


async def test_a_failure_retries_later_and_holds_back_only_its_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    stuck = await ticket(client, w, "Stuck")
    ok(await client.post(f"{T}/{stuck}/messages", headers=w["admin"],
                         json={"kind": "note", "body": "later"}), 201)  # fmt: skip
    await ticket(client, w, "Fine")
    rec = Recorder(w["ws"], fail={"Stuck": HandlerError("Cevanos said 503")})
    await deliver_due(app_sessionmaker, [rec])
    assert [s for _, s, _ in rec.got] == ["Fine"]  # the other ticket went
    created, note = [r for r in await rows(app_sessionmaker, w["ws"]) if r["subject"] == "Stuck"]
    assert created["attempts"] == 1 and created["last_error"] == "rec: Cevanos said 503"
    assert created["published_at"] is None and created["failed_at"] is None
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        sql = "SELECT extract(epoch FROM next_attempt_at - now()) FROM hd_outbox WHERE id = :id"
        wait = await s.scalar(text(sql), {"id": created["id"]})
    assert 50 < float(wait) <= 60  # 1 minute after the first failure
    assert note["attempts"] == 0  # waits behind it, no try used

    # It works now: both go, in order.
    rec.fail.clear()
    await due_now(app_sessionmaker, w["ws"])
    await deliver_due(app_sessionmaker, [rec])
    assert [(e, s) for e, s, _ in rec.got][1:] == [
        ("ticket.created", "Stuck"), ("message.created", "Stuck"),
    ]  # fmt: skip


async def test_a_handler_that_worked_is_not_repeated(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "Two steps")
    first = Recorder(w["ws"], "first")
    second = Recorder(w["ws"], "second", fail={"Two steps": RuntimeError("bug")})
    await deliver_due(app_sessionmaker, [first, second])
    [row] = await rows(app_sessionmaker, w["ws"])
    assert row["last_error"] == "second: RuntimeError"  # a crash is retried too
    second.fail.clear()
    await due_now(app_sessionmaker, w["ws"])
    await deliver_due(app_sessionmaker, [first, second])
    assert len(first.got) == 1 and len(second.got) == 1


async def test_failed_events_are_listed_and_can_be_sent_again(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "Refused")
    await ticket(client, w, "Flaky")
    rec = Recorder(w["ws"], fail={
        "Refused": HandlerError("no such chat", permanent=True),
        "Flaky": HandlerError("timeout"),
    })  # fmt: skip
    await deliver_due(app_sessionmaker, [rec])
    async with workspace_session(app_sessionmaker, w["ws"]) as s:  # the 6th try is next
        await s.execute(text("UPDATE hd_outbox SET attempts = :n, next_attempt_at = now() "
                             "WHERE payload->'ticket'->>'subject' = 'Flaky'"),
                        {"n": MAX_ATTEMPTS - 1})  # fmt: skip
    await deliver_due(app_sessionmaker, [rec])

    assert (await client.get("/api/v1/outbox/failed", headers=w["neha"])).status_code == 403
    listed = ok(await client.get("/api/v1/outbox/failed", headers=w["admin"]))["events"]
    assert {(e["event"], e["last_error"]) for e in listed} == {
        ("ticket.created", "rec: no such chat"), ("ticket.created", "rec: timeout"),
    }  # fmt: skip
    refused = next(e for e in listed if e["last_error"] == "rec: no such chat")
    assert refused["attempts"] == 1  # permanent: no retries

    rec.fail.clear()
    r = await client.post(f"/api/v1/outbox/{refused['id']}/retry", headers=w["admin"])
    assert r.status_code == 204
    await deliver_due(app_sessionmaker, [rec])
    assert [s for _, s, _ in rec.got] == ["Refused"]
    left = ok(await client.get("/api/v1/outbox/failed", headers=w["admin"]))["events"]
    assert [e["last_error"] for e in left] == ["rec: timeout"]
    again = await client.post(f"/api/v1/outbox/{refused['id']}/retry", headers=w["admin"])
    assert again.status_code == 404  # not failed any more
