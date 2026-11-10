"""Saved replies (md W19) through the real API with RLS on: the mock's four defaults,
Admin-only changes, the `/` list for agents, and adding the defaults to an old workspace."""

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import seeds
from cedesk.helpdesk.service.errors import NotFound
from cedesk.helpdesk.service.seeds import add_workflow_defaults
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

R = "/api/v1/saved-replies"


async def admin(client: AsyncClient, sm: Sm, *, seed: bool = False) -> tuple[str, str]:
    slug = await new_workspace(sm, seed=seed)
    return slug, await sign_in(client, slug, "admin@example.com")


async def test_a_new_workspace_gets_the_mocks_four_saved_replies(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, token = await admin(client, app_sessionmaker, seed=True)
    rows = ok(await client.get(R, headers=bearer(token)))
    assert [r["shortcut"] for r in rows] == ["photo", "refund", "shipping", "thanks"]
    assert all(r["source"] == "default" for r in rows)
    thanks = next(r for r in rows if r["shortcut"] == "thanks")
    assert "{{customer.first_name}}" in thanks["body"]


async def test_the_list_narrows_to_what_was_typed_after_the_slash(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, token = await admin(client, app_sessionmaker, seed=True)
    h = bearer(token)
    assert [r["shortcut"] for r in ok(await client.get(R, headers=h, params={"q": "/sh"}))] == [
        "shipping"
    ]
    assert [r["shortcut"] for r in ok(await client.get(R, headers=h, params={"q": "T"}))] == [
        "thanks"
    ]
    assert ok(await client.get(R, headers=h, params={"q": "zzz"})) == []
    # Matching is a prefix, never a pattern: `%` and `_` are plain characters.
    assert ok(await client.get(R, headers=h, params={"q": "%"})) == []


async def test_an_admin_adds_changes_and_deletes_a_saved_reply(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, token = await admin(client, app_sessionmaker)
    h = bearer(token)
    created = ok(
        await client.post(R, headers=h, json={"shortcut": "/Bulk", "body": "  12% off 40+  "}), 201
    )
    assert created["shortcut"] == "bulk"
    assert created["body"] == "12% off 40+"
    assert created["source"] == "manual"

    changed = ok(
        await client.patch(f"{R}/{created['id']}", headers=h, json={"shortcut": "bulk-order"})
    )
    assert changed["shortcut"] == "bulk-order"
    assert changed["body"] == "12% off 40+"
    changed = ok(await client.patch(f"{R}/{created['id']}", headers=h, json={"body": "New"}))
    assert (changed["shortcut"], changed["body"]) == ("bulk-order", "New")

    assert (await client.delete(f"{R}/{created['id']}", headers=h)).status_code == 204
    assert ok(await client.get(R, headers=h)) == []
    r = await client.delete(f"{R}/{created['id']}", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NotFound"


async def test_a_shortcut_is_unique_whatever_its_case_or_slash(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, token = await admin(client, app_sessionmaker, seed=True)
    h = bearer(token)
    r = await client.post(R, headers=h, json={"shortcut": "/REFUND", "body": "x"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "Conflict"

    mine = ok(await client.post(R, headers=h, json={"shortcut": "mine", "body": "x"}), 201)
    r = await client.patch(f"{R}/{mine['id']}", headers=h, json={"shortcut": "Refund"})
    assert r.status_code == 409
    # Renaming to its own shortcut is not a clash.
    ok(await client.patch(f"{R}/{mine['id']}", headers=h, json={"shortcut": "mine"}))


@pytest.mark.parametrize(
    "body",
    [
        {"shortcut": "two words", "body": "x"},
        {"shortcut": "/", "body": "x"},
        {"shortcut": "ok", "body": "   "},
        {"shortcut": "ok", "body": "x" * 10_001},
        {"shortcut": "ok"},
        {"shortcut": "ok", "body": "x", "source": "intercom"},
    ],
)
async def test_a_bad_saved_reply_is_refused(
    client: AsyncClient, app_sessionmaker: Sm, body: dict[str, str]
) -> None:
    _, token = await admin(client, app_sessionmaker)
    r = await client.post(R, headers=bearer(token), json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "Invalid"


async def test_agents_use_saved_replies_but_only_admins_change_them(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug, token = await admin(client, app_sessionmaker, seed=True)
    h = bearer(token)
    for email, role in [
        ("sup@example.com", "supervisor"),
        ("agent@example.com", "agent"),
        ("light@example.com", "light_agent"),
    ]:
        await add(client, token, email, role)
    reply_id = ok(await client.get(R, headers=h))[0]["id"]

    for email in ["sup@example.com", "agent@example.com", "light@example.com"]:
        other = bearer(await sign_in(client, slug, email))
        assert len(ok(await client.get(R, headers=other))) == 4
        for r in [
            await client.post(R, headers=other, json={"shortcut": "new", "body": "x"}),
            await client.patch(f"{R}/{reply_id}", headers=other, json={"body": "x"}),
            await client.delete(f"{R}/{reply_id}", headers=other),
        ]:
            assert r.status_code == 403, (email, r.text)
    assert (await client.get(R)).status_code == 401


async def test_one_workspace_never_sees_or_changes_anothers_replies(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, first = await admin(client, app_sessionmaker, seed=True)
    _, second = await admin(client, app_sessionmaker)
    theirs = ok(await client.get(R, headers=bearer(first)))[0]["id"]

    assert ok(await client.get(R, headers=bearer(second))) == []
    h = bearer(second)
    assert (await client.patch(f"{R}/{theirs}", headers=h, json={"body": "x"})).status_code == 404
    assert (await client.delete(f"{R}/{theirs}", headers=h)).status_code == 404
    # The same shortcut is free in another workspace.
    ok(await client.post(R, headers=h, json={"shortcut": "refund", "body": "ours"}), 201)
    assert len(ok(await client.get(R, headers=bearer(first)))) == 4


async def test_an_old_workspace_gets_the_defaults_once_and_keeps_its_own(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug, token = await admin(client, app_sessionmaker)
    h = bearer(token)
    ok(await client.post(R, headers=h, json={"shortcut": "refund", "body": "Our own text"}), 201)

    # 3 replies (its own /refund is kept) + the 2 rules and 2 macros that need no team or
    # type: this workspace has none, so the other 4 rules and "Escalate to Tech" are left out.
    assert await add_workflow_defaults(app_sessionmaker, slug.upper()) == 7
    assert await add_workflow_defaults(app_sessionmaker, slug) == 0  # again: nothing new
    rules = ok(await client.get("/api/v1/rules", headers=h))
    assert [r["name"] for r in rules] == [
        "Reopen when the customer replies",
        "Upset customer goes first",
    ]
    macros = ok(await client.get("/api/v1/macros", headers=h))
    assert [m["name"] for m in macros] == ["Refund approved", "Ask for a photo"]
    rows = {r["shortcut"]: r for r in ok(await client.get(R, headers=h))}
    assert sorted(rows) == sorted(r.shortcut for r in seeds.SAVED_REPLIES)
    assert rows["refund"]["body"] == "Our own text"
    assert rows["refund"]["source"] == "manual"

    with pytest.raises(NotFound):
        await add_workflow_defaults(app_sessionmaker, "no-such-workspace")


async def test_defaults_an_admin_removed_never_come_back(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug, token = await admin(client, app_sessionmaker, seed=True)
    h = bearer(token)
    rules = {r["name"]: r["id"] for r in ok(await client.get("/api/v1/rules", headers=h))}
    macros = {m["name"]: m["id"] for m in ok(await client.get("/api/v1/macros", headers=h))}
    replies = {r["shortcut"]: r["id"] for r in ok(await client.get(R, headers=h))}
    await client.delete(f"/api/v1/rules/{rules['Upset customer goes first']}", headers=h)
    ok(await client.patch(f"/api/v1/macros/{macros['Escalate to Tech']}", headers=h,
                          json={"name": "Escalate"}))  # fmt: skip
    await client.delete(f"{R}/{replies['thanks']}", headers=h)

    assert await add_workflow_defaults(app_sessionmaker, slug) == 0
    rule_names = [r["name"] for r in ok(await client.get("/api/v1/rules", headers=h))]
    assert "Upset customer goes first" not in rule_names and len(rule_names) == 5
    macro_names = [m["name"] for m in ok(await client.get("/api/v1/macros", headers=h))]
    assert sorted(macro_names) == ["Ask for a photo", "Escalate", "Refund approved"]
    assert "thanks" not in [r["shortcut"] for r in ok(await client.get(R, headers=h))]


async def test_a_default_waiting_for_its_team_is_added_once_the_team_exists(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug, token = await admin(client, app_sessionmaker)  # no teams or types
    h = bearer(token)
    await add_workflow_defaults(app_sessionmaker, slug)
    names = [m["name"] for m in ok(await client.get("/api/v1/macros", headers=h))]
    assert "Escalate to Tech" not in names
    ok(await client.post("/api/v1/teams", headers=h, json={"name": "Tech"}), 201)
    assert await add_workflow_defaults(app_sessionmaker, slug) == 1
    names = [m["name"] for m in ok(await client.get("/api/v1/macros", headers=h))]
    assert names[-1] == "Escalate to Tech"
