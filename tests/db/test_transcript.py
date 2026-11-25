"""Email transcript (feature-gap plan 1.6) with RLS on: the customer's view of the
conversation (never notes, system lines or side conversations) is queued from the
support address, recorded on the ticket and in the audit log without any text, refused
where it can't go, rate-limited, and never reachable from another workspace."""

import json
from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, lines, sql

pytestmark = pytest.mark.db


def X(t: str) -> str:
    return f"{T}/{t}/transcript"


async def setup(client: AsyncClient, sm: Sm, *, address: bool = True) -> dict[str, Any]:
    w = await desk(client, sm)
    if address:
        ok(await client.post("/api/v1/email-addresses", headers=w["h"], json={
            "address": f"help@{w['slug']}.test", "name": "Acme help"}), 201)  # fmt: skip
    body = {"subject": "Torn shirt", "channel": "email", "assignee_id": w["ids"]["Neha"],
            "contact": {"email": "priya@example.com", "name": "Priya Sharma"},
            "description": "PUBLIC-customer: it arrived torn."}  # fmt: skip
    w["t"] = ok(await client.post(T, headers=w["h"], json=body), 201)["id"]
    w["neha"] = await as_(client, w, "Neha")
    msgs = f"{T}/{w['t']}/messages"
    ok(await client.post(msgs, headers=w["neha"],
                         json={"kind": "reply", "body": "PUBLIC-reply: sorry!"}), 201)  # fmt: skip
    ok(await client.post(msgs, headers=w["neha"],
                         json={"kind": "note", "body": "SECRET-note: refund it"}), 201)  # fmt: skip
    return w


async def outgoing(sm: Sm, w: dict[str, Any]) -> list[Any]:
    rows = await sql(sm, w["ws"],
                     "SELECT to_addresses, subject, body_text, sensitive, from_address "
                     "FROM hd_email_out WHERE ticket_id = :t "
                     "AND subject LIKE 'Conversation transcript%' "
                     "ORDER BY created_at", t=w["t"])  # fmt: skip
    return list(rows)


async def test_the_customer_gets_only_what_they_saw(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    ok(
        await client.post(
            f"{T}/{w['t']}/side-conversations",
            headers=w["neha"],
            json={"to": ["warehouse@supplier.example"], "body": "SECRET-side: check stock"},
        ),
        201,
    )
    got = ok(await client.post(X(w["t"]), headers=w["neha"], json={}), 202)
    assert got == {"to": "priya@example.com", "to_customer": True, "messages": 2}
    [(to, subject, body, sensitive, sender)] = await outgoing(app_sessionmaker, w)
    assert to == ["priya@example.com"] and sensitive is True
    assert sender == f"help@{w['slug']}.test"
    assert "Torn shirt" in subject and "T-" in subject
    assert "PUBLIC-customer" in body and "PUBLIC-reply" in body
    assert body.index("PUBLIC-customer") < body.index("PUBLIC-reply")
    assert "SECRET" not in body and "Ticket created" not in body
    assert "Transcript emailed to the customer by Neha" in await lines(client, w, w["t"])
    entry = (await audit(client, w, "tickets"))[0]
    assert entry["action"] == "transcript_sent" and entry["actor_name"] == "Neha"
    assert entry["detail"] == {"to": "customer", "domain": None, "messages": 2}
    assert "PUBLIC" not in json.dumps(entry) and "SECRET" not in json.dumps(entry)


async def test_to_another_address(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    got = ok(await client.post(X(w["t"]), headers=w["neha"],
                               json={"to": " Boss@Partner.Example "}), 202)  # fmt: skip
    assert got["to"] == "boss@partner.example" and got["to_customer"] is False
    entry = (await audit(client, w, "tickets"))[0]
    assert entry["detail"] == {"to": "other", "domain": "partner.example", "messages": 2}
    assert "Transcript emailed to boss@partner.example by Neha" in await lines(client, w, w["t"])


async def test_what_is_refused(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    meena = await as_(client, w, "Meena")
    assert (await client.post(X(w["t"]), headers=meena, json={})).status_code == 403
    assert (await client.post(X(w["t"]), headers=them["h"], json={})).status_code == 404
    r = await client.post(X(w["t"]), headers=w["neha"], json={"to": f"help@{w['slug']}.test"})
    assert r.status_code == 422  # a support address would loop back in
    r = await client.post(X(w["t"]), headers=w["neha"], json={"to": "not-an-address"})
    assert r.status_code == 422
    # A chat customer has no real address.
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_contact SET email = 'c-1@chat.invalid' "
              "WHERE email = 'priya@example.com'")  # fmt: skip
    assert (await client.post(X(w["t"]), headers=w["neha"], json={})).status_code == 422
    # A ticket nobody wrote on yet.
    empty = ok(
        await client.post(
            T,
            headers=w["h"],
            json={"subject": "Empty", "contact": {"email": "e@example.org", "name": "E"}},
        ),
        201,
    )["id"]
    assert (await client.post(X(empty), headers=w["h"], json={})).status_code == 409
    assert await outgoing(app_sessionmaker, w) == []


async def test_no_support_address_is_a_409(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker, address=False)
    r = await client.post(X(w["t"]), headers=w["neha"], json={})
    assert r.status_code == 409 and "support email address" in r.json()["error"]["message"]


async def test_the_rate_limit(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    codes = [(await client.post(X(w["t"]), headers=w["neha"], json={})).status_code
             for _ in range(11)]  # fmt: skip
    assert codes[:10] == [202] * 10 and codes[10] == 429
    assert len(await outgoing(app_sessionmaker, w)) == 10


async def test_refused_requests_use_none_of_the_limit(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The limit was spent before the checks, so refused tries (here a support address,
    422) used it up: now only a transcript that goes out counts, as bulk changes."""
    w = await setup(client, app_sessionmaker)
    for _ in range(12):
        r = await client.post(X(w["t"]), headers=w["neha"], json={"to": f"help@{w['slug']}.test"})
        assert r.status_code == 422
    codes = [(await client.post(X(w["t"]), headers=w["neha"], json={})).status_code
             for _ in range(11)]  # fmt: skip
    assert codes[:10] == [202] * 10 and codes[10] == 429
