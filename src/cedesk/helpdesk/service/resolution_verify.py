"""The Verify button of a resolution receipt (differentiator plan #5): reads the
customer's Shopify order or Stripe payment, as the ticket's sidebar cards do, and puts
what it reports in the receipt. Nothing is ever written to the shop or to Stripe.

A module of its own: the apps service reaches the tickets service (through Cevanos),
which saves receipts when resolving."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping

from cedesk.cevanos_client import Cevanos
from cedesk.crypto import Sealer
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import resolution as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import resolution as receipt_repo
from cedesk.helpdesk.service import apps, audit
from cedesk.helpdesk.service import resolution as receipts
from cedesk.helpdesk.service.apps import AppHttp
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require


def _order_result(order: Any) -> str:
    paid = order.payment or "unknown"
    shipped = order.fulfillment or "not fulfilled"
    return f"Order {order.number}: payment {paid}, {shipped}, total {order.total} {order.currency}"


def _payment_result(payment: Any) -> str:
    state = "refunded" if payment.refunded else payment.status
    return f"Payment of {payment.amount} {payment.currency}: {state}"


async def verify(
    sm: Sessionmaker,
    sealer: Sealer,
    http: AppHttp,
    caller: Caller,
    ticket_id: UUID,
    *,
    order_id: str | None = None,
    payment_id: str | None = None,
    cevanos: Cevanos | None = None,
) -> RowMapping:
    """The Verify button: reads the customer's order (Shopify) or payment (Stripe), as
    the ticket's sidebar card does, and puts what it says in the receipt's result."""
    require(caller, Action.reply)
    if (order_id is None) == (payment_id is None):
        raise Invalid("Send an order_id or a payment_id.")
    now = datetime.now(UTC)
    async with workspace_session(sm, caller.workspace_id) as s:
        await receipts.editable_ticket(s, caller, ticket_id, now)
    if order_id is not None:
        card = await apps.orders(sm, sealer, http, caller, ticket_id, cevanos)
        kind, wanted = rules.Verified.shopify, card.source == "shopify"
        match = next((o for o in card.orders if o.id == order_id), None)
        result = _order_result(match) if match else None
    else:
        card = await apps.payments(sm, sealer, http, caller, ticket_id, cevanos)
        kind, wanted = rules.Verified.stripe, card.source == "stripe"
        pay = next((p for p in card.payments if p.id == payment_id), None)
        result = _payment_result(pay) if pay else None
    if not card.available:
        raise Conflict(card.message or "The app couldn't be read; try again later.")
    if not wanted:
        raise Conflict(f"Verify reads the {kind.value.title()} app, which isn't the source here.")
    if result is None:
        raise NotFound("That isn't one of the customer's latest orders or payments.")
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await receipts.editable_ticket(s, caller, ticket_id, now)
        await receipt_repo.update(
            s, ticket_id,
            {"verified": kind.value, "verified_at": now, "result": result[: rules.RESULT_MAX]},
        )  # fmt: skip
        await audit.record(
            s, caller, Area.resolutions, "verified", f"Ticket #{ticket['number']}",
            target_id=ticket_id, detail={"verified": kind.value},
        )  # fmt: skip
        found = await receipt_repo.get(s, ticket_id)
    assert found is not None
    return found
