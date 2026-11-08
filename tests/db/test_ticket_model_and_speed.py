"""Phase 1 data model (md 4.1 columns), search kept current by its trigger, and the ticket
list's sort indexes (md 2B performance)."""

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.domain.tickets import Sentiment
from cedesk.helpdesk.repo.tickets import _LIST
from cedesk.helpdesk.service import tickets
from cedesk.helpdesk.service.errors import Conflict, Invalid
from cedesk.helpdesk.service.tickets import NewTicket
from tests.db.helpers import Sm, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def admin_caller(client: AsyncClient, sm: Sm) -> tuple[Caller, dict[str, str]]:
    token = await sign_in(client, await new_workspace(sm), "admin@example.com")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))["member"]
    caller = Caller(UUID(token.split(".")[0]), UUID(me["id"]), Role.admin, uuid4())
    return caller, bearer(token)


def handover_ticket(**extra: Any) -> NewTicket:
    values: dict[str, Any] = {
        "subject": "Refund for torn shirt",
        "contact_email": "priya@example.com",
        "contact_name": "Priya Sharma",
        "conversation_id": "conv_123",
        "accepted": False,
        "handover": {"summary": "Torn seam, wants 1500 back", "reason": "Above the AI limit"},
        "topic": "Refund / return",
        "sentiment": Sentiment.negative,
        "language": "hi",
        "source": "cevanos",
        "source_id": "conv_123",
    }
    return NewTicket(**(values | extra))


async def test_handover_and_triage_columns(client: AsyncClient, app_sessionmaker: Sm) -> None:
    caller, h = await admin_caller(client, app_sessionmaker)
    view = await tickets.create_ticket(app_sessionmaker, caller, handover_ticket())
    tid = view.ticket["id"]

    t = ok(await client.get(f"{T}/{tid}", headers=h))
    assert t["accepted"] is False
    assert t["conversation_id"] == "conv_123"
    assert t["handover"]["reason"] == "Above the AI limit"
    assert (t["topic"], t["sentiment"], t["language"]) == ("Refund / return", "negative", "hi")
    assert (t["source"], t["source_id"]) == ("cevanos", "conv_123")
    assert t["parent_id"] is None and t["merged_into_id"] is None and t["child_count"] == 0
    row = ok(await client.get(f"{T}?q=torn", headers=h))["tickets"][0]
    assert row["accepted"] is False and row["language"] == "hi" and row["source"] == "cevanos"
    assert "handover" not in row  # the summary is on the ticket, not in every list row

    # An agent-made ticket: accepted, nothing from a handover or triage.
    plain = ok(await client.post(T, headers=h, json={"subject": "Hi", "contact": {
        "email": "a@b.test", "name": "A"}}), 201)  # fmt: skip
    assert plain["accepted"] is True
    assert [plain[k] for k in ("conversation_id", "handover", "topic", "sentiment", "language",
                               "source", "source_id")] == [None] * 7  # fmt: skip
    for message in plain["messages"]:
        assert message["email"] is None and message["channel_message_id"] is None


async def test_one_ticket_per_source_record(client: AsyncClient, app_sessionmaker: Sm) -> None:
    caller, h = await admin_caller(client, app_sessionmaker)
    await tickets.create_ticket(app_sessionmaker, caller, handover_ticket())
    with pytest.raises(Conflict, match="already exists for cevanos conv_123"):
        await tickets.create_ticket(app_sessionmaker, caller, handover_ticket(subject="Again"))
    # The failed create used no ticket number and left nothing behind.
    listed = ok(await client.get(T, headers=h))
    assert [t["label"] for t in listed["tickets"]] == ["T-1001"]
    second = await tickets.create_ticket(
        app_sessionmaker, caller, handover_ticket(source_id="conv_456")
    )
    assert second.ticket["number"] == 1002

    # Another workspace may hold the same source record.
    other, _ = await admin_caller(client, app_sessionmaker)
    await tickets.create_ticket(app_sessionmaker, other, handover_ticket())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"language": "Hindi"}, "language"),
        ({"source": "Cevanos!"}, "source"),
        ({"source": None}, "source_id needs a source"),
        ({"topic": "x" * 61}, "topic"),
        ({"conversation_id": ""}, "conversation_id"),
    ],
)
async def test_origin_values_are_checked(
    client: AsyncClient, app_sessionmaker: Sm, change: dict[str, Any], message: str
) -> None:
    caller, _ = await admin_caller(client, app_sessionmaker)
    with pytest.raises(Invalid, match=message):
        await tickets.create_ticket(app_sessionmaker, caller, handover_ticket(**change))


