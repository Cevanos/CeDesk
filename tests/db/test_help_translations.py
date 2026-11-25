"""Help center languages (feature-gap plan 4.1) with RLS on: Admins write translations, agents
read them (drafts too), the public gets the best language available, the default language
can't clash with a translation, and nothing crosses workspaces."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.repo import auth as auth_repo
from tests.db.helpers import Sm, ok
from tests.db.report_helpers import sql
from tests.db.test_help_center import A, C, app_client, desk, done, write

pytestmark = pytest.mark.db

S = "/api/v1/help-center/settings"


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    async with app_client(app_sessionmaker) as c:
        yield c


def tr(article_id: str, locale: str) -> str:
    return f"{A}/{article_id}/translations/{locale}"


async def put(client: AsyncClient, w: dict[str, Any], article_id: str, locale: str,
              title: str, body: str = "", status: str = "published") -> Any:  # fmt: skip
    return await client.put(tr(article_id, locale), headers=w["admin"],
                            json={"title": title, "body": body, "status": status})  # fmt: skip


async def workspace_id(sm: Sm, w: dict[str, Any]) -> UUID:
    async with sm() as s, s.begin():
        found = await auth_repo.workspace_for_slug(s, w["slug"])
    assert found is not None
    return found


async def test_admins_write_translations_agents_read_drafts_too(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Refund policy", "Money back in 5 days.")
    made = ok(await put(client, w, art["id"], "HI", " धनवापसी  नीति ", "5 दिन में", "draft"))
    assert made["locale"] == "hi" and made["title"] == "धनवापसी नीति"
    assert made["status"] == "draft" and made["published_at"] is None

    for who in ("neha", "meena"):  # agents and light agents read, drafts included
        listed = ok(await client.get(f"{A}/{art['id']}/translations", headers=w[who]))
        assert [t["locale"] for t in listed] == ["hi"]
        assert ok(await client.get(f"{A}/{art['id']}", headers=w[who]))["locales"] == ["hi"]
        refused = await client.put(tr(art["id"], "fr"), headers=w[who], json={"title": "x"})
        assert refused.status_code == 403
        refused = await client.delete(tr(art["id"], "hi"), headers=w[who])
        assert refused.status_code == 403

    live = ok(await put(client, w, art["id"], "hi", "धनवापसी नीति", "5 दिन में", "published"))
    assert live["published_at"]
    first = live["published_at"]
    back = ok(await put(client, w, art["id"], "hi", "धनवापसी नीति", "5 दिन में", "draft"))
    assert back["published_at"] == first  # first went live then, like an article

    assert (await put(client, w, art["id"], "not a code", "x")).status_code == 422
    assert (await put(client, w, art["id"], "fr", "")).status_code == 422
    missing = await put(client, w, "00000000-0000-4000-8000-000000000000", "fr", "x")
    assert missing.status_code == 404
    done(await client.delete(tr(art["id"], "hi"), headers=w["admin"]))
    assert (await client.delete(tr(art["id"], "hi"), headers=w["admin"])).status_code == 404
    assert ok(await client.get(f"{A}/{art['id']}", headers=w["admin"]))["locales"] == []


async def test_the_default_language_and_its_rules(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    assert ok(await client.get(S, headers=w["neha"])) == {
        "default_locale": "en",
        "seo_indexable": False,
    }
    art = await write(client, w, "Hello")
    assert (await put(client, w, art["id"], "en", "Hello again")).status_code == 422  # default

    await put(client, w, art["id"], "hi", "नमस्ते")
    clash = await client.put(S, headers=w["admin"], json={"default_locale": "hi"})
    assert clash.status_code == 409 and "'hi'" in clash.json()["error"]["message"]
    assert ok(await client.get(S, headers=w["admin"]))["default_locale"] == "en"
    nope = await client.put(S, headers=w["neha"], json={"default_locale": "fr"})
    assert nope.status_code == 403
    bad = await client.put(S, headers=w["admin"], json={"default_locale": "english"})
    assert bad.status_code == 422

    off = {"seo_indexable": False}
    assert ok(await client.put(S, headers=w["admin"], json={"default_locale": "FR"})) == {
        "default_locale": "fr",
        **off,
    }
    assert ok(await client.put(S, headers=w["admin"], json={"default_locale": "fr"})) == {
        "default_locale": "fr",
        **off,
    }
    assert (await put(client, w, art["id"], "fr", "Bonjour")).status_code == 422


async def test_changing_the_default_never_ends_with_a_translation_in_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The settings row is locked: a change of default and a translation saved in that
    language at the same moment can't both succeed."""
    for _ in range(6):
        w = await desk(client, app_sessionmaker)
        art = await write(client, w, "Race")
        change, save = await asyncio.gather(
            client.put(S, headers=w["admin"], json={"default_locale": "hi"}),
            put(client, w, art["id"], "hi", "दौड़"),
        )
        assert {change.status_code, save.status_code} in ({200, 422}, {409, 200})
        default = ok(await client.get(S, headers=w["admin"]))["default_locale"]
        kept = ok(await client.get(f"{A}/{art['id']}/translations", headers=w["admin"]))
        assert not (default == "hi" and any(t["locale"] == "hi" for t in kept))


