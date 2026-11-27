"""Help center SEO controls (feature-gap plan 4.3) with RLS on: slug, meta text and canonical
address per article and per language, the indexable switch (off by default), and the sitemap
data. Admins write, agents read, the public gets only what is published."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event

from cedesk.helpdesk.service import articles as articles_service
from tests.db.helpers import Sm, ok
from tests.db.test_help_center import A, app_client, desk, write
from tests.db.test_help_translations import S, put, tr

pytestmark = pytest.mark.db


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    async with app_client(app_sessionmaker) as c:
        yield c


async def sitemap(client: AsyncClient, w: dict[str, Any]) -> Any:
    return await client.get(f"/api/v1/public/{w['slug']}/sitemap")


async def test_an_article_carries_slug_meta_text_and_a_canonical_address(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    new = {"title": "Refund policy", "body": "x", "status": "published",
           "slug": "Refunds-FAQ", "meta_title": "  Refunds \n in 5 days ",
           "meta_description": "How refunds work.",
           "canonical_url": "https://example.com/help/refunds"}  # fmt: skip
    made = ok(await client.post(A, headers=w["admin"], json=new), 201)
    assert made["slug"] == "refunds-faq" and made["meta_title"] == "Refunds in 5 days"
    assert made["canonical_url"] == "https://example.com/help/refunds"
    assert made["suggested_slug"] == "refund-policy"  # from the title, offered not applied
    for who in ("neha", "meena"):
        assert ok(await client.get(f"{A}/{made['id']}", headers=w[who]))["slug"] == "refunds-faq"

    url = f"{A}/{made['id']}"
    cleared = ok(await client.patch(url, headers=w["admin"],
                                    json={"canonical_url": None, "meta_title": ""}))  # fmt: skip
    assert cleared["canonical_url"] is None and cleared["meta_title"] is None
    assert cleared["slug"] == "refunds-faq"  # not sent: unchanged
    only = ok(await client.patch(url, headers=w["admin"], json={"title": "Refunds"}))
    assert only["meta_description"] == "How refunds work."  # SEO fields survive other edits

    for bad in ({"slug": "no spaces"}, {"slug": "ab"}, {"canonical_url": "http://x.com/"},
                {"meta_title": "x" * 121}, {"meta_description": "x" * 321},
                {"canonical_url": "https://user:pw@x.com/"}):  # fmt: skip
        assert (await client.patch(url, headers=w["admin"], json=bad)).status_code == 422, bad
    assert (await client.post(A, headers=w["admin"], json={"title": "T", "slug": "!!"})
            ).status_code == 422  # fmt: skip
    refused = await client.patch(url, headers=w["neha"], json={"slug": "mine"})
    assert refused.status_code == 403

    # No slug is fine, and the page works by id: the slug is only cosmetic.
    plain = await write(client, w, "धनवापसी नीति")
    assert plain["slug"] is None and plain["suggested_slug"] is None
    twin = await write(client, w, "Another")
    ok(await client.patch(f"{A}/{twin['id']}", headers=w["admin"], json={"slug": "refunds-faq"}))
    one = ok(await client.get(f"{w['pub']}/{twin['id']}"))  # the same slug twice is allowed
    assert one["slug"] == "refunds-faq" and one["title"] == "Another"


async def test_each_language_has_its_own_seo_text_and_never_inherits_the_canonical(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    new = {"title": "Refund policy", "body": "x", "status": "published", "slug": "refunds",
           "meta_title": "Refunds", "canonical_url": "https://example.com/refunds"}  # fmt: skip
    art = ok(await client.post(A, headers=w["admin"], json=new), 201)
    one = f"{w['pub']}/{art['id']}"

    hindi = ok(await client.put(tr(art["id"], "hi"), headers=w["admin"], json={
        "title": "धनवापसी नीति", "body": "पैसे", "status": "published", "slug": "dhanvapasi",
        "meta_description": "धनवापसी कैसे होती है"}))  # fmt: skip
    assert hindi["slug"] == "dhanvapasi" and hindi["canonical_url"] is None
    assert hindi["suggested_slug"] is None  # the title is Hindi
    assert ok(await client.get(f"{A}/{art['id']}/translations", headers=w["neha"]))[0]["slug"] == (
        "dhanvapasi"
    )

    default = ok(await client.get(one))
    assert default["canonical_url"] == "https://example.com/refunds"
    assert default["meta_title"] == "Refunds" and default["slug"] == "refunds"
    shown = ok(await client.get(one, params={"locale": "hi"}))
    assert shown["canonical_url"] is None  # never the article's: it is its own page
    assert shown["meta_title"] is None and shown["meta_description"] == "धनवापसी कैसे होती है"
    assert shown["slug"] == "dhanvapasi"
    both = [{"locale": "en", "slug": "refunds"}, {"locale": "hi", "slug": "dhanvapasi"}]
    assert default["alternates"] == both and shown["alternates"] == both
    assert shown["available_locales"] == ["en", "hi"]

    # A translation write replaces everything, SEO fields included.
    replaced = {"title": "धनवापसी नीति", "status": "published",
                "canonical_url": "https://hi.example.com/"}  # fmt: skip
    again = ok(await client.put(tr(art["id"], "hi"), headers=w["admin"], json=replaced))
    assert again["slug"] is None and again["canonical_url"] == "https://hi.example.com/"
    bad = await client.put(tr(art["id"], "hi"), headers=w["admin"],
                           json={"title": "x", "slug": "Not Valid"})  # fmt: skip
    assert bad.status_code == 422

    # A draft translation is not listed as an alternate.
    await put(client, w, art["id"], "fr", "Remboursements", status="draft")
    assert [a["locale"] for a in ok(await client.get(one))["alternates"]] == ["en", "hi"]
    # The list rows carry the slug of the language shown.
    row = ok(await client.get(w["pub"], params={"locale": "hi"}))["articles"][0]
    default_row = ok(await client.get(w["pub"]))["articles"][0]
    assert row["slug"] is None and default_row["slug"] == "refunds"


async def test_the_help_center_is_not_indexable_until_an_admin_says_so(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await write(client, w, "Visible")
    assert ok(await client.get(S, headers=w["neha"]))["seo_indexable"] is False
    assert ok(await client.get(w["pub"]))["indexable"] is False
    off = await sitemap(client, w)
    assert ok(off) == {"indexable": False, "entries": [], "truncated": False}
    assert off.headers["cache-control"] == "public, max-age=300"

    assert (await client.put(S, headers=w["neha"], json={"seo_indexable": True})).status_code == 403
    on = ok(await client.put(S, headers=w["admin"], json={"seo_indexable": True}))
    assert on == {"default_locale": "en", "seo_indexable": True}  # the language was not sent
    assert ok(await client.get(w["pub"]))["indexable"] is True
    assert ok(await client.put(S, headers=w["admin"], json={}))["seo_indexable"] is True  # no-op
    back = ok(await client.put(S, headers=w["admin"], json={"seo_indexable": False}))
    assert back["seo_indexable"] is False
    assert (await client.get("/api/v1/public/no-such-workspace/sitemap")).status_code == 404


async def test_the_sitemap_lists_published_pages_in_every_language(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.put(S, headers=w["admin"], json={"seo_indexable": True}))
    live = ok(await client.post(A, headers=w["admin"], json={
        "title": "Refund policy", "status": "published", "slug": "refunds"}), 201)  # fmt: skip
    await write(client, w, "Hidden draft", status="draft")
    await write(client, w, "No slug")
    await put(client, w, live["id"], "hi", "धनवापसी", status="published")
    await put(client, w, live["id"], "fr", "Remboursements", status="draft")  # not listed

    found = ok(await sitemap(client, w))
    assert found["indexable"] is True and found["truncated"] is False
    entries = [(e["id"], e["locale"], e["slug"]) for e in found["entries"]]
    assert len(entries) == 3  # two articles, plus the Hindi page of one
    assert (live["id"], "en", "refunds") in entries and (live["id"], "hi", None) in entries
    mine = [e for e in found["entries"] if e["id"] == live["id"]]
    assert [a["locale"] for a in mine[0]["alternates"]] == ["en", "hi"]
    assert mine[0]["alternates"] == mine[1]["alternates"]
    assert all(e["updated_at"] for e in found["entries"])
    assert "Hidden draft" not in str(found)

    ok(await client.patch(f"{A}/{live['id']}", headers=w["admin"], json={"status": "draft"}))
    assert live["id"] not in str(ok(await sitemap(client, w)))  # unpublished: gone, with its pages


async def test_the_sitemap_is_capped_and_says_so(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(articles_service, "SITEMAP_ARTICLES", 3)
    w = await desk(client, app_sessionmaker)
    ok(await client.put(S, headers=w["admin"], json={"seo_indexable": True}))
    for n in range(5):
        await write(client, w, f"Page {n}")
    found = ok(await sitemap(client, w))
    assert len(found["entries"]) == 3 and found["truncated"] is True


async def test_the_sitemap_costs_the_same_however_many_pages_and_languages(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """One query for the list whatever its size: no query per article or per language."""
    w = await desk(client, app_sessionmaker)
    ok(await client.put(S, headers=w["admin"], json={"seo_indexable": True}))
    engine = app_sessionmaker.kw["bind"].sync_engine
    statements: list[str] = []

    def count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        if "hd_article" in statement:
            statements.append(statement)

    async def measure() -> int:
        statements.clear()
        event.listen(engine, "before_cursor_execute", count)
        try:
            assert (await sitemap(client, w)).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", count)
        return len(statements)

    first = await write(client, w, "One")
    await put(client, w, first["id"], "hi", "एक")
    small = await measure()
    for n in range(8):
        art = await write(client, w, f"More {n}")
        for code in ("hi", "fr", "de"):
            await put(client, w, art["id"], code, f"t{n}")
    assert await measure() == small == 1
    assert len(ok(await sitemap(client, w))["entries"]) == 1 + 1 + 8 * 4


async def test_without_a_language_the_public_answers_keep_their_old_values(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The fields the public routes always had are unchanged by languages and SEO text."""
    w = await desk(client, app_sessionmaker)
    art = await write(client, w, "Refund policy", "Money **back**.", category=w["billing"])
    row = ok(await client.get(w["pub"]))["articles"][0]
    one = ok(await client.get(f"{w['pub']}/{art['id']}"))
    for found in (row, one):
        assert found["id"] == art["id"] and found["title"] == "Refund policy"
        assert found["category_id"] == w["billing"] and found["category_name"] == "Billing"
        assert found["updated_at"] == art["updated_at"]
    assert one["body"] == "Money **back**." and one["locale"] == "en"
    assert one["fallback"] is False and one["alternates"] == [{"locale": "en", "slug": None}]
