"""What a new workspace starts with, taken from the mock (md section 4: "Seeds for a new
workspace"). Admins can rename, change or delete all of it afterwards.

Teams, ticket types, saved replies, rules and macros so far. SLA and email addresses join
this list when those features are built.

At the end: the demo and sample tickets (md D1-D3).
"""

from dataclasses import dataclass
from typing import Any

from cedesk.helpdesk.domain.tickets import Channel, FieldDef, FieldKind, Priority


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


# Demo and sample tickets (md D1-D3): `POST /demo/seed`, `/demo/reset`, `/demo/samples`.
# Customers write in through a channel, so the rules above, triage, routing and SLA run on
# every one. Agent replies are only on chat and web tickets, which are never emailed; the
# customers have example.com / example.org addresses (reserved, never delivered).

DEMO_SOURCE = "demo"  # `hd_ticket.source` of every demo ticket


@dataclass(frozen=True)
class DemoTicket:
    subject: str
    name: str
    email: str
    body: str
    channel: Channel = Channel.web
    priority: Priority = Priority.medium
    language: str | None = None


@dataclass(frozen=True)
class PastTicket:
    """Resolved, with the customer's CSAT rating, `days_ago`; it took `hours` from the
    customer's message to the reply that solved it."""

    ticket: DemoTicket
    reply: str
    score: int
    comment: str | None
    days_ago: int
    hours: int


@dataclass(frozen=True)
class LiveTicket:
    """Assigned to the person who loads the demo, who is working on it."""

    ticket: DemoTicket
    reply: str | None = None
    note: str | None = None
    task: str | None = None
    minutes: int | None = None  # time logged
    pending: bool = False  # waiting on the customer


@dataclass(frozen=True)
class Handover:
    """A chat the AI handed over, waiting for someone to accept it."""

    ticket: DemoTicket
    summary: str
    reason: str


# The 6 samples ("Load sample tickets", md D3): new and untouched. The first five match
# a rule above (refund, billing, tracking, app error), the last one none.
DEMO_SAMPLES: tuple[DemoTicket, ...] = (
    DemoTicket(
        "Refund for damaged T-shirt (order #1042)",
        "Priya Sharma",
        "priya.sharma@example.com",
        "Hi, my T-shirt from order #1042 arrived torn at the seam. I want a refund.",
        Channel.email,
    ),
    DemoTicket(
        "Invoice copy for October bulk order",
        "Rohan Gupta",
        "rohan.gupta@example.org",
        "Hello,\nPlease send a GST invoice copy for our order #0987 (40 tees).\n\nRegards,\nRohan",
        Channel.email,
    ),
    DemoTicket(
        "Where is my order #1077?",
        "Arjun Mehta",
        "arjun.mehta@example.com",
        "Mera order #1077 kahan hai? Kal aana tha. Mujhe kal trip ke liye chahiye!",
        Channel.chat,
        Priority.urgent,
        "hi",
    ),
    DemoTicket(
        "App crashes on the payment page",
        "Sara Khan",
        "sara.khan@example.com",
        "The app crashes when I press Pay. Error: white screen.",
        Channel.web,
    ),
    DemoTicket(
        "Tracking link not working for #1081",
        "Vikram Rao",
        "vikram.rao@example.org",
        "The tracking link in your email says 'not found'. Where is my parcel?",
        Channel.email,
    ),
    DemoTicket(
        "Do you ship to Sri Lanka?",
        "Meera Iyer",
        "meera.iyer@example.com",
        "Hi, do you deliver to Colombo? How long does it take?",
        Channel.web,
        Priority.low,
    ),
)

