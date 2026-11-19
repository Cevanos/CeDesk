"""The Calendly app (md PL7, W19), with RLS on: install with only the booking link (no
credential; only calendly.com links), the saved reply /book it adds and keeps in step,
a workspace's own /book kept (Calendly's becomes /book1), uninstall removing only
Calendly's reply, audited; a /book the Admin edited (relinked, or kept on uninstall as an
ordinary reply), renamed or deleted; other apps still need their credential; tenancy."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import apps as rules
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/apps"
R = "/api/v1/saved-replies"
LINK = "https://calendly.com/acme/30min"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"h": bearer(token), "ws": UUID(me["workspace"]["id"]),
            "agent": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def replies(client: AsyncClient, w: dict[str, Any]) -> dict[str, Any]:
    return {r["shortcut"]: r for r in ok(await client.get(R, headers=w["h"]))}


async def audit(client: AsyncClient, w: dict[str, Any]) -> list[Any]:
    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "apps"}))
    return [(e["action"], e["detail"]) for e in page["entries"]]


async def test_a_link_copied_from_the_address_bar_is_saved_clean(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Live retest: the link as the browser shows it (?month=...) was refused. It's saved
    as the page alone, and nothing of its query reaches /book."""
    w = await desk(client, app_sessionmaker)
    copied = {"settings": {"link": "www.calendly.com/acme/30min?month=2026-11&x=<b>"}}
    got = ok(await client.put(f"{A}/calendly", headers=w["h"], json=copied))
    assert got["settings"] == {"link": LINK}
    assert (await replies(client, w))["book"]["body"] == rules.book_reply(LINK)


