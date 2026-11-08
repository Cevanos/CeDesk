"""What a new workspace starts with, taken from the mock (md section 4: "Seeds for a new
workspace"). Admins can rename, change or delete all of it afterwards.

Phase 1 seeds teams and ticket types only. Rules, macros, saved replies, SLA and email
addresses join this list when those features are built.
"""

from dataclasses import dataclass

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
