"""Resolution receipts (differentiator plan #5): what was done on a ticket, and whether
the result was checked, not only "resolved". Pure rules.

- The workspace setting is `off` (the default: no receipts asked for), `optional` (an
  agent may add one when resolving), or `required` (resolving needs one) for every team
  or only for the teams listed.
- A receipt has the customer's request, the action taken and the result in a few words,
  how the result was verified (`unverified`, `agent`, or read from `shopify` / `stripe`),
  whether the customer was told, and an optional pending item ("Bank settlement").
- It can be changed while the ticket stays resolved, for 7 days after resolving.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

REQUEST_MAX = 300
ACTION_MAX = 500
RESULT_MAX = 300
PENDING_MAX = 200
TEAMS_MAX = 200
EDIT_WINDOW = timedelta(days=7)
NOTIFIED_WINDOW = timedelta(hours=24)


class Mode(StrEnum):
    off = "off"
    optional = "optional"
    required = "required"


class Verified(StrEnum):
    unverified = "unverified"
    agent = "agent"  # the agent checked it by hand
    shopify = "shopify"  # read from the shop's order (never written)
    stripe = "stripe"  # read from the Stripe payment (never written)


# Set only by the Verify button (a read from the connected app), never typed in.
READ_FROM_APP = frozenset({Verified.shopify, Verified.stripe})

REQUIRED = "This ticket needs a resolution receipt: say what was done before resolving."
TURNED_OFF = "Resolution receipts are off in this workspace."
TOO_LATE = "A receipt can be changed for 7 days after resolving, while the ticket is resolved."
NOT_RESOLVED = "This ticket isn't resolved, so it has no receipt to change."


class ResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class Setting:
    mode: Mode = Mode.off
    # With `required`: only these teams need a receipt (empty: every team).
    required_teams: tuple[UUID, ...] = field(default_factory=tuple)

    def asked(self) -> bool:
        return self.mode is not Mode.off

    def needed(self, team_id: UUID | None) -> bool:
        """Resolving a ticket of this team needs a receipt."""
        if self.mode is not Mode.required:
            return False
        return not self.required_teams or team_id in self.required_teams


def setting(mode: str, teams: Sequence[UUID]) -> Setting:
    try:
        checked = Mode(mode)
    except ValueError as exc:
        raise ResolutionError("Receipts are off, optional or required.") from exc
    if len(teams) > TEAMS_MAX:
        raise ResolutionError(f"List at most {TEAMS_MAX} teams.")
    if teams and checked is not Mode.required:
        raise ResolutionError("Teams are only listed when receipts are required.")
    return Setting(checked, tuple(dict.fromkeys(teams)))


def _text(value: Any, label: str, limit: int, *, needed: bool) -> str | None:
    if value is None:
        if needed:
            raise ResolutionError(f"Say {label}.")
        return None
    text = " ".join(str(value).split())
    if not text:
        if needed:
            raise ResolutionError(f"Say {label}.")
        return None
    if len(text) > limit:
        raise ResolutionError(f"Keep {label} to {limit} characters.")
    return text


@dataclass(frozen=True)
class Receipt:
    request: str | None
    action_taken: str
    result: str | None
    verified: Verified
    customer_notified: bool | None  # None: work it out from the replies
    pending_item: str | None


def receipt(body: Mapping[str, Any]) -> Receipt:
    """A receipt typed by an agent. The action taken is the one part always needed."""
    verified = Verified(body.get("verified") or Verified.unverified)
    if verified in READ_FROM_APP:
        raise ResolutionError("Shopify or Stripe checks come from the Verify button.")
    notified = body.get("customer_notified")
    return Receipt(
        request=_text(body.get("request"), "what the customer asked", REQUEST_MAX, needed=False),
        action_taken=_text(body.get("action_taken"), "what was done", ACTION_MAX, needed=True)
        or "",
        result=_text(body.get("result"), "the result", RESULT_MAX, needed=False),
        verified=verified,
        customer_notified=None if notified is None else bool(notified),
        pending_item=_text(body.get("pending_item"), "what is pending", PENDING_MAX, needed=False),
    )


def changes(body: Mapping[str, Any]) -> dict[str, Any]:
    """An edit: only the keys sent. `action_taken` can't be emptied."""
    out: dict[str, Any] = {}
    limits = {
        "request": ("what the customer asked", REQUEST_MAX),
        "result": ("the result", RESULT_MAX),
        "pending_item": ("what is pending", PENDING_MAX),
    }
    for key, (label, limit) in limits.items():
        if key in body:
            out[key] = _text(body[key], label, limit, needed=False)
    if "action_taken" in body:
        out["action_taken"] = _text(body["action_taken"], "what was done", ACTION_MAX, needed=True)
    if "verified" in body:
        verified = Verified(body["verified"] or Verified.unverified)
        if verified in READ_FROM_APP:
            raise ResolutionError("Shopify or Stripe checks come from the Verify button.")
        out["verified"] = verified.value
    if "customer_notified" in body:
        out["customer_notified"] = bool(body["customer_notified"])
    return out


def editable(resolved_at: datetime | None, status: str, now: datetime) -> bool:
    return status == "resolved" and resolved_at is not None and now - resolved_at <= EDIT_WINDOW


def notified(last_public_reply: datetime | None, now: datetime) -> bool:
    """Told the customer: a public reply went out in the 24 hours before resolving."""
    return last_public_reply is not None and now - last_public_reply <= NOTIFIED_WINDOW


def webhook_part(row: Mapping[Any, Any] | None, *, with_text: bool) -> dict[str, Any] | None:
    """The `resolution` object of `ticket.resolved`: no free text unless asked for."""
    if row is None:
        return None
    part: dict[str, Any] = {
        "verified": row["verified"],
        "verified_at": row["verified_at"].isoformat() if row["verified_at"] else None,
        "customer_notified": row["customer_notified"],
        "pending": row["pending_item"] is not None,
    }
    if with_text:
        part |= {k: row[k] for k in ("request", "action_taken", "result", "pending_item")}
    return part