async def test_the_public_gets_the_best_language_available(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Refund policy", "Money back.")
    one = f"{w['pub']}/{art['id']}"
    plain = ok(await client.get(one))  # no language asked: the article itself
    assert plain["title"] == "Refund policy" and plain["locale"] == "en"
    assert plain["fallback"] is False and plain["default_locale"] == "en"
    assert plain["available_locales"] == ["en"]

    await put(client, w, art["id"], "hi", "धनवापसी नीति", "पैसे वापस।", "draft")
    drafted = ok(await client.get(one, params={"locale": "hi"}))
    assert drafted["title"] == "Refund policy" and drafted["fallback"] is True  # draft: not shown
    assert drafted["available_locales"] == ["en"]

    await put(client, w, art["id"], "hi", "धनवापसी नीति", "पैसे वापस।", "published")
    await put(client, w, art["id"], "pt", "Reembolso", "Dinheiro de volta.", "published")
    hindi = ok(await client.get(one, params={"locale": "hi"}))
    assert hindi["title"] == "धनवापसी नीति" and hindi["body"] == "पैसे वापस।"
    assert hindi["locale"] == "hi" and hindi["fallback"] is False
    assert hindi["available_locales"] == ["en", "hi", "pt"]

    brazil = ok(await client.get(one, params={"locale": "pt-BR"}))  # exact, then the base
    assert brazil["locale"] == "pt" and brazil["fallback"] is True
    assert ok(await client.get(one, params={"locale": "fr"}))["title"] == "Refund policy"
    assert ok(await client.get(one, params={"locale": "en"}))["fallback"] is False
    assert (await client.get(one, params={"locale": "english"})).status_code == 422

    listed = ok(await client.get(w["pub"], params={"locale": "hi"}))
    assert [a["title"] for a in listed["articles"]] == ["धनवापसी नीति"]
    assert listed["articles"][0]["locale"] == "hi"
    assert listed["default_locale"] == "en" and listed["locale"] == "hi"
    assert ok(await client.get(w["pub"]))["articles"][0]["title"] == "Refund policy"

    # An unpublished article hides all its translations.
    ok(await client.patch(f"{A}/{art['id']}", headers=w["admin"], json={"status": "draft"}))
    assert (await client.get(one, params={"locale": "hi"})).status_code == 404
    assert ok(await client.get(w["pub"], params={"locale": "hi"}))["articles"] == []


async def test_search_in_a_language_never_hides_an_article(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    translated = await write(client, w, "Refund policy", "Money back in five days.")
    drafted = await write(client, w, "Shipping times", "Orders leave in two days.")
    await write(client, w, "Gift cards", "Buy one for a friend.")
    await put(client, w, translated["id"], "hi", "धनवापसी नीति", "पांच दिन में पैसे वापस।")
    await put(client, w, drafted["id"], "hi", "शिपिंग समय", "दो दिन।", "draft")

    def titles(r: Any) -> list[str]:
        return [a["title"] for a in ok(r)["articles"]]

    hi = {"locale": "hi"}
    assert titles(await client.get(w["pub"], params={**hi, "q": "धनवापसी"})) == ["धनवापसी नीति"]
    # The translated article is searched in Hindi, so its English words no longer match.
    assert titles(await client.get(w["pub"], params={**hi, "q": "refund"})) == []
    # A draft translation never hides an article: it is found by its own words.
    found = await client.get(w["pub"], params={**hi, "q": "shipping"})
    assert titles(found) == ["Shipping times"]
    assert titles(await client.get(w["pub"], params={**hi, "q": "gift"})) == ["Gift cards"]
    # Without a language the default text is searched, as before.
    assert titles(await client.get(w["pub"], params={"q": "refund"})) == ["Refund policy"]


async def test_translators_list_what_still_needs_a_language(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    done_one = await write(client, w, "Done")
    todo = await write(client, w, "Todo")
    await put(client, w, done_one["id"], "hi", "हो गया")
    await put(client, w, done_one["id"], "fr", "Fait", status="draft")  # any status counts

    def ids(r: Any) -> set[str]:
        return {a["id"] for a in ok(r)["articles"]}

    both = {done_one["id"], todo["id"]}
    assert ids(await client.get(A, headers=w["neha"], params={"missing_locale": "hi"})) == {
        todo["id"]
    }
    assert ids(await client.get(A, headers=w["neha"], params={"missing_locale": "fr"})) == {
        todo["id"]
    }
    assert ids(await client.get(A, headers=w["neha"], params={"missing_locale": "es"})) == both
    assert ids(await client.get(A, headers=w["neha"])) == both
    searched = await client.get(A, headers=w["neha"], params={"missing_locale": "hi", "q": "todo"})
    assert ids(searched) == {todo["id"]}
    bad = await client.get(A, headers=w["neha"], params={"missing_locale": "english"})
    assert bad.status_code == 422


async def test_category_names_in_other_languages(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await write(client, w, "Track my order", category=w["orders"])
    url = f"{C}/{w['orders']}/translations/hi"
    first = ok(await client.put(url, headers=w["admin"], json={"name": " ऑर्डर  "}))
    assert first == {"name_translations": {"hi": "ऑर्डर"}}
    more = ok(await client.put(f"{C}/{w['orders']}/translations/fr", headers=w["admin"],
                               json={"name": "Commandes"}))  # fmt: skip
    assert more["name_translations"] == {"fr": "Commandes", "hi": "ऑर्डर"}
    cats = {c["id"]: c for c in ok(await client.get(C, headers=w["neha"]))}
    assert cats[w["orders"]]["name_translations"]["hi"] == "ऑर्डर"
    assert cats[w["orders"]]["name"] == "Orders"

    def named(r: Any) -> list[str]:
        return [c["name"] for c in ok(r)["categories"]]

    assert named(await client.get(w["pub"], params={"locale": "hi"})) == ["ऑर्डर"]
    assert named(await client.get(w["pub"], params={"locale": "fr-CA"})) == ["Commandes"]
    assert named(await client.get(w["pub"], params={"locale": "de"})) == ["Orders"]
    assert named(await client.get(w["pub"])) == ["Orders"]
    row = ok(await client.get(w["pub"], params={"locale": "hi"}))["articles"][0]
    assert row["category_name"] == "ऑर्डर"

    assert (await client.put(f"{C}/{w['orders']}/translations/en", headers=w["admin"],
                             json={"name": "x"})).status_code == 422  # default  # fmt: skip
    assert (await client.put(url, headers=w["neha"], json={"name": "x"})).status_code == 403
    gone = ok(await client.delete(url, headers=w["admin"]))
    assert gone == {"name_translations": {"fr": "Commandes"}}
    assert (await client.delete(url, headers=w["admin"])).status_code == 404
    unknown = "00000000-0000-4000-8000-000000000000"
    assert (await client.put(f"{C}/{unknown}/translations/hi", headers=w["admin"],
                             json={"name": "x"})).status_code == 404  # fmt: skip


async def test_an_article_has_at_most_forty_translations(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Many")
    codes = [f"a{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(41)]
    for code in codes[:40]:
        assert (await put(client, w, art["id"], code, "t")).status_code == 200
    assert (await put(client, w, art["id"], codes[40], "t")).status_code == 422
    assert (await put(client, w, art["id"], codes[0], "changed")).status_code == 200  # a change


async def test_deleting_an_article_deletes_its_translations_and_workspaces_are_apart(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    art = await write(client, w, "Mine")
    await put(client, w, art["id"], "hi", "मेरा")

    # Another workspace's Admin can't see, write or delete it: it is a 404, not a 403.
    assert (await put(client, other, art["id"], "fr", "x")).status_code == 404
    assert (await client.get(f"{A}/{art['id']}/translations", headers=other["admin"])
            ).status_code == 404  # fmt: skip
    assert (await client.delete(tr(art["id"], "hi"), headers=other["admin"])).status_code == 404
    assert ok(await client.get(S, headers=other["admin"]))["default_locale"] == "en"
    ok(await client.put(S, headers=other["admin"], json={"default_locale": "fr"}))
    assert ok(await client.get(S, headers=w["admin"]))["default_locale"] == "en"  # not shared

    ws = await workspace_id(app_sessionmaker, w)
    count = "SELECT count(*) AS n FROM hd_article_translation"
    assert (await sql(app_sessionmaker, ws, count))[0]["n"] == 1
    done(await client.delete(f"{A}/{art['id']}", headers=w["admin"]))
    assert (await sql(app_sessionmaker, ws, count))[0]["n"] == 0


async def test_a_vote_records_the_language_of_the_page(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Vote here")
    ws = await workspace_id(app_sessionmaker, w)
    url = f"{w['pub']}/{art['id']}/vote"
    visitor = "visitor-0001-abcdef"
    done(await client.post(url, json={"helpful": True, "visitor": visitor, "locale": "HI"}))
    rows = await sql(app_sessionmaker, ws, "SELECT helpful, locale FROM hd_article_vote")
    assert [(r["helpful"], r["locale"]) for r in rows] == [(True, "hi")]
    # Still one vote per visitor: voting again (on another language's page) replaces it.
    done(await client.post(url, json={"helpful": False, "visitor": visitor, "locale": "fr"}))
    rows = await sql(app_sessionmaker, ws, "SELECT helpful, locale FROM hd_article_vote")
    assert [(r["helpful"], r["locale"]) for r in rows] == [(False, "fr")]
    done(await client.post(url, json={"helpful": True, "visitor": visitor}))  # no language
    rows = await sql(app_sessionmaker, ws, "SELECT locale FROM hd_article_vote")
    assert [r["locale"] for r in rows] == [None]
    bad = await client.post(url, json={"helpful": True, "visitor": visitor, "locale": "english"})
    assert bad.status_code == 422


async def test_translation_changes_are_in_the_audit_log_without_the_text(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Audited")
    await put(client, w, art["id"], "hi", "ऑडिट", "गोपनीय पाठ")
    await put(client, w, art["id"], "hi", "ऑडिट", "गोपनीय पाठ 2")
    done(await client.delete(tr(art["id"], "hi"), headers=w["admin"]))
    ok(await client.put(S, headers=w["admin"], json={"default_locale": "fr"}))
    page = ok(await client.get("/api/v1/audit", headers=w["admin"],
                               params={"area": "help_center", "limit": 200}))  # fmt: skip
    mine = [e for e in page["entries"] if "ranslation" in e["target"] or "settings" in e["target"]]
    assert [e["action"] for e in reversed(mine)] == ["created", "changed", "deleted", "changed"]
    assert "गोपनीय" not in str(page)


async def test_three_part_codes_work_and_fall_back_one_step_at_a_time(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Refund policy", "Money back.")
    one = f"{w['pub']}/{art['id']}"
    made = ok(await put(client, w, art["id"], "ZH-Hant-TW", "退款政策", "五個工作天內退款。"))
    assert made["locale"] == "zh-hant-tw"
    ok(await put(client, w, art["id"], "zh", "退款", "简体。"))
    exact = ok(await client.get(one, params={"locale": "zh-Hant-TW"}))
    assert exact["title"] == "退款政策" and exact["fallback"] is False
    # Hong Kong has no page: the nearest is the base, until a script-level page exists.
    hk = ok(await client.get(one, params={"locale": "zh-hant-hk"}))
    assert hk["locale"] == "zh" and hk["fallback"] is True
    ok(await put(client, w, art["id"], "zh-hant", "退款政策 繁體", "繁體。"))
    hk = ok(await client.get(one, params={"locale": "zh-hant-hk"}))
    assert hk["locale"] == "zh-hant" and hk["fallback"] is True
    assert (await client.get(one, params={"locale": "zh-hant-tw-x"})).status_code == 422


async def test_search_ignores_accents_and_how_the_text_was_typed(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    import unicodedata

    w = await desk(client, app_sessionmaker)
    cafe = await write(client, w, "Café opening hours", "We open at nine.")
    reset = await write(client, w, "Password reset", "Click here.")
    hindi = await write(client, w, "Refunds", "Money back.")
    title = unicodedata.normalize("NFD", "Réinitialiser le mot de passe")  # as a Mac may paste it
    body = unicodedata.normalize("NFD", "Cliquez sur « Mot de passe oublié ».")
    ok(await put(client, w, reset["id"], "fr", title, body))
    ok(await put(client, w, hindi["id"], "hi", "धनवापसी नीति", "पांच दिन में पैसे वापस।"))

    def found(r: Any) -> list[str]:
        return [a["id"] for a in ok(r)["articles"]]

    fr = {"locale": "fr"}
    for q in (
        "réinitialiser",
        "Réinitialiser",
        "reinitialiser",
        "REINITIALISER",
        "oublie",
        "oublié",
    ):
        assert found(await client.get(w["pub"], params={**fr, "q": q})) == [reset["id"]], q
    decomposed_q = unicodedata.normalize("NFD", "réinitialiser")
    assert found(await client.get(w["pub"], params={**fr, "q": decomposed_q})) == [reset["id"]]
    assert found(await client.get(w["pub"], params={"q": "cafe"})) == [cafe["id"]]
    assert found(await client.get(w["pub"], params={"q": "CAFÉ"})) == [cafe["id"]]
    assert found(await client.get(w["pub"], params={**fr, "q": "passage"})) == []
    # Scripts with no accent rules are left alone.
    hi = {"locale": "hi"}
    assert found(await client.get(w["pub"], params={**hi, "q": "धनवापसी"})) == [hindi["id"]]
    # The stored title is the composed form.
    got = ok(await client.get(f"{w['pub']}/{reset['id']}", params=fr))
    assert got["title"] == unicodedata.normalize("NFC", title)
    # The agents' own search folds accents too.
    mine = ok(await client.get(A, headers=w["admin"], params={"q": "cafe"}))
    assert [a["id"] for a in mine["articles"]] == [cafe["id"]]
