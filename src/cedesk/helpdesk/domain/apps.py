"""Marketplace apps (md PL7) and the ticket sidebar's orders and payments cards (C12),
the rules that need no database.

- Apps here: Shopify (orders) and Stripe (payments), read only; Jira (issues from tickets),
  Slack alerts (Urgent and High tickets to a channel), Slack side conversations (a bot
  that posts in a channel and hears the thread's replies) and Calendly (the saved reply
  /book with the booking link; no credential, the link is public).
- An Admin installs an app with its credentials; CeDesk checks them with the provider,
  keeps them sealed (only the last 4 characters are shown), and audits install and
  uninstall. With Cevanos connected, orders and payments come from Cevanos's own
  integrations instead (md D16): the Shopify and Stripe apps aren't installed a second time.
- The cards are read live from the provider for the ticket's customer (by email), at most
  5 each; nothing is stored in CeDesk. A provider that fails shows as unavailable and the
  ticket still loads.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class App(StrEnum):
    shopify = "shopify"
    stripe = "stripe"
    jira = "jira"
    slack_alerts = "slack_alerts"
    slack = "slack"  # side conversations in Slack (md X5)
    calendly = "calendly"  # the saved reply /book (md W19)


@dataclass(frozen=True)
class AppInfo:
    name: str
    card: str  # what it adds: orders, payments, jira (on tickets), alerts, side or book
    settings: tuple[str, ...]  # what the Admin types that isn't secret
    credential: str | None  # its name; None: the app needs none (Calendly)
    credential_help: str
    second_credential: str | None = None  # also secret, sealed with the first


CATALOG: dict[App, AppInfo] = {
    App.shopify: AppInfo(
        name="Shopify",
        card="orders",
        settings=("shop",),
        credential="access_token",
        credential_help="An Admin API access token of a custom app with read_orders and "
        "read_customers (Shopify admin > Settings > Apps > Develop apps).",
    ),
    App.stripe: AppInfo(
        name="Stripe",
        card="payments",
        settings=(),
        credential="api_key",
        credential_help="A restricted key (rk_...) with read access to Customers and Charges; "
        "a secret key (sk_...) works too but can do far more than CeDesk needs.",
    ),
    App.jira: AppInfo(
        name="Jira",
        card="jira",
        settings=("site", "email", "project"),
        credential="api_token",
        credential_help="An Atlassian API token of the account in `email` "
        "(id.atlassian.com > Security > API tokens); `site` is acme for acme.atlassian.net, "
        "`project` the key new issues go to (e.g. SUP).",
    ),
    App.slack_alerts: AppInfo(
        name="Slack alerts",
        card="alerts",
        settings=(),
        credential="webhook_url",
        credential_help="A Slack Incoming Webhook URL (https://hooks.slack.com/services/...) "
        "for the channel that gets Urgent and High tickets. Installing it posts a test line.",
    ),
    App.slack: AppInfo(
        name="Slack side conversations",
        card="side",
        settings=(),
        credential="bot_token",
        credential_help="Make a Slack app (api.slack.com/apps) with the bot scopes "
        "chat:write, channels:history, groups:history and users:read, install it in your "
        "Slack, and paste its bot token (xoxb-...) and, as `signing_secret`, its signing "
        "secret. Then, in the Slack app's Event Subscriptions, set the Request URL to "
        "<CeDesk API>/api/v1/slack/events/<workspace id> and subscribe the bot to "
        "message.channels and message.groups. Invite the bot to each channel it should use.",
        second_credential="signing_secret",
    ),
    App.calendly: AppInfo(
        name="Calendly",
        card="book",
        settings=("link",),
        credential=None,
        credential_help="No credential: the booking link is public. `link` is your "
        "Calendly page or one event's page (https://calendly.com/acme/30min). Installing "
        "adds the saved reply /book with it (/book1 if you already have a /book); "
        "uninstalling removes it.",
    ),
}
BOOK_SHORTCUT = "book"
_CALENDLY = re.compile(r"^https://calendly\.com/[A-Za-z0-9_-]{1,100}(/[A-Za-z0-9_-]{1,100})?/?$")
CARD_LIMIT = 5  # orders or payments shown per customer
SECRET_MAX = 300
SHOPIFY_API = "2024-10"

# Currencies Stripe counts in whole units (no cents), so 500 is 500, not 5.00.
_ZERO_DECIMAL = frozenset(
    {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd",
     "vuv", "xaf", "xof", "xpf"}
)  # fmt: skip


class BadCredentials(ValueError):
    """The message is for the Admin."""


def check_secret(app: App, value: str) -> str:
    value = value.strip()
    if not value or len(value) > SECRET_MAX:
        name = (CATALOG[app].credential or "credential").replace("_", " ")
        raise BadCredentials(f"Paste the {name}.")
    if app is App.stripe and not value.startswith(("rk_", "sk_")):
        raise BadCredentials("A Stripe key starts with rk_ (restricted) or sk_.")
    return value


def secret_hint(value: str) -> str:
    return f"…{value[-4:]}"


def booking_link(raw: Any) -> str:
    """A Calendly page: https://calendly.com/<you> or https://calendly.com/<you>/<event>."""
    link = str(raw or "").strip()
    if not _CALENDLY.match(link):
        raise BadCredentials(
            "Give your Calendly booking link, e.g. https://calendly.com/acme/30min."
        )
    return link.rstrip("/")


