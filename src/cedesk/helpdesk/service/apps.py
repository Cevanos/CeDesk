"""Marketplace apps (md PL7) and the ticket sidebar's orders and payments (C12). Rules in
domain/apps.py.

- Install, change and uninstall need `settings` (as the Cevanos connection) and are
  audited (area `apps`: installed, changed, uninstalled; never the credential).
- The cards need only the right to read the ticket. They are read live with the
  importers' HTTP client (`SourceHttp`: GET only, the egress guard before each call, no
  redirects, credentials only in headers), at most 10 s. A provider that fails or refuses
  gives `available: false` with a message; the card never fails the ticket.
- With Cevanos connected (md D16), orders and payments come from Cevanos's integrations:
  installing Shopify or Stripe is refused, and the cards say Cevanos will fill them once
  it serves them over `/v1` (planned with the Cevanos AI features).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from cedesk.crypto import Sealer
from cedesk.db import workspace_session
from cedesk.egress import BadAddress, account_host, account_label
from cedesk.helpdesk.domain import apps as rules
from cedesk.helpdesk.domain import jira as jira_rules
from cedesk.helpdesk.domain import saved_replies as reply_rules
from cedesk.helpdesk.domain import slack as slack_rules
from cedesk.helpdesk.domain.apps import App, Order, Payment
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.imports import RateLimited, SourceError
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import apps as repo
from cedesk.helpdesk.repo import cevanos as cevanos_repo
from cedesk.helpdesk.repo import saved_replies as reply_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, scope, slack_bot
from cedesk.helpdesk.service import jira as jira_service
from cedesk.helpdesk.service.app_post import AppCallError
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound, Unavailable
from cedesk.helpdesk.service.imports.http import SourceHttp, bearer
from cedesk.helpdesk.service.jira import PostFactory
from cedesk.helpdesk.service.members import require

SLACK_TEST_LINE = "CeDesk is connected: Urgent and High tickets will be posted here."

STRIPE_HOST = "api.stripe.com"
CARD_TIMEOUT = 10.0
CEVANOS_SOON = (
    "Cevanos is connected: these come from its integrations once Cevanos serves them to CeDesk."
)
NO_EMAIL = "This customer has no email yet, so there is nothing to look up."

AppHttp = Callable[[str, Mapping[str, str], str], SourceHttp]
"""(host, headers, label) -> a client. The app state holds the real one; tests a fake."""


def make_http(host: str, headers: Mapping[str, str], label: str) -> SourceHttp:
    return SourceHttp(host, headers=headers, label=label, timeout=CARD_TIMEOUT)


@dataclass(frozen=True)
class AppView:
    app: App
    name: str
    card: str
    settings_fields: tuple[str, ...]
    credential: str | None  # None: the app needs none (Calendly)
    credential_help: str
    second_credential: str | None  # also secret (Slack side conversations: signing_secret)
    installed: bool
    settings: dict[str, Any]
    secret_hint: str | None
    installed_at: datetime | None
    installed_by_name: str | None
    blocked: str | None  # why it can't be installed here (Cevanos connected)


@dataclass(frozen=True)
class Card:
    source: str | None  # shopify, stripe, cevanos, or None (nothing to read from)
    available: bool
    message: str | None = None
    orders: list[Order] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)


def _view(app: App, row: Any, blocked: str | None) -> AppView:
    info = rules.CATALOG[app]
    return AppView(
        app=app,
        name=info.name,
        card=info.card,
        settings_fields=info.settings,
        credential=info.credential,
        credential_help=info.credential_help,
        second_credential=info.second_credential,
        installed=row is not None,
        settings=dict(row["settings"]) if row is not None else {},
        secret_hint=row["secret_hint"] if row is not None else None,
        installed_at=row["installed_at"] if row is not None else None,
        installed_by_name=row["installed_by_name"] if row is not None else None,
        blocked=blocked,
    )


def _bound(workspace_id: UUID, app: App) -> str:
    return f"{workspace_id}:app:{app.value}"


def jira_bound(workspace_id: UUID) -> str:
    return _bound(workspace_id, App.jira)


def slack_bound(workspace_id: UUID) -> str:
    return _bound(workspace_id, App.slack_alerts)


_DATA_APPS = frozenset({App.shopify, App.stripe})  # Cevanos's integrations replace these


def _blocked(app: App, connected: bool) -> str | None:
    if not connected or app not in _DATA_APPS:
        return None
    return (
        "Cevanos is connected: orders and payments come from its integrations, so this "
        "isn't installed a second time."
    )


async def catalog(sm: Sessionmaker, caller: Caller) -> list[AppView]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = {r["app"]: r for r in await repo.installed(s)}
        connected = await cevanos_repo.connection(s) is not None
    return [_view(app, rows.get(app.value), _blocked(app, connected)) for app in App]


def _client(http: AppHttp, app: App, settings: Mapping[str, Any], secret: str) -> SourceHttp:
    if app is App.shopify:
        host = account_host("shopify", str(settings["shop"]))
        return http(host, {"X-Shopify-Access-Token": secret}, "Shopify")
    return http(STRIPE_HOST, bearer(secret), "Stripe")


async def _check_with_provider(client: SourceHttp, app: App) -> None:
    try:
        if app is App.shopify:
            await client.get(f"/admin/api/{rules.SHOPIFY_API}/shop.json")
        else:
            await client.get("/v1/customers", {"limit": 1})
    except SourceError as exc:
        if exc.permanent:
            raise Invalid(str(exc)) from None
        raise Unavailable(str(exc)) from None
    except RateLimited:
        raise Unavailable("The provider is busy; try again in a minute.") from None


def _clean_settings(app: App, settings: Mapping[str, Any]) -> dict[str, Any]:
    """What the Admin typed, checked; raises BadCredentials, BadAddress, JiraInputError."""
    if app is App.shopify:
        return {"shop": account_label("shopify", str(settings.get("shop") or ""))}
    if app is App.jira:
        email = str(settings.get("email") or "").strip().lower()
        if "@" not in email or len(email) > 200 or " " in email:
            raise rules.BadCredentials("Give the email of the Atlassian account the token is for.")
        return {
            "site": account_label("jira", str(settings.get("site") or "")),
            "email": email,
            "project": jira_rules.project_key(str(settings.get("project") or "")),
        }
    if settings:
        raise rules.BadCredentials(f"{rules.CATALOG[app].name} takes no settings.")
    return {}


async def _check_by_post(
    post: PostFactory, app: App, settings: Mapping[str, Any], secret: str
) -> None:
    """Jira: the token can see the project. Slack alerts: a test line reaches the channel.
    Slack side conversations: Slack knows the bot token."""
    try:
        if app is App.jira:
            await jira_service.check_install(post, settings, secret)
        elif app is App.slack:
            token, signing_secret = slack_rules.opened(secret)
            await slack_bot.Bot(post, token, signing_secret).check()
        else:
            await post(slack_rules.HOST, {}, "Slack").post(
                slack_rules.path_of(secret), {"text": SLACK_TEST_LINE}
            )
    except AppCallError as exc:
        if exc.permanent:
            raise Invalid(str(exc)) from None
        raise Unavailable(str(exc)) from None


async def install(
    sm: Sessionmaker,
    sealer: Sealer,
    http: AppHttp,
    post: PostFactory,
    caller: Caller,
    app: App,
    *,
    settings: Mapping[str, Any],
    secret: str | None,
    second: str | None = None,
) -> AppView:
    """Installs, or replaces the settings and credential of an installed app. The
    credential is checked with the provider first: a refused one is a 422. `second`: the
    Slack side conversations app's signing secret, sealed with the bot token. Calendly has
    no credential (its link is public)."""
    require(caller, Action.settings)
    if app is App.calendly:
        return await _install_calendly(sm, caller, settings, secret, second)
    try:
        secret = rules.check_secret(app, secret or "")
        hint = rules.secret_hint(secret)
        if app is App.slack_alerts:
            secret = slack_rules.check_webhook(secret)
        if app is App.slack:
            token = slack_rules.check_bot_token(secret)
            hint = rules.secret_hint(token)
            secret = slack_rules.seal_value(token, slack_rules.check_signing_secret(second))
        elif second is not None:
            raise rules.BadCredentials(f"{rules.CATALOG[app].name} takes one credential.")
        clean = _clean_settings(app, settings)
    except (rules.BadCredentials, BadAddress, jira_rules.JiraInputError,
            slack_rules.BadWebhook, slack_rules.BadSlack) as exc:  # fmt: skip
        raise Invalid(str(exc)) from exc
    if app in _DATA_APPS:
        async with workspace_session(sm, caller.workspace_id) as s:
            if await cevanos_repo.connection(s) is not None:
                raise Conflict(str(_blocked(app, True)))
        await _check_with_provider(_client(http, app, clean, secret), app)
    else:
        await _check_by_post(post, app, clean, secret)
    async with workspace_session(sm, caller.workspace_id) as s:
        new = await repo.save(
            s, caller.workspace_id, app=app.value, settings=clean,
            secret_sealed=sealer.seal(secret, bound_to=_bound(caller.workspace_id, app)),
            secret_hint=hint, by=caller.member_id,
        )  # fmt: skip
        await audit.record(
            s, caller, Area.apps, "installed" if new else "changed", rules.CATALOG[app].name,
            detail={"settings": clean, "hint": hint},
        )  # fmt: skip
        row = await repo.get(s, app.value)
    return _view(app, row, None)


async def uninstall(sm: Sessionmaker, caller: Caller, app: App) -> None:
    """The credential is deleted with it; Calendly's saved reply (/book) too."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.remove(s, app.value):
            raise NotFound(f"{rules.CATALOG[app].name} isn't installed.")
        detail: dict[str, Any] = {}
        if app is App.calendly:
            gone = await reply_repo.delete_from(s, App.calendly.value)
            detail["saved_replies_removed"] = [f"/{shortcut}" for shortcut in gone]
        await audit.record(
            s, caller, Area.apps, "uninstalled", rules.CATALOG[app].name, detail=detail or None
        )


