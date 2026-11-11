"""Triage and redaction (md I1-I5, W6) with RLS on: every new ticket and every customer
reply gets a topic, mood and language before the rules run; card numbers never reach
the database; the Admin edits the keywords and the redaction switch; Hindi tickets go
first to agents with the Hindi skill."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.tickets import Channel
from cedesk.helpdesk.repo import cevanos as cevanos_repo
from cedesk.helpdesk.service import customer_replies, triage
from cedesk.helpdesk.service.tickets import ChatLine, NewTicket, create_from_channel
from tests.db.helpers import Sm, add, bearer, ok, sign_in
from tests.db.test_rules_api import desk, lines

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
S = "/api/v1/triage-settings"
CARD = "4111 1111 1111 1111"
REMOVED = "[card number removed]"


def chips(t: dict[str, Any]) -> tuple[str, str, str]:
    return t["topic"], t["sentiment"], t["language"]


async def from_customer(sm: Sm, w: dict[str, Any], **kw: Any) -> Any:
    new = NewTicket(**{"contact_email": "priya@example.org", "contact_name": "Priya",
                       "channel": Channel.web} | kw)  # fmt: skip
    return (await create_from_channel(sm, w["ws"], new)).ticket


async def stored_text(sm: Sm, w: dict[str, Any], ticket_id: Any) -> str:
    """Everything saved on the ticket: subject, fields, handover, every message, the
    email headers."""
    async with workspace_session(sm, w["ws"]) as s:
        found = await s.execute(text(
            "SELECT t.subject || ' ' || t.fields::text || ' ' || "
            "coalesce(t.handover::text, '') || ' ' || coalesce(string_agg(m.body || ' ' || "
            "coalesce(m.email::text, ''), ' '), '') FROM hd_ticket t "
            "LEFT JOIN hd_message m ON m.ticket_id = t.id WHERE t.id = :id GROUP BY t.id"),
            {"id": ticket_id})  # fmt: skip
    return str(found.scalar_one())


async def test_a_new_ticket_is_triaged_before_the_rules_run(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    upset = await from_customer(app_sessionmaker, w, subject="Mixer",
                                description="Worst service ever, I am not happy")  # fmt: skip
    seen = ok(await client.get(f"{T}/{upset['id']}", headers=w["h"]))
    assert chips(seen) == ("General question", "negative", "en")
    # The mock's rule 6 reads the mood at create time.
    assert "upset" in seen["tags"] and seen["priority"] == "high"
    assert "Rule applied: Upset customer goes first → Priority High · Tag upset" in lines(seen)

    hindi = await from_customer(app_sessionmaker, w, subject="पैसा", description="मेरा रिफंड कब आएगा")
    assert chips(hindi) == ("Refund / return", "neutral", "hi")
    chat = await from_customer(app_sessionmaker, w, subject="Chat with customer", history=[
        ChatLine("customer", "mera order kab aayega bhai", None, None),
        ChatLine("ai", "Your order is late, sorry, refund maybe", "AI", None)])  # fmt: skip
    # Only the customer's words count: the AI's "late" and "refund" don't make a topic.
    assert chips(chat) == ("General question", "neutral", "hi")

    # What the channel already knew wins (an import's language, Cevanos's mood).
    given = await from_customer(app_sessionmaker, w, subject="Refund", language="ta")
    assert given["language"] == "ta" and given["topic"] == "Refund / return"
    # An agent logging a phone call: triage reads the subject and description too.
    body = {"subject": "Called about tracking", "contact": {"email": "a@b.co", "name": "A"},
            "channel": "phone"}  # fmt: skip
    assert chips(ok(await client.post(T, headers=w["h"], json=body), 201)) == (
        "Delivery", "neutral", "en")  # fmt: skip


async def test_every_customer_reply_triages_again(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await from_customer(app_sessionmaker, w, subject="Hello", description="Quick question")
    assert chips(t) == ("General question", "neutral", "en")
    await customer_replies.customer_reply(
        app_sessionmaker, w["ws"], t["id"], "The app crashes on login, terrible"
    )
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert chips(seen) == ("App problem", "negative", "en")
    # The mood is the newest message's; the topic still reads everything they wrote.
    await customer_replies.customer_reply(app_sessionmaker, w["ws"], t["id"], "ok it works, thanks")
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert chips(seen) == ("App problem", "positive", "en")
    # Digits only: the language stays what it was.
    await customer_replies.customer_reply(app_sessionmaker, w["ws"], t["id"], "12345")
    assert ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))["language"] == "en"


async def test_card_numbers_never_reach_the_database(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await from_customer(
        app_sessionmaker, w, subject=f"Charged twice on {CARD}",
        description=f"My card {CARD.replace(' ', '-')} was charged. CVV 123",
        first_email={"subject": f"Charged twice on {CARD}", "to": ["support@x.test"]},
        history=[ChatLine("customer", "also 5500 0000 0000 0004", None, None),
                 ChatLine("ai", "Never share 4111111111111111", "AI", None)],
        handover={"reason": f"Customer wants a refund to {CARD}", "channel_kind": "web"},
    )  # fmt: skip
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert seen["subject"] == f"Charged twice on {REMOVED}"
    assert seen["messages"][0]["body"] == f"My card {REMOVED} was charged. CVV 123"
    assert seen["messages"][0]["email"]["subject"] == f"Charged twice on {REMOVED}"
    # 4111... in the subject, the message and the email subject, and 5500... in the chat.
    assert "2 card numbers removed from the customer's message" in lines(seen)
    assert seen["topic"] == "Billing"
    # A bot that repeats the number, and the handover's reason, are cleaned too.
    assert f"Never share {REMOVED}" in [m["body"] for m in seen["messages"]]

    # An email with the same card in its subject and text: one number, said once.
    twice = await from_customer(app_sessionmaker, w, subject=f"Card {CARD}",
                                description=f"It is {CARD}", channel=Channel.email)  # fmt: skip
    twice_seen = ok(await client.get(f"{T}/{twice['id']}", headers=w["h"]))
    assert "1 card number removed from the customer's message" in lines(twice_seen)

    r = await customer_replies.customer_reply(
        app_sessionmaker, w["ws"], t["id"], f"New card {CARD}, thanks",
        email={"subject": f"Re: {CARD}"},
    )  # fmt: skip
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    reply = next(m for m in seen["messages"] if m["id"] == str(r.message_id))
    assert reply["body"] == f"New card {REMOVED}, thanks"
    assert reply["email"]["subject"] == f"Re: {REMOVED}"
    assert lines(seen).count("1 card number removed from the customer's message") == 1
    stored = await stored_text(app_sessionmaker, w, t["id"])
    assert "4111 1111" not in stored and "4111-1111" not in stored and "5500 0000" not in stored
    assert "4111111111111111" not in stored and f"refund to {REMOVED}" in stored


async def test_the_admin_edits_keywords_and_redaction(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert (await client.get(S, headers=neha)).status_code == 403
    first = ok(await client.get(S, headers=w["h"]))
    assert first["keywords_are_default"] and first["redact_cards"] is True
    assert [t["name"] for t in first["topics"]] == [
        "Refund / return", "Billing", "Delivery", "App problem"]  # fmt: skip
    assert first["general_topic"] == "General question" and first["updated_by_name"] is None

    topics = [{"name": t["name"], "keywords": []} for t in first["topics"]]
    topics[3]["keywords"] = ["White Screen", "white  screen"]
    saved = ok(await client.put(S, headers=w["h"], json={"topics": topics,
                                                        "redact_cards": False}))  # fmt: skip
    assert saved["topics"][3]["keywords"] == ["white screen"] and not saved["keywords_are_default"]
    assert saved["redact_cards"] is False and saved["updated_by_name"] == "Admin"
    t = await from_customer(app_sessionmaker, w, subject="Help",
                            description=f"white screen, refund to {CARD}")  # fmt: skip
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert seen["topic"] == "App problem"  # "refund" is no longer a keyword
    assert CARD in seen["messages"][0]["body"]  # redaction is off
    assert not any("card number" in b for b in lines(seen))

    for bad in ({"topics": topics[:3]}, {"topics": [*topics[:3], topics[0]]},
                {"topics": [*topics[:3], {"name": "Other", "keywords": []}]},
                {"topics": [{**topics[0], "keywords": ["x" * 61]}, *topics[1:]]}):  # fmt: skip
        assert (await client.put(S, headers=w["h"], json=bad)).status_code == 422, bad
    assert (await client.put(S, headers=neha, json={"topics": None})).status_code == 403

    back = ok(await client.put(S, headers=w["h"], json={"topics": None}))
    assert back["keywords_are_default"] and back["redact_cards"] is True
    assert "refund" in back["topics"][0]["keywords"]


async def test_hindi_tickets_go_first_to_hindi_speakers(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    asha = await add(client, w["token"], "asha@example.com", "agent", "Asha")
    ok(await client.patch(f"/api/v1/agents/{asha}/routing", headers=w["h"],
                          json={"skills": ["Hindi"]}))  # fmt: skip
    ok(await client.patch(f"/api/v1/teams/{w['teams']['Billing']}", headers=w["h"],
                          json={"member_ids": [w["neha"], asha]}))  # fmt: skip
    # Round robin would pick Neha first; the Hindi ticket goes to Asha.
    t = await from_customer(app_sessionmaker, w, subject="refund",
                            description="mera paisa kab wapas milega bhai")  # fmt: skip
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))
    assert seen["language"] == "hi" and seen["team_name"] == "Billing"
    assert seen["assignee_name"] == "Asha"
    assert any(b.startswith("Auto-assigned to Asha (Billing · skill hindi") for b in lines(seen))
    english = await from_customer(app_sessionmaker, w, subject="refund", description="wrong size")
    assert ok(await client.get(f"{T}/{english['id']}", headers=w["h"]))["assignee_name"] == "Neha"


async def test_a_mood_from_cevanos_is_kept_on_later_replies(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Cevanos's AI read the whole chat; a keyword guess on one line must not undo it."""
    w = await desk(client, app_sessionmaker)
    chat = await from_customer(app_sessionmaker, w, subject="Order", description="Where is it")
    plain = await from_customer(app_sessionmaker, w, subject="Order", description="Where is it")
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await cevanos_repo.set_sentiment(s, chat["id"], "negative")  # conversation.classified
    for t in (chat, plain):
        await customer_replies.customer_reply(
            app_sessionmaker, w["ws"], t["id"], "ok thanks, the refund is great"
        )
    seen = ok(await client.get(f"{T}/{chat['id']}", headers=w["h"]))
    assert chips(seen) == ("Refund / return", "negative", "en")  # topic moves, mood stays
    assert ok(await client.get(f"{T}/{plain['id']}", headers=w["h"]))["sentiment"] == "positive"


async def test_a_reply_reads_the_first_message_and_the_newest_ones(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """A long chat (every line a reply) costs the same each time: the first customer
    message and the 20 newest are read, not all of them."""
    w = await desk(client, app_sessionmaker)
    t = await from_customer(app_sessionmaker, w, subject="Hello", description="refund please")

    async def reply(body: str) -> Any:
        await customer_replies.customer_reply(app_sessionmaker, w["ws"], t["id"], body)
        return ok(await client.get(f"{T}/{t['id']}", headers=w["h"]))["topic"]

    assert await reply("the app crash, app crash again, app crash") == "App problem"
    for n in range(triage.RECENT_MESSAGES - 1):
        assert await reply(f"still waiting {n}") == "App problem"
    # The crash line is now 21st from the end: only the first message's refund is left.
    assert await reply("still waiting") == "Refund / return"