async def test_installing_calendly_adds_book(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    [info] = [a for a in ok(await client.get(A, headers=w["h"])) if a["app"] == "calendly"]
    assert (info["credential"], info["settings_fields"], info["card"]) == (None, ["link"], "book")

    bad_links = ["http://calendly.com/acme", "https://calendly.com.evil.test/acme",
                 "https://evil.test/calendly.com/acme", "https://calendly.com/",
                 "javascript:alert(1)", "",
                 "https://user@calendly.com/acme", "https://calendly.com/a/b/c"]  # fmt: skip
    for link in bad_links:
        r = await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": link}})
        assert r.status_code == 422, link
    for extra in ({"secret": "abc"}, {"signing_secret": "x" * 32},
                  {"settings": {"link": LINK, "colour": "red"}}):  # fmt: skip
        body = {"settings": {"link": LINK}} | extra
        assert (await client.put(f"{A}/calendly", headers=w["h"], json=body)).status_code == 422
    body = {"settings": {"link": LINK}}
    assert (await client.put(f"{A}/calendly", headers=w["agent"], json=body)).status_code == 403
    assert "book" not in await replies(client, w)

    got = ok(await client.put(f"{A}/calendly", headers=w["h"], json=body))
    assert (got["installed"], got["secret_hint"], got["settings"]) == (True, None, {"link": LINK})
    book = (await replies(client, w))["book"]
    assert book["source"] == "calendly" and LINK in book["body"]
    shown = ok(await client.get(R, headers=w["agent"], params={"q": "bo"}))
    assert [r["shortcut"] for r in shown] == ["book"]  # agents can use it

    newer = "https://calendly.com/acme/15min/"
    ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": newer}}))
    after = await replies(client, w)
    assert after["book"]["id"] == book["id"] and newer.rstrip("/") in after["book"]["body"]
    assert [r for r in after.values() if r["source"] == "calendly"] == [after["book"]]

    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    assert "book" not in await replies(client, w)
    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 404
    assert await audit(client, w) == [
        ("uninstalled", {"saved_replies_removed": ["/book"]}),
        ("changed", {"settings": {"link": "https://calendly.com/acme/15min"},
                     "saved_reply": "/book"}),
        ("installed", {"settings": {"link": LINK}, "saved_reply": "/book"}),
    ]  # fmt: skip


async def test_a_book_of_your_own_is_kept(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    own = {"shortcut": "book", "body": "Call us on 080 1234"}
    mine = ok(await client.post(R, headers=w["h"], json=own), 201)
    ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": LINK}}))
    got = await replies(client, w)
    assert got["book"] == mine  # untouched
    assert got["book1"]["source"] == "calendly" and LINK in got["book1"]["body"]

    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    got = await replies(client, w)
    assert "book1" not in got and got["book"] == mine  # only Calendly's went


async def test_other_apps_still_need_their_credential(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for app, body in (("stripe", {}), ("slack_alerts", {"secret": ""}),
                      ("jira", {"settings": {"site": "acme", "email": "a@acme.test",
                                             "project": "SUP"}})):  # fmt: skip
        r = await client.put(f"{A}/{app}", headers=w["h"], json=body)
        assert r.status_code == 422 and "Paste the" in r.json()["error"]["message"], app


async def test_another_workspace_is_untouched(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": LINK}}))
    assert "book" in await replies(client, w)
    assert "book" not in await replies(client, other)
    assert (await client.delete(f"{A}/calendly", headers=other["h"])).status_code == 404
    assert "book" in await replies(client, w)  # still there


async def install(client: AsyncClient, w: dict[str, Any], link: str = LINK) -> Any:
    return ok(await client.put(f"{A}/calendly", headers=w["h"], json={"settings": {"link": link}}))


async def reply_audit(client: AsyncClient, w: dict[str, Any]) -> list[Any]:
    page = ok(await client.get("/api/v1/audit", headers=w["h"],
                               params={"area": "saved_replies"}))  # fmt: skip
    return [(e["action"], e["target"]) for e in page["entries"]]


async def test_an_edited_book_keeps_its_words_and_gets_the_new_link(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    assert (await install(client, w))["note"] is None
    book = (await replies(client, w))["book"]
    mine = f"Hello! Book a call with our team here: {LINK} (we answer in English and Hindi)"
    ok(await client.patch(f"{R}/{book['id']}", headers=w["h"], json={"body": mine}))

    newer = "https://calendly.com/acme/45min"
    got = await install(client, w, newer)
    assert got["note"] is None
    assert (await replies(client, w))["book"]["body"] == mine.replace(LINK, newer)

    # Edited beyond the link (the link is gone): left alone, with a note.
    theirs = "Write to us and we'll send you a time."
    ok(await client.patch(f"{R}/{book['id']}", headers=w["h"], json={"body": theirs}))
    got = await install(client, w, LINK)
    assert got["note"] == "/book was edited by hand; update its link yourself."
    assert (await replies(client, w))["book"]["body"] == theirs
    assert (await audit(client, w))[0] == ("changed", {
        "settings": {"link": LINK}, "saved_reply": "/book", "note": got["note"]})  # fmt: skip
    # Saving the same link again: no note, nothing changes.
    assert (await install(client, w, LINK))["note"] is None
    assert [a for a in await reply_audit(client, w) if a[0] != "changed"] == [
        ("created", "Saved reply /book")]  # fmt: skip
    assert len(await reply_audit(client, w)) == 4  # created; Admin x2; relinked by Calendly


def test_a_link_inside_a_longer_link_is_not_swapped() -> None:
    old, new = "https://calendly.com/acme", "https://calendly.com/zen"
    body = f"Book: {old}/30min or {old}."
    assert rules.relinked_book(body, old, new) == f"Book: {old}/30min or {new}."
    assert rules.relinked_book(f"Book: {old}/30min", old, new) is None
    assert rules.relinked_book(rules.book_reply(old), old, new) == rules.book_reply(new)
    assert rules.book_untouched(rules.book_reply(old), old)
    assert not rules.book_untouched(rules.book_reply(old) + " Thanks!", old)
    assert not rules.book_untouched(rules.book_reply(old), None)


async def test_an_edited_book_is_kept_on_uninstall(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await install(client, w)
    book = (await replies(client, w))["book"]
    edited = book["body"] + " See you soon!"
    ok(await client.patch(f"{R}/{book['id']}", headers=w["h"], json={"body": edited}))
    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    kept = (await replies(client, w))["book"]
    assert (kept["id"], kept["body"], kept["source"]) == (book["id"], edited, "manual")
    note = "/book was edited by hand, so it was kept as an ordinary saved reply."
    assert (await audit(client, w))[0] == ("uninstalled", {
        "saved_replies_removed": [], "saved_reply_kept": "/book", "note": note})  # fmt: skip
    assert (await reply_audit(client, w))[0] == ("changed", "Saved reply /book")

    # Installing again: the kept reply is the Admin's now, so Calendly's is /book1.
    await install(client, w)
    got = await replies(client, w)
    assert got["book"]["source"] == "manual" and got["book1"]["source"] == "calendly"


async def test_a_renamed_book_is_still_calendlys(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await install(client, w)
    book = (await replies(client, w))["book"]
    ok(await client.patch(f"{R}/{book['id']}", headers=w["h"], json={"shortcut": "meet"}))
    newer = "https://calendly.com/acme/15min"
    await install(client, w, newer)  # renamed, text untouched: relinked under its new name
    got = await replies(client, w)
    assert "book" not in got and got["meet"]["body"].endswith(newer)
    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    assert "meet" not in await replies(client, w)  # only the name changed: removed
    assert (await audit(client, w))[0] == ("uninstalled", {"saved_replies_removed": ["/meet"]})
    assert (await reply_audit(client, w))[0] == ("deleted", "Saved reply /meet")


async def test_a_book_deleted_by_hand_comes_back(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await install(client, w)
    book = (await replies(client, w))["book"]
    assert (await client.delete(f"{R}/{book['id']}", headers=w["h"])).status_code == 204
    # Saving the settings again adds it back (with the current link), as a new reply.
    newer = "https://calendly.com/acme/15min"
    await install(client, w, newer)
    back = (await replies(client, w))["book"]
    assert back["id"] != book["id"] and back["source"] == "calendly"
    assert back["body"].endswith(newer)
    assert [a[0] for a in await reply_audit(client, w)] == ["created", "deleted", "created"]
    # Deleted again, then uninstalled: nothing to remove, no error.
    assert (await client.delete(f"{R}/{back['id']}", headers=w["h"])).status_code == 204
    assert (await client.delete(f"{A}/calendly", headers=w["h"])).status_code == 204
    assert (await audit(client, w))[0] == ("uninstalled", {"saved_replies_removed": []})
