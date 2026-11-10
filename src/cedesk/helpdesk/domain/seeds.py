"""What a new workspace starts with, taken from the mock (md section 4: "Seeds for a new
workspace"). Admins can rename, change or delete all of it afterwards.

Teams, ticket types, saved replies, rules and macros so far. SLA and email addresses join
this list when those features are built.
"""

from dataclasses import dataclass
from typing import Any

from cedesk.helpdesk.domain.tickets import FieldDef, FieldKind


@dataclass(frozen=True)
class SeedTeam:
    name: str
    auto_assign: bool = True
    max_open_per_agent: int = 5


@dataclass(frozen=True)
class SeedType:
    name: str
    fields: tuple[FieldDef, ...] = ()


TEAMS: tuple[SeedTeam, ...] = (SeedTeam("Billing"), SeedTeam("Shipping"), SeedTeam("Tech"))

_ORDER_ID = FieldDef("order_id", "Order ID", FieldKind.text)

TICKET_TYPES: tuple[SeedType, ...] = (
    SeedType(
        "Refund",
        (
            _ORDER_ID,
            FieldDef("amount", "Refund amount (₹)", FieldKind.number),
            FieldDef(
                "reason",
                "Reason",
                FieldKind.select,
                ("Damaged", "Wrong item", "Late delivery", "Changed mind"),
            ),
        ),
    ),
    SeedType(
        "Shipping issue",
        (
            _ORDER_ID,
            FieldDef(
                "courier",
                "Courier",
                FieldKind.select,
                ("Delhivery", "Blue Dart", "Ekart", "India Post"),
            ),
            FieldDef("tracking", "Tracking number", FieldKind.text),
        ),
    ),
    SeedType(
        "Technical problem",
        (
            FieldDef("device", "Device", FieldKind.select, ("Android", "iPhone", "Desktop")),
            FieldDef("app_version", "App version", FieldKind.text),
            FieldDef("error", "Error message", FieldKind.text),
        ),
    ),
    SeedType("Billing question", (FieldDef("invoice", "Invoice number", FieldKind.text),)),
    SeedType("General"),
)


@dataclass(frozen=True)
class SeedReply:
    shortcut: str
    body: str


# The mock's four saved replies (md W19), word for word.
SAVED_REPLIES: tuple[SeedReply, ...] = (
    SeedReply(
        "refund",
        "Refunds are processed within 5–7 business days to your original payment method. "  # noqa: RUF001 (the mock's dash)
        "You will get an email when it is done.",
    ),
    SeedReply(
        "shipping",
        "Orders ship within 2 business days. You will get a tracking link by email as soon as "
        "your order leaves our warehouse.",
    ),
    SeedReply(
        "photo",
        "Could you please share a photo of the product and the packaging? It helps us process "
        "your request faster.",
    ),
    SeedReply(
        "thanks",
        "Thank you for contacting us, {{customer.first_name}}! Is there anything else I can "
        "help you with?",
    ),
)


@dataclass(frozen=True)
class SeedRule:
    """Actions name their ticket type and team (`"type": "Refund"`); seeding swaps the
    names for the workspace's ids, and skips the rule if one of them is missing."""

    name: str
    event: str
    conditions: tuple[dict[str, Any], ...]
    actions: tuple[dict[str, Any], ...]


# The mock's six rules (md W15), in the mock's order. Rule 1 keeps an Urgent ticket Urgent,
# like rule 6; rule 3 sets the type only when no rule or person set one (the mock's
# "if the type is General").
RULES: tuple[SeedRule, ...] = (
    SeedRule(
        "Refund requests to Billing",
        "created",
        ({"kind": "text_contains_any", "words": ["refund"]},),
        (
            {"kind": "set_type", "type": "Refund"},
            {"kind": "set_team", "team": "Billing"},
            {"kind": "set_priority", "priority": "high", "keep_urgent": True},
        ),
    ),
    SeedRule(
        "Order tracking to Shipping",
        "created",
        ({"kind": "text_contains_any", "words": ["where is my order", "tracking"]},),
        (
            {"kind": "set_type", "type": "Shipping issue"},
            {"kind": "set_team", "team": "Shipping"},
        ),
    ),
    SeedRule(
        "billing@ emails to Billing",
        "created",
        (
            {"kind": "channel_in", "channels": ["email"]},
            {"kind": "to_address_contains", "text": "billing@"},
        ),
        (
            {"kind": "set_team", "team": "Billing"},
            {"kind": "set_type", "type": "Billing question", "only_if_empty": True},
        ),
    ),
    SeedRule(
        "App errors to Tech",
        "created",
        ({"kind": "text_contains_any", "words": ["crash", "error"]},),
        (
            {"kind": "set_type", "type": "Technical problem"},
            {"kind": "set_team", "team": "Tech"},
        ),
    ),
    SeedRule(
        "Reopen when the customer replies",
        "customer_replied",
        ({"kind": "status_in", "statuses": ["pending"]},),
        ({"kind": "set_status", "status": "open"},),
    ),
    SeedRule(
        "Upset customer goes first",
        "created",
        ({"kind": "sentiment_in", "sentiments": ["negative"]},),
        (
            {"kind": "set_priority", "priority": "high", "keep_urgent": True},
            {"kind": "add_tag", "tag": "upset"},
        ),
    ),
)


@dataclass(frozen=True)
class SeedMacro:
    """A team step names its team (`"team": "Tech"`); seeding swaps the name for the id,
    and skips the macro if that team is missing."""

    name: str
    steps: tuple[dict[str, Any], ...]


def _reply(shortcut: str) -> str:
    return next(r.body for r in SAVED_REPLIES if r.shortcut == shortcut)


# The mock's three macros (md W18), in the mock's order.
MACROS: tuple[SeedMacro, ...] = (
    SeedMacro(
        "Refund approved",
        (
            {
                "kind": "reply",
                "text": "Hi {{customer.first_name}}, your refund of ₹{{fields.amount}} for order "
                "{{fields.order_id}} is approved. The money will reach your original payment "
                "method in 5–7 business days.",  # noqa: RUF001 (the mock's dash)
            },
            {"kind": "add_tag", "tag": "refund"},
            {"kind": "set_priority", "priority": "low"},
            {
                "kind": "note",
                "text": "Refund approved. Check the payment reached the customer in 7 days.",
            },
            {"kind": "set_status", "status": "resolved"},
        ),
    ),
    SeedMacro(
        "Ask for a photo",
        (
            {"kind": "reply", "text": _reply("photo")},
            {"kind": "set_status", "status": "pending"},
        ),
    ),
    SeedMacro(
        "Escalate to Tech",
        (
            {"kind": "set_team", "team": "Tech"},
            {"kind": "set_priority", "priority": "high"},
            {"kind": "note", "text": "Escalated by L1 support. Needs a developer to look at it."},
        ),
    ),
)