async def _install_calendly(
    sm: Sessionmaker, caller: Caller, settings: Mapping[str, Any], secret: str | None,
    second: str | None,
) -> AppView:  # fmt: skip
    """Saves the booking link and keeps the saved reply /book in step with it: added on
    install (/book1 ... when the workspace has its own /book), its text changed with the
    link. Both in one transaction, audited together."""
    try:
        if secret or second:
            raise rules.BadCredentials("Calendly needs no credential: give its booking link.")
        if set(settings) - {"link"}:
            raise rules.BadCredentials("Calendly takes one setting: link.")
        clean = {"link": rules.booking_link(settings.get("link"))}
    except rules.BadCredentials as exc:
        raise Invalid(str(exc)) from exc
    body = rules.book_reply(clean["link"])
    source = App.calendly.value
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            new = await repo.save(
                s, caller.workspace_id, app=source, settings=clean, secret_sealed=None,
                secret_hint=None, by=caller.member_id,
            )  # fmt: skip
            reply = await reply_repo.by_source(s, source, rules.BOOK_SHORTCUT)
            if reply is None:
                taken = await reply_repo.shortcuts(s)
                shortcut = reply_rules.free_shortcut(rules.BOOK_SHORTCUT, taken)
                await reply_repo.insert_from(
                    s, caller.workspace_id, shortcut=shortcut, body=body, source=source,
                    source_id=rules.BOOK_SHORTCUT, created_by=caller.member_id,
                )  # fmt: skip
            else:
                shortcut = str(reply["shortcut"])
                await reply_repo.update_reply(s, reply["id"], shortcut=None, body=body)
            await audit.record(
                s, caller, Area.apps, "installed" if new else "changed", "Calendly",
                detail={"settings": clean, "saved_reply": f"/{shortcut}"},
            )  # fmt: skip
            row = await repo.get(s, source)
    except IntegrityError as exc:  # two installs at once
        raise Conflict("Calendly was being set up at the same time; try again.") from exc
    return _view(App.calendly, row, None)


