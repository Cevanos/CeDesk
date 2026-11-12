"""Rules for the ticket tools (md X1-X4, X6, X8, X9): merge, links, child tickets,
approvals, tasks and the time log. No I/O here."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

TEXT_MAX = 500  # a task, an approval request, a decision note, a time-log note
MINUTES_MAX = 1440  # one time-log entry is at most a day
AMOUNT_MAX = Decimal("999999999999.99")  # numeric(14, 2)
MORNING = time(9, 0)  # "tomorrow" on a task means 9:00 the next day, workspace time


class DueIn(StrEnum):
    """The quick due times on a new task (X8); any other time is sent as `due_at`."""

    one_hour = "1h"
    four_hours = "4h"
    tomorrow = "tomorrow"


class Decision(StrEnum):
    approve = "approve"
    reject = "reject"


class ToolError(ValueError):
    """A request the rules refuse; the message is shown to the agent."""


def due_at(due_in: DueIn, now: datetime, timezone: str) -> datetime:
    """When a task with a quick due time is due. `now` is timezone-aware."""
    if due_in is DueIn.one_hour:
        return now + timedelta(hours=1)
    if due_in is DueIn.four_hours:
        return now + timedelta(hours=4)
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    return datetime.combine(local.date() + timedelta(days=1), MORNING, tzinfo=zone)


def clean_text(value: str | None, what: str, *, required: bool) -> str | None:
    """Trimmed text of at most TEXT_MAX characters; empty means none."""
    text = (value or "").strip()
    if not text:
        if required:
            raise ToolError(f"Write the {what}.")
        return None
    if len(text) > TEXT_MAX:
        raise ToolError(f"The {what} is at most {TEXT_MAX} characters.")
    return text


def check_minutes(minutes: int) -> int:
    if not 1 <= minutes <= MINUTES_MAX:
        raise ToolError(f"Log between 1 and {MINUTES_MAX} minutes.")
    return minutes


def money(amount: Decimal, currency: str) -> str:
    """₹1,500 or ₹1,500.50 for rupees; "USD 20.50" for anything else."""
    whole = amount == amount.to_integral_value()
    number = f"{amount:,.0f}" if whole else f"{amount:,.2f}"
    return f"₹{number}" if currency == "INR" else f"{currency} {number}"


def check_amount(amount: Decimal | None, currency: str | None) -> tuple[Decimal | None, str | None]:
    """An approval's amount (rounded to paise or cents) and its currency (default INR)."""
    if amount is None:
        return None, None
    code = (currency or "INR").strip().upper()
    if len(code) != 3 or not code.isalpha() or not code.isascii():
        raise ToolError("currency is a 3-letter code such as INR or USD.")
    value = amount.quantize(Decimal("0.01"))
    if not Decimal("0") < value <= AMOUNT_MAX:
        raise ToolError("The amount must be more than 0.")
    return value, code


def default_what(amount: Decimal | None, currency: str | None, order_id: object) -> str | None:
    """The request text when the agent wrote none (X6): from the amount and the order."""
    parts = []
    if amount is not None and currency is not None:
        parts.append(f"Refund of {money(amount, currency)}")
    if isinstance(order_id, str | int) and str(order_id).strip():
        order = str(order_id).strip().lstrip("#")
        parts.append(f"for order #{order}" if parts else f"Order #{order}")
    return " ".join(parts) or None


def link_pair(one: UUID, other: UUID) -> tuple[UUID, UUID]:
    """A link is stored once per pair, the smaller id first (the table checks it)."""
    if one == other:
        raise ToolError("A ticket can't be linked to itself.")
    return (one, other) if one < other else (other, one)


def moved_work(tasks: int, approvals: int) -> str | None:
    """'2 open tasks, 1 waiting approval' for the merge line; None when nothing moved."""
    parts = []
    if tasks:
        parts.append(f"{tasks} open task" + ("" if tasks == 1 else "s"))
    if approvals:
        parts.append(f"{approvals} waiting approval" + ("" if approvals == 1 else "s"))
    return ", ".join(parts) or None


def overdue_note(ticket_label: str, task: str) -> str:
    return f"Task overdue on {ticket_label}: {task}"[:500]
