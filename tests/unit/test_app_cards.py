"""Reading Shopify orders and Stripe payments into the sidebar cards (md PL7, C12), no
database: amounts, odd answers left out, at most 5, and the credential checks."""

from datetime import UTC, datetime

import pytest

from cedesk.helpdesk.domain import apps as rules
from cedesk.helpdesk.domain.apps import App

SHOPIFY = {
    "orders": [
        {"id": 450789469, "name": "#1001", "created_at": "2026-11-01T10:00:00+05:30",
         "financial_status": "paid", "fulfillment_status": None, "total_price": "1299.00",
         "currency": "inr", "line_items": [{"quantity": 2}, {"quantity": 1}]},
        {"id": 450789470, "order_number": 1002, "total_price": "10.00", "currency": "USD"},
        {"name": "no id: left out"},
        "not an order",
    ]
}  # fmt: skip


def test_shopify_orders_become_cards() -> None:
    first, second = rules.shopify_orders(SHOPIFY, "acme")
    assert (first.id, first.number, first.payment, first.fulfillment) == (
        "450789469", "#1001", "paid", None)  # fmt: skip
    assert (first.total, first.currency, first.items) == ("1299.00", "INR", 3)
    assert first.created_at == datetime(2026, 11, 1, 4, 30, tzinfo=UTC)
    assert first.url == "https://acme.myshopify.com/admin/orders/450789469"
    assert (second.number, second.items, second.created_at) == ("#1002", 0, None)


def test_odd_answers_give_no_cards() -> None:
    for data in (None, {}, {"orders": "x"}, [], "text"):
        assert rules.shopify_orders(data, "acme") == []
        assert rules.stripe_payments(data) == []
        assert rules.stripe_customer_ids(data) == []


def test_at_most_five_cards() -> None:
    many = {"orders": [{"id": n, "total_price": "1"} for n in range(9)]}
    assert len(rules.shopify_orders(many, "acme")) == rules.CARD_LIMIT
    charges = {"data": [{"id": f"ch_{n}", "amount": 100, "currency": "inr"} for n in range(9)]}
    assert len(rules.stripe_payments(charges)) == rules.CARD_LIMIT


@pytest.mark.parametrize(
    ("minor", "currency", "shown"),
    [(129900, "inr", "1299.00"), (5, "usd", "0.05"), (500, "jpy", "500"), (0, "eur", "0.00")],
)
def test_stripe_amounts(minor: int, currency: str, shown: str) -> None:
    assert rules.stripe_amount(minor, currency) == shown


def test_stripe_charges_become_cards() -> None:
    data = {"data": [
        {"id": "ch_1", "amount": 49900, "currency": "inr", "status": "succeeded",
         "refunded": True, "created": 1762000000, "description": "Order #1001",
         "payment_intent": "pi_9"},
        {"id": "ch_2", "amount": "not a number"},
    ]}  # fmt: skip
    (card,) = rules.stripe_payments(data)
    assert (card.id, card.amount, card.currency, card.status, card.refunded) == (
        "ch_1", "499.00", "INR", "succeeded", True)  # fmt: skip
    assert card.created_at == datetime.fromtimestamp(1762000000, UTC)
    assert card.url == "https://dashboard.stripe.com/payments/pi_9"
    assert rules.stripe_customer_ids({"data": [{"id": "cus_1"}, {"x": 1}]}) == ["cus_1"]


def test_credentials_are_checked_for_shape() -> None:
    assert rules.check_secret(App.stripe, "  rk_live_abc  ") == "rk_live_abc"
    assert rules.check_secret(App.shopify, "shpat_123") == "shpat_123"
    for app, bad in ((App.stripe, "pk_live_abc"), (App.stripe, ""), (App.shopify, " "),
                     (App.shopify, "x" * 301)):  # fmt: skip
        with pytest.raises(rules.BadCredentials):
            rules.check_secret(app, bad)
    assert rules.secret_hint("rk_live_abcd1234") == "…1234"
