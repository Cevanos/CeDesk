"""Marketplace apps (md PL7) and the ticket sidebar's orders and payments cards (C12),
the rules that need no database.

- Apps here: Shopify (orders) and Stripe (payments), read only; Jira (issues from tickets)
  and Slack alerts (Urgent and High tickets to a channel). Calendly is on the cut line.
- An Admin installs an app with its credentials; CeDesk checks them with the provider,
  keeps them sealed (only the last 4 characters are shown), and audits install and
  uninstall. With Cevanos connected, orders and payments come from Cevanos's own
  integrations instead (md D16): the Shopify and Stripe apps aren't installed a second time.
- The cards are read live from the provider for the ticket's customer (by email), at most
  5 each; nothing is stored in CeDesk. A provider that fails shows as unavailable and the
  ticket still loads.
"""

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


@dataclass(frozen=True)
class AppInfo:
    name: str
    card: str  # what it adds: orders, payments, jira (on tickets) or alerts
    settings: tuple[str, ...]  # what the Admin types that isn't secret
    credential: str  # its name
    credential_help: str


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
}
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
        raise BadCredentials(f"Paste the {CATALOG[app].credential.replace('_', ' ')}.")
    if app is App.stripe and not value.startswith(("rk_", "sk_")):
        raise BadCredentials("A Stripe key starts with rk_ (restricted) or sk_.")
    return value


def secret_hint(value: str) -> str:
    return f"…{value[-4:]}"


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
    url: str  # the payment in the Stripe dashboard


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