# The 5 past tickets (md D1): resolved over the last week, each rated.
DEMO_PAST: tuple[PastTicket, ...] = (
    PastTicket(
        DemoTicket(
            "Coupon SAVE10 not applied at checkout",
            "Ananya Singh",
            "ananya.singh@example.com",
            "The coupon SAVE10 says invalid but it is in your newsletter.",
            Channel.chat,
        ),
        "Sorry Ananya, the code had expired a day early by mistake. I've added ₹100 to your "
        "wallet for the next order.",
        5,
        "Quick and kind, thank you!",
        1,
        1,
    ),
    PastTicket(
        DemoTicket(
            "Wrong size delivered (order #0954)",
            "Karan Malhotra",
            "karan.malhotra@example.org",
            "I ordered L and got S. Can you exchange it?",
            Channel.web,
        ),
        "Hi Karan, the courier will pick up the S tomorrow and bring the L on the same visit.",
        4,
        "Exchange was easy.",
        2,
        5,
    ),
    PastTicket(
        DemoTicket(
            "How do I change my account email?",
            "Divya Nair",
            "divya.nair@example.com",
            "I changed jobs and want my orders to go to my personal email.",
            Channel.chat,
            Priority.low,
        ),
        "Hi Divya, go to Profile, then Email, and enter the new address; we send a code to "
        "confirm it.",
        5,
        None,
        3,
        2,
    ),
    PastTicket(
        DemoTicket(
            "Package marked delivered but not received",
            "Rahul Verma",
            "rahul.verma@example.com",
            "The app says delivered yesterday but nothing came. Nobody rang the bell.",
            Channel.web,
            Priority.high,
        ),
        "Rahul, the courier found it with your building's security. Sorry for the trouble.",
        3,
        "Sorted in the end, but it took a while.",
        5,
        20,
    ),
    PastTicket(
        DemoTicket(
            "Can I add a gift note to my order?",
            "Sneha Kulkarni",
            "sneha.kulkarni@example.org",
            "It's a birthday gift. Can you add a note and remove the price tag?",
            Channel.chat,
            Priority.low,
        ),
        "Done, Sneha: the note is added and the parcel goes without a price tag.",
        5,
        "Lovely, thanks!",
        6,
        3,
    ),
)

# The 4 live tickets (md D1): a reply, a note, a task, time logged; one waits on the customer.
DEMO_LIVE: tuple[LiveTicket, ...] = (
    LiveTicket(
        DemoTicket(
            "Order #1103 delivered to the wrong address",
            "Farhan Ali",
            "farhan.ali@example.com",
            "My order went to my old address in Pune. I live in Bengaluru now.",
            Channel.chat,
            Priority.high,
        ),
        reply="Sorry Farhan. I've asked the courier to collect it and send it to Bengaluru.",
        task="Call the courier to confirm the pickup",
    ),
    LiveTicket(
        DemoTicket(
            "Payment taken twice for order #1110",
            "Pooja Desai",
            "pooja.desai@example.org",
            "I was charged ₹2,499 twice for the same order. Please check.",
            Channel.web,
            Priority.high,
        ),
        note="Two charges in the payment log, 40 seconds apart. Asking finance to refund one.",
        minutes=15,
    ),
    LiveTicket(
        DemoTicket(
            "Need a GST invoice with our company name",
            "Nikhil Joshi",
            "nikhil.joshi@example.com",
            "Our invoice for #1098 has my name. We need the company name and GSTIN on it.",
            Channel.chat,
        ),
        reply="Sure Nikhil. Could you send the company's legal name and GSTIN?",
        pending=True,
    ),
    LiveTicket(
        DemoTicket(
            "Bulk order: 200 T-shirts for an event",
            "Isha Bose",
            "isha.bose@example.org",
            "We need 200 printed T-shirts by the 30th. Can you do it, and at what price?",
            Channel.web,
        ),
        reply="Hi Isha, yes, we can. I'll send a quote with three print options today.",
        note="Big order: checking stock of white tees in M and L.",
        minutes=20,
    ),
)

# The handover (md D1): a chat the AI passed to a person, not accepted yet.
DEMO_HANDOVER = Handover(
    DemoTicket(
        "Return shoes after 35 days",
        "Aditi Menon",
        "aditi.menon@example.com",
        "The sole came off after a month. Can I still return them?",
        Channel.chat,
        Priority.high,
    ),
    summary="Aditi bought running shoes (order #1012) 35 days ago; the sole came off. She "
    "wants a return or a new pair.",
    reason="Returns after the 30-day window need a person to decide.",
)