async def test_search_follows_every_edit(client: AsyncClient, app_sessionmaker: Sm) -> None:
    _, h = await admin_caller(client, app_sessionmaker)
    refund = ok(
        await client.post(
            "/api/v1/ticket-types",
            headers=h,
            json={
                "name": "Refund",
                "fields": [{"key": "order_id", "label": "Order", "kind": "text"}],
            },
        ),
        201,
    )
    new = {
        "subject": "Where is it",
        "contact": {"email": "ana@shop.test", "name": "Ana"},
        "type_id": refund["id"],
        "fields": {"order_id": "ZX-1"},
        "tags": ["first"],
    }
    t = ok(await client.post(T, headers=h, json=new), 201)

    async def found(q: str) -> bool:
        listed = ok(await client.get(f"{T}?q={q}", headers=h))["tickets"]
        return [x["id"] for x in listed] == [t["id"]]

    assert await found("zx-1") and await found("FIRST") and await found("where")
    ok(await client.patch(f"{T}/{t['id']}", headers=h, json={
        "subject": "Parcel lost", "fields": {"order_id": "QQ-9"},
        "add_tags": ["second"], "remove_tags": ["first"]}))  # fmt: skip
    assert await found("parcel") and await found("qq-9") and await found("second")
    assert not await found("zx-1") and not await found("first") and not await found("where")

    # The customer's name and email are searched live, so a rename is found at once.
    contact_id = t["contact"]["id"]
    ok(await client.patch(f"/api/v1/contacts/{contact_id}/profile", headers=h, json={
        "name": "Anabelle"}))  # fmt: skip
    assert await found("anabelle") and await found("ana@shop")


@pytest.mark.parametrize(
    ("sort", "index"),
    [
        ("priority", "hd_ticket_sort_priority"),
        ("newest", "hd_ticket_sort_created"),
        ("oldest", "hd_ticket_sort_created"),
        ("updated", "hd_ticket_sort_updated"),
    ],
)
async def test_each_sort_is_served_by_its_index(
    client: AsyncClient, app_sessionmaker: Sm, sort: str, index: str
) -> None:
    """The list orders by the indexed expressions themselves, so Postgres walks the index
    and stops after one page. If a query's keys stop matching migration 0009, the plan
    gets a Sort over every ticket instead and this test fails."""
    caller, _ = await admin_caller(client, app_sessionmaker)
    params: dict[str, Any] = {
        "me": caller.member_id, "status": None, "priority": None, "team": None,
        "contact": None, "scope": "everyone", "q_like": None, "q_number": None, "limit": 51,
    }  # fmt: skip
    keys = 4 if sort == "priority" else 2
    for first_page in (True, False):
        cursor = [None] * keys if first_page else ([0, 1, "1700000000.5", 7] if keys == 4
                                                   else ["1700000000.5", 7])  # fmt: skip
        async with workspace_session(app_sessionmaker, caller.workspace_id) as s:
            await s.execute(text("SET LOCAL enable_seqscan = off"))
            await s.execute(text("SET LOCAL enable_sort = off"))
            rows = await s.execute(
                text("EXPLAIN " + _LIST[sort]),
                params | {f"c{i}": v for i, v in enumerate(cursor, 1)},
            )
            plan = "\n".join(r[0] for r in rows)
        assert (
            f"Index Scan using {index}" in plan or f"Index Scan Backward using {index}" in plan
        ), plan
        assert "Sort" not in plan.replace("Sort Key", ""), plan