# The sidebar (C12)


async def _card_source(
    sm: Sessionmaker, sealer: Sealer, caller: Caller, ticket_id: UUID, app: App
) -> tuple[Card | None, str, Any, str]:
    """(an answer already, or None to read), the customer's email, the app row, secret."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        detail = await ticket_repo.ticket_detail(s, ticket_id)
        if detail is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=False)
        connected = await cevanos_repo.connection(s) is not None
        row = await repo.get(s, app.value) if not connected else None
    email = str(detail["contact_email"] or "")
    if connected:
        return Card("cevanos", False, CEVANOS_SOON), email, None, ""
    if row is None:
        name = rules.CATALOG[app].name
        return Card(None, False, f"Install the {name} app to see them here."), email, None, ""
    if not email or email.endswith(".invalid"):
        return Card(app.value, False, NO_EMAIL), email, row, ""
    secret = sealer.open(bytes(row["secret_sealed"]), bound_to=_bound(caller.workspace_id, app))
    return None, email, row, secret


def _unavailable(app: App, exc: Exception) -> Card:
    if isinstance(exc, RateLimited):
        message = f"{rules.CATALOG[app].name} is busy; try again in a minute."
    else:
        message = str(exc)
    return Card(app.value, False, message)


async def orders(
    sm: Sessionmaker, sealer: Sealer, http: AppHttp, caller: Caller, ticket_id: UUID
) -> Card:
    """The customer's latest Shopify orders: the shop's customers with this exact email,
    then each one's orders (Shopify's order list can't be filtered by email)."""
    early, email, row, secret = await _card_source(sm, sealer, caller, ticket_id, App.shopify)
    if early is not None:
        return early
    shop = str(row["settings"]["shop"])
    client = _client(http, App.shopify, {"shop": shop}, secret)
    api = f"/admin/api/{rules.SHOPIFY_API}"
    try:
        customers = rules.shopify_customer_ids(
            await client.get(
                f"{api}/customers/search.json",
                {"query": f"email:{email}", "fields": "id,email", "limit": 3},
            ),
            email,
        )
        found: list[rules.Order] = []
        for customer in customers:
            data = await client.get(
                f"{api}/customers/{customer}/orders.json",
                {"status": "any", "limit": rules.CARD_LIMIT,
                 "fields": "id,email,name,order_number,created_at,financial_status,"
                           "fulfillment_status,total_price,currency,line_items"},
            )  # fmt: skip
            found += rules.shopify_orders(data, shop, email)
    except (SourceError, RateLimited) as exc:
        return _unavailable(App.shopify, exc)
    found.sort(key=lambda o: o.created_at.timestamp() if o.created_at else 0, reverse=True)
    return Card(App.shopify.value, True, orders=found[: rules.CARD_LIMIT])


async def payments(
    sm: Sessionmaker, sealer: Sealer, http: AppHttp, caller: Caller, ticket_id: UUID
) -> Card:
    """The customer's latest Stripe charges, by email (one email can be several Stripe
    customers: up to 3 are read, newest charges first)."""
    early, email, _, secret = await _card_source(sm, sealer, caller, ticket_id, App.stripe)
    if early is not None:
        return early
    client = _client(http, App.stripe, {}, secret)
    try:
        customers = rules.stripe_customer_ids(
            await client.get("/v1/customers", {"email": email, "limit": 3})
        )
        found: list[Payment] = []
        for customer in customers:
            data = await client.get(
                "/v1/charges", {"customer": customer, "limit": rules.CARD_LIMIT}
            )
            found += rules.stripe_payments(data)
    except (SourceError, RateLimited) as exc:
        return _unavailable(App.stripe, exc)
    found.sort(key=lambda p: p.created_at.timestamp() if p.created_at else 0, reverse=True)
    return Card(App.stripe.value, True, payments=found[: rules.CARD_LIMIT])
