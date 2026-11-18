"""Marketplace apps (md PL7) and the ticket sidebar's orders and payments (C12), with RLS
on and a fake Shopify and Stripe: install checks the credential and keeps it sealed;
install, change and uninstall are audited; the cards read live for the ticket's customer;
a provider that refuses or fails never fails the ticket; with Cevanos connected the apps
step aside."""

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.egress import BadAddress
from cedesk.helpdesk.service.imports.http import SourceHttp
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/apps"
T = "/api/v1/tickets"
SHOP_TOKEN = "shpat_good1234"
STRIPE_KEY = "rk_live_good5678"


class Providers:
    """A fake Shopify (acme.myshopify.com) and Stripe (api.stripe.com)."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.down:
            return httpx.Response(503)
        path, q = request.url.path, request.url.params
        if request.url.host == "acme.myshopify.com":
            if request.headers.get("X-Shopify-Access-Token") != SHOP_TOKEN:
                return httpx.Response(401)
            if path.endswith("/shop.json"):
                return httpx.Response(200, json={"shop": {"name": "Acme"}})
            if path.endswith("/customers/search.json"):  # loose: a near match comes back too
                found = [{"id": 7, "email": "Priya@Example.org"}, {"id": 8, "email": "p@x.org"}]
                hit = "priya" in q["query"]
                return httpx.Response(200, json={"customers": found if hit else []})
            if path.endswith("/customers/7/orders.json"):
                order = {"id": 11, "email": "priya@example.org", "name": "#1001",
                         "total_price": "1299.00", "currency": "INR",
                         "financial_status": "paid", "line_items": [{"quantity": 2}]}  # fmt: skip
                other = {**order, "id": 12, "email": "someone@else.org", "name": "#1002"}
                return httpx.Response(200, json={"orders": [order, other]})
            if path.endswith("/orders.json"):
                return httpx.Response(500)  # any other order list: never asked
        if request.url.host == "api.stripe.com":
            if request.headers.get("Authorization") != f"Bearer {STRIPE_KEY}":
                return httpx.Response(401)
            if path == "/v1/customers":
                found = [{"id": "cus_1"}] if q.get("email") == "priya@example.org" else []
                return httpx.Response(200, json={"data": found})
            if path == "/v1/charges" and q["customer"] == "cus_1":
                return httpx.Response(200, json={"data": [
                    {"id": "ch_1", "amount": 49900, "currency": "inr", "status": "succeeded",
                     "created": 1762000000, "payment_intent": "pi_1"}]})  # fmt: skip
        return httpx.Response(404)


async def guard(host: str) -> None:
    if host.startswith("internal."):
        raise BadAddress(f"'{host}' isn't a public address.")


def install_fakes(client: AsyncClient) -> Providers:
    fake = Providers()

    def http(host: str, headers: Mapping[str, str], label: str) -> SourceHttp:
        return SourceHttp(host, headers=headers, label=label, guard=guard,
                          transport=httpx.MockTransport(fake))  # fmt: skip

    client._transport.app.state.app_http = http  # type: ignore[attr-defined]
    return fake


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=h))
    body = {
        "subject": "Where is my order",
        "contact": {"email": "priya@example.org", "name": "Priya"},
    }
    t = ok(await client.post(T, headers=h, json=body), 201)
    return {"slug": slug, "h": h, "ws": UUID(me["workspace"]["id"]), "ticket": t["id"],
            "neha": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def put_app(client: AsyncClient, w: dict[str, Any], app: str, secret: str,
                  settings: dict[str, str] | None = None) -> httpx.Response:  # fmt: skip
    return await client.put(f"{A}/{app}", headers=w["h"],
                            json={"secret": secret, "settings": settings or {}})  # fmt: skip


async def test_each_install_form_names_its_boxes(client: AsyncClient, app_sessionmaker: Sm) -> None:
    """Live retest: the Jira form showed three boxes with no label (the API gave only
    "site", "email", "project"), and Calendly one. `fields` gives each box its label, its
    hint and where to send it, in order."""
    install_fakes(client)
    w = await desk(client, app_sessionmaker)
    listed = {a["app"]: a for a in ok(await client.get(A, headers=w["h"]))}
    shown = {app: [(f["key"], f["label"], f["send_as"], f["secret"]) for f in a["fields"]]
             for app, a in listed.items()}  # fmt: skip
    assert shown["jira"] == [
        ("site", "Jira site", "settings", False),
        ("email", "Atlassian account email", "settings", False),
        ("project", "Project key", "settings", False),
        ("api_token", "API token", "secret", True),
    ]
    assert shown["calendly"] == [("link", "Booking link", "settings", False)]
    assert shown["slack"] == [
        ("bot_token", "Bot token", "secret", True),
        ("signing_secret", "Signing secret", "signing_secret", True),
    ]
    for app in listed.values():  # every box has a label and a hint, none made up
        assert app["fields"] and all(f["label"] and f["hint"] for f in app["fields"])
        settings = [f["key"] for f in app["fields"] if f["send_as"] == "settings"]
        assert settings == app["settings_fields"]  # the old field still says the same


async def test_admins_install_change_and_uninstall(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    fake = install_fakes(client)
    w = await desk(client, app_sessionmaker)
    listed = {a["app"]: a for a in ok(await client.get(A, headers=w["h"]))}
    assert listed["shopify"]["installed"] is False and listed["shopify"]["settings_fields"] == [
        "shop"
    ]

    shop = ok(
        await put_app(
            client, w, "shopify", SHOP_TOKEN, {"shop": "https://Acme.myshopify.com/admin"}
        )
    )
    assert (shop["installed"], shop["settings"], shop["secret_hint"]) == (
        True,
        {"shop": "acme"},
        "…1234",
    )
    assert fake.seen[-1].url.path == "/admin/api/2024-10/shop.json"  # checked with Shopify
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        sealed = (await s.execute(text("SELECT secret_sealed FROM hd_app_install"))).scalar_one()
    assert SHOP_TOKEN.encode() not in bytes(sealed)
    ok(await put_app(client, w, "shopify", SHOP_TOKEN, {"shop": "acme"}))  # again: a change

    for app, secret, settings, code in (
        ("shopify", "shpat_wrong", {"shop": "acme"}, 422),  # Shopify refuses it
        ("shopify", SHOP_TOKEN, {"shop": "acme.evil.com"}, 422),  # not a shop name
        ("shopify", SHOP_TOKEN, {"shop": "internal"}, 422),  # not a public address
        ("stripe", "pk_live_x", {}, 422),  # a publishable key can't read
        ("stripe", STRIPE_KEY, {"shop": "x"}, 422),  # Stripe takes no settings
        ("calendly", "x", {}, 422),  # Calendly takes no credential (and needs its link)
        ("zapier", "x", {}, 422),  # not an app here
    ):
        r = await put_app(client, w, app, secret, settings)
        assert r.status_code == code, (app, secret, settings, r.text)
    assert (
        await client.put(f"{A}/stripe", headers=w["neha"], json={"secret": STRIPE_KEY})
    ).status_code == 403
    fake.down = True
    assert (await put_app(client, w, "stripe", STRIPE_KEY)).status_code == 503
    fake.down = False

    assert (await client.delete(f"{A}/shopify", headers=w["h"])).status_code == 204
    assert (await client.delete(f"{A}/shopify", headers=w["h"])).status_code == 404
    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "apps"}))
    entries = page["entries"]
    assert [e["action"] for e in entries] == ["uninstalled", "changed", "installed"]
    assert SHOP_TOKEN not in json.dumps(entries)


async def test_the_sidebar_reads_orders_and_payments(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    fake = install_fakes(client)
    w = await desk(client, app_sessionmaker)
    orders_url, payments_url = f"{T}/{w['ticket']}/orders", f"{T}/{w['ticket']}/payments"
    empty = ok(await client.get(orders_url, headers=w["neha"]))
    assert (empty["source"], empty["available"], empty["orders"]) == (None, False, [])
    assert "Install the Shopify app" in empty["message"]

    ok(await put_app(client, w, "shopify", SHOP_TOKEN, {"shop": "acme"}))
    ok(await put_app(client, w, "stripe", STRIPE_KEY))
    orders = ok(await client.get(orders_url, headers=w["neha"]))  # an agent may read them
    assert (orders["source"], orders["available"]) == ("shopify", True)
    assert [(o["number"], o["total"], o["currency"], o["items"]) for o in orders["orders"]] == [
        ("#1001", "1299.00", "INR", 2)]  # fmt: skip
    # Only customer 7 (the exact email, any case) is read; another's order is left out.
    asked = [r.url.path.rsplit("/", 2)[-2:] for r in fake.seen[-2:]]
    assert asked == [["customers", "search.json"], ["7", "orders.json"]]
    assert fake.seen[-2].url.params["query"] == "email:priya@example.org"
    pays = ok(await client.get(payments_url, headers=w["neha"]))
    assert [(p["amount"], p["currency"], p["status"]) for p in pays["payments"]] == [
        ("499.00", "INR", "succeeded")]  # fmt: skip
    assert pays["payments"][0]["url"] == "https://dashboard.stripe.com/payments/pi_1"

    fake.down = True  # the provider fails: the card says so, nothing breaks
    down = ok(await client.get(orders_url, headers=w["neha"]))
    assert (down["available"], down["orders"]) == (False, []) and "Shopify" in down["message"]
    fake.down = False
    r = await client.get(f"{T}/{UUID(int=5)}/orders", headers=w["neha"])
    assert r.status_code == 404
    other = await desk(client, app_sessionmaker)
    r = await client.get(f"{T}/{w['ticket']}/orders", headers=other["h"])
    assert r.status_code == 404  # another workspace's ticket

    # A role that sees only its own team's tickets doesn't see this customer's orders.
    team = ok(await client.post("/api/v1/teams", headers=w["h"],
                                json={"name": "Billing", "auto_assign": False}), 201)  # fmt: skip
    role = ok(await client.post("/api/v1/roles", headers=w["h"], json={
        "name": "Billing only", "scope": "team_only", "team_id": team["id"]}), 201)  # fmt: skip
    admin_token = w["h"]["Authorization"].split()[1]
    rita = await add(client, admin_token, "rita@example.com", "agent", "Rita")
    patch = {"role": "custom", "role_id": role["id"]}
    ok(await client.patch(f"/api/v1/members/{rita}", headers=w["h"], json=patch))
    scoped = bearer(await sign_in(client, w["slug"], "rita@example.com"))
    assert (await client.get(orders_url, headers=scoped)).status_code == 404


async def test_with_cevanos_connected_the_apps_step_aside(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    install_fakes(client)
    w = await desk(client, app_sessionmaker)
    ok(await put_app(client, w, "stripe", STRIPE_KEY))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(
            text(
                "INSERT INTO hd_cevanos_connection (workspace_id, api_key_sealed, "
                "api_key_hint, webhook_secret_sealed) VALUES (:ws, '\\x00', 'abcd', '\\x00')"
            ),
            {"ws": w["ws"]},
        )
    assert (await put_app(client, w, "shopify", SHOP_TOKEN, {"shop": "acme"})).status_code == 409
    listed = {a["app"]: a for a in ok(await client.get(A, headers=w["h"]))}
    assert listed["shopify"]["blocked"] and listed["stripe"]["installed"] is True
    card = ok(await client.get(f"{T}/{w['ticket']}/payments", headers=w["h"]))
    assert (card["source"], card["available"]) == ("cevanos", False)