def book_reply(link: str) -> str:
    """The saved reply /book (placeholders are filled when it is sent)."""
    return f"Hi {{{{customer.first_name}}}}, you can pick a time that suits you here: {link}"


BOOK_EDITED = "/{shortcut} was edited by hand; update its link yourself."
BOOK_KEPT = "/{shortcut} was edited by hand, so it was kept as an ordinary saved reply."


def _link_in(link: str) -> re.Pattern[str]:
    """The link as a whole word: https://calendly.com/acme isn't found in .../acme/30min."""
    return re.compile(re.escape(link) + r"(?![A-Za-z0-9_/-])")


def book_untouched(body: str, link: str | None) -> bool:
    """Whether /book is still exactly the text Calendly wrote for `link` (not edited by
    hand). Only such a reply is deleted when Calendly is uninstalled."""
    return link is not None and body == book_reply(link)


def relinked_book(body: str, old: str | None, new: str) -> str | None:
    """/book's text for a new link: Calendly's own text, or the Admin's text with only the
    old link swapped for the new one. None when the Admin's text no longer has the old link
    (edited beyond that): it is left alone."""
    if old is None:
        return None
    if body == book_reply(old):
        return book_reply(new)
    pattern = _link_in(old)
    if not pattern.search(body):
        return None
    return pattern.sub(lambda _: new, body)


@dataclass(frozen=True)
class Order:
    id: str
    number: str  # as the shop shows it, e.g. #1001
    created_at: datetime | None
    payment: str | None  # paid, pending, refunded, ...
    fulfillment: str | None  # fulfilled, partial, or None (not yet)
    total: str  # "1299.00"
    currency: str
    items: int
    url: str | None  # the order in the shop's admin


@dataclass(frozen=True)
class Payment:
    id: str
    amount: str  # "12.50"
    currency: str  # INR, USD...
    status: str  # succeeded, pending, failed
    refunded: bool
    created_at: datetime | None
    description: str | None
    url: str | None  # the payment in the Stripe dashboard (None: the store has no link)


def _when(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _text(value: Any, limit: int = 200) -> str | None:
    return str(value)[:limit] if value not in (None, "") else None


def _same_email(value: Any, email: str) -> bool:
    return isinstance(value, str) and value.strip().lower() == email.strip().lower()


def shopify_customer_ids(data: Any, email: str) -> list[str]:
    """From `GET /admin/api/<v>/customers/search.json?query=email:<email>`: the customers
    whose email is exactly this one (the search is loose, so it is checked here), at most 3."""
    customers = data.get("customers") if isinstance(data, dict) else None
    out = []
    for c in customers if isinstance(customers, list) else []:
        if isinstance(c, dict) and c.get("id") is not None and _same_email(c.get("email"), email):
            out.append(str(c["id"]))
    return out[:3]


def shopify_orders(data: Any, shop: str, email: str | None = None) -> list[Order]:
    """From `GET /admin/api/<v>/customers/<id>/orders.json`; anything unexpected is left
    out. With `email`, so is an order whose email isn't the customer's: the card never
    shows another customer's order, whatever Shopify answers."""
    orders = data.get("orders") if isinstance(data, dict) else None
    out = []
    for o in orders if isinstance(orders, list) else []:
        if not isinstance(o, dict) or o.get("id") is None:
            continue
        if email is not None and not _same_email(o.get("email"), email):
            continue
        items = o.get("line_items")
        out.append(
            Order(
                id=str(o["id"]),
                number=str(o.get("name") or f"#{o.get('order_number', o['id'])}")[:40],
                created_at=_when(o.get("created_at")),
                payment=_text(o.get("financial_status"), 40),
                fulfillment=_text(o.get("fulfillment_status"), 40),
                total=str(o.get("total_price") or "0"),
                currency=str(o.get("currency") or "")[:3].upper(),
                items=sum(int(i.get("quantity") or 0) for i in items if isinstance(i, dict))
                if isinstance(items, list)
                else 0,
                url=f"https://{shop}.myshopify.com/admin/orders/{o['id']}",
            )
        )
    return out[:CARD_LIMIT]


def stripe_amount(minor: int, currency: str) -> str:
    """Stripe counts in the currency's smallest unit (paise, cents) except zero-decimal
    currencies."""
    if currency.lower() in _ZERO_DECIMAL:
        return str(minor)
    return f"{Decimal(minor) / 100:.2f}"


def stripe_payments(data: Any) -> list[Payment]:
    """From `GET /v1/charges`."""
    charges = data.get("data") if isinstance(data, dict) else None
    out = []
    for c in charges if isinstance(charges, list) else []:
        if not isinstance(c, dict) or not c.get("id") or not isinstance(c.get("amount"), int):
            continue
        currency = str(c.get("currency") or "")
        target = c.get("payment_intent") or c["id"]
        out.append(
            Payment(
                id=str(c["id"]),
                amount=stripe_amount(int(c["amount"]), currency),
                currency=currency.upper()[:3],
                status=str(c.get("status") or "unknown")[:40],
                refunded=bool(c.get("refunded")),
                created_at=_when(c.get("created")),
                description=_text(c.get("description")),
                url=f"https://dashboard.stripe.com/payments/{target}",
            )
        )
    return out[:CARD_LIMIT]


def stripe_customer_ids(data: Any) -> list[str]:
    """From `GET /v1/customers?email=`: one email can have several Stripe customers."""
    found = data.get("data") if isinstance(data, dict) else None
    return [str(c["id"]) for c in found or [] if isinstance(c, dict) and c.get("id")][:3]
