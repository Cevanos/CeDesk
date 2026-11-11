"""Help center (md P6) with RLS on: Admins write, agents read drafts too, the public sees
only published articles, search finds words and prefixes, and votes are one per visitor
(changeable), counted for agents."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/articles"
C = "/api/v1/article-categories"


def app_client(sm: Sm, **settings: Any) -> AsyncClient:
    app = create_app(Settings(sign_in_limit=100_000, **settings))
    app.state.sessionmaker = sm
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def done(r: Any) -> None:
    assert r.status_code == 204, r.text


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    async with app_client(app_sessionmaker) as c:
        yield c


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    await add(client, admin, "neha@example.com", "agent", "Neha")
    await add(client, admin, "meena@example.com", "light_agent", "Meena")
    a = bearer(admin)
    orders = ok(await client.post(C, headers=a, json={"name": "Orders", "position": 1}), 201)
    billing = ok(await client.post(C, headers=a, json={"name": "Billing", "position": 0}), 201)
    return {
        "slug": slug, "admin": a, "pub": f"/api/v1/public/{slug}/articles",
        "neha": bearer(await sign_in(client, slug, "neha@example.com")),
        "meena": bearer(await sign_in(client, slug, "meena@example.com")),
        "orders": orders["id"], "billing": billing["id"],
    }  # fmt: skip


async def write(client: AsyncClient, w: dict[str, Any], title: str, body: str = "",
                status: str = "published", category: str | None = None) -> Any:  # fmt: skip
    new = {"title": title, "body": body, "status": status, "category_id": category}
    return ok(await client.post(A, headers=w["admin"], json=new), 201)


async def test_admins_write_agents_read_drafts_too(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    draft = await write(client, w, "  Refund   policy ", "Money back in **5 days**.", "draft",
                        w["billing"])  # fmt: skip
    assert draft["title"] == "Refund policy" and draft["status"] == "draft"
    assert draft["published_at"] is None and draft["category_name"] == "Billing"
    assert draft["updated_by_name"] == "Admin"

    for who in ("neha", "meena"):  # agents and light agents read, drafts included
        assert (
            ok(await client.get(f"{A}/{draft['id']}", headers=w[who]))["title"] == "Refund policy"
        )
        listed = ok(await client.get(A, headers=w[who], params={"status": "draft"}))
        assert [x["id"] for x in listed["articles"]] == [draft["id"]]
        for method, url, body in (("POST", A, {"title": "x"}),
                                  ("PATCH", f"{A}/{draft['id']}", {"title": "x"}),
                                  ("DELETE", f"{A}/{draft['id']}", None),
                                  ("POST", C, {"name": "x"})):  # fmt: skip
            r = await client.request(method, url, headers=w[who], json=body)
            assert r.status_code == 403, (who, method, url)

    cats = ok(await client.get(C, headers=w["neha"]))
    assert [(c["name"], c["article_count"]) for c in cats] == [("Billing", 1), ("Orders", 0)]

    bad = await client.post(A, headers=w["admin"], json={"title": "x", "category_id": w["slug"]})
    assert bad.status_code == 422
    other = await desk(client, app_sessionmaker)  # another workspace's category is unknown
    r = await client.post(
        A, headers=w["admin"], json={"title": "x", "category_id": other["orders"]}
    )
    assert r.status_code == 422 and r.json()["error"]["message"] == "Unknown category."
    assert (await client.get(f"{A}/{draft['id']}", headers=other["admin"])).status_code == 404
    r = await client.post(C, headers=w["admin"], json={"name": " orders "})
    assert r.status_code == 201  # names are case-sensitive; exact duplicates are not
    assert (await client.post(C, headers=w["admin"], json={"name": "Orders"})).status_code == 409


async def test_publishing_editing_and_deleting(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await write(client, w, "Track my order", "Use the link in the email", "draft", w["orders"])
    url = f"{A}/{t['id']}"
    live = ok(await client.patch(url, headers=w["admin"], json={"status": "published"}))
    assert live["published_at"] and live["title"] == "Track my order"
    first = live["published_at"]
    off = ok(
        await client.patch(url, headers=w["admin"], json={"status": "draft", "title": "Track"})
    )
    assert off["published_at"] == first and off["title"] == "Track"  # first went live then
    cleared = ok(await client.patch(url, headers=w["admin"], json={"category_id": None}))
    assert cleared["category_id"] is None and cleared["body"] == "Use the link in the email"
    assert (await client.patch(url, headers=w["admin"], json={"title": ""})).status_code == 422

    # Deleting a category keeps its articles, with no category.
    kept = await write(client, w, "Late delivery", category=w["orders"])
    done(await client.delete(f"{C}/{w['orders']}", headers=w["admin"]))
    assert ok(await client.get(f"{A}/{kept['id']}", headers=w["admin"]))["category_id"] is None
    done(await client.delete(url, headers=w["admin"]))
    assert (await client.get(url, headers=w["admin"])).status_code == 404
    assert (await client.delete(url, headers=w["admin"])).status_code == 404


async def test_the_public_sees_only_published_articles(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    live = await write(client, w, "Refund policy", "Money back in 5 days", category=w["billing"])
    draft = await write(client, w, "Secret pricing", "Internal", "draft", w["orders"])
    center = ok(await client.get(w["pub"]))
    assert center["workspace_name"] == "Test"
    assert [(c["name"], c["article_count"]) for c in center["categories"]] == [("Billing", 1)]
    assert [a["title"] for a in center["articles"]] == ["Refund policy"]
    assert "body" not in center["articles"][0]

    page = ok(await client.get(f"{w['pub']}/{live['id']}"))
    assert page["body"] == "Money back in 5 days" and "helpful_count" not in page
    assert (await client.get(f"{w['pub']}/{draft['id']}")).status_code == 404
    assert ok(await client.get(w["pub"], params={"q": "secret"}))["articles"] == []
    assert (await client.get("/api/v1/public/no-such-place/articles")).status_code == 404
    other = await desk(client, app_sessionmaker)
    assert (await client.get(f"{other['pub']}/{live['id']}")).status_code == 404

    for n in range(3):
        await write(client, w, f"Article {n}")
    first = ok(await client.get(w["pub"], params={"limit": 2}))
    assert [a["title"] for a in first["articles"]] == ["Article 2", "Article 1"]
    rest = ok(await client.get(w["pub"], params={"limit": 2, "cursor": first["next_cursor"]}))
    assert [a["title"] for a in rest["articles"]] == ["Article 0", "Refund policy"]
    assert rest["next_cursor"] is None
    only = ok(await client.get(w["pub"], params={"category_id": w["billing"]}))
    assert [a["title"] for a in only["articles"]] == ["Refund policy"]


async def test_search_finds_words_and_prefixes(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    await write(client, w, "Delivery times", "We refund the shipping fee if it is late")
    await write(client, w, "Refund policy", "Money back in 5 days")
    await write(client, w, "रिफंड कैसे पाएं", "पैसे 5 दिन में वापस")

    def titles(r: Any) -> list[str]:
        return [a["title"] for a in ok(r)["articles"]]

    # A title match ranks above a body match; the last word is a prefix.
    assert titles(await client.get(w["pub"], params={"q": "refu"})) == [
        "Refund policy", "Delivery times"]  # fmt: skip
    assert titles(await client.get(w["pub"], params={"q": "money back"})) == ["Refund policy"]
    assert titles(await client.get(w["pub"], params={"q": "रिफंड"})) == ["रिफंड कैसे पाएं"]
    for nothing in ("zzz", "&|!:*()", "'; DROP TABLE hd_article; --"):
        r = ok(await client.get(w["pub"], params={"q": nothing}))
        assert r["articles"] == [] and r["next_cursor"] is None
    agent = ok(await client.get(A, headers=w["neha"], params={"q": "REFUND"}))
    assert len(agent["articles"]) == 2


async def test_votes_are_one_per_visitor_and_counted(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    a = await write(client, w, "Refund policy")
    draft = await write(client, w, "Draft", status="draft")
    vote = f"{w['pub']}/{a['id']}/vote"
    done(await client.post(vote, json={"helpful": True, "visitor": "visitor-one"}))
    done(await client.post(vote, json={"helpful": True, "visitor": "visitor-two"}))
    done(await client.post(vote, json={"helpful": False, "visitor": "visitor-one"}))  # changed
    seen = ok(await client.get(f"{A}/{a['id']}", headers=w["neha"]))
    assert (seen["helpful_count"], seen["not_helpful_count"]) == (1, 1)

    r = await client.post(
        f"{w['pub']}/{draft['id']}/vote", json={"helpful": True, "visitor": "v-1234567"}
    )
    assert r.status_code == 404
    assert (await client.post(vote, json={"helpful": True, "visitor": "x"})).status_code == 422


async def test_votes_per_ip_have_a_limit(app_sessionmaker: Sm) -> None:
    async with app_client(app_sessionmaker, vote_limit=2) as c:
        w = await desk(c, app_sessionmaker)
        a = await write(c, w, "Refund policy")
        vote = f"{w['pub']}/{a['id']}/vote"
        for n in range(2):
            done(await c.post(vote, json={"helpful": True, "visitor": f"visitor-{n}"}))
        r = await c.post(vote, json={"helpful": True, "visitor": "visitor-9"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


async def test_public_reads_have_a_limit(app_sessionmaker: Sm) -> None:
    async with app_client(app_sessionmaker, public_limit=3) as c:
        w = await desk(c, app_sessionmaker)
        a = await write(c, w, "Refund policy")
        ok(await c.get(w["pub"], params={"q": "refund"}))
        ok(await c.get(w["pub"]))
        ok(await c.get(f"{w['pub']}/{a['id']}"))
        r = await c.get(w["pub"], params={"q": "refund"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
