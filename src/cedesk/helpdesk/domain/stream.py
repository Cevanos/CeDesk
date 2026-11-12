"""Live updates (md C11, P7): what is published to a stream, and how it is written on the wire.
No database here.

Two audiences, never mixed:

- **Agents** (`GET /stream`): ticket events, new messages, assignments, notifications and the
  state of a Cevanos chat. A role limited to one team's tickets only gets that team's.
- **Portal customers** (`GET /portal/stream`): only what the customer could already read on
  their own ticket: a customer, agent or AI message (never a note or a system line) and a
  change of its status.

**A published event says that something changed, never what it says.** It carries ids (the
ticket, the message) and the names of what changed, never a subject, a message text, an
address or a customer's name. The client fetches the ticket through the normal API, which
applies the same permissions as always. So a stream can't show anyone more than they could
already open, and a missed event costs one refetch, not a leak.

Only the kinds in `AGENT_KINDS` and `CUSTOMER_KINDS` are ever written. `security.alert` (an
email to the Admins, not a ticket event) is not one of them and is never published.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from cedesk.helpdesk.domain.events import Event

TICKET_LIFETIME = timedelta(seconds=60)  # a stream ticket is used within a minute, once
RETENTION = timedelta(hours=24)  # events are kept this long, for Last-Event-ID
POLL_SECONDS = 1.0  # how often an open stream looks for new events
HEARTBEAT_SECONDS = 15.0  # a comment line, so proxies and browsers keep the connection
RECHECK_SECONDS = 60.0  # how often the sign-in behind a stream is checked again
OVERLAP = timedelta(seconds=30)  # events this recent are read again, in case one committed late
OVERLAP_SEQS = 5000  # ...but only among the last this-many ids
BATCH = 100  # events sent per poll
SEEN = 2000  # ids remembered, so the overlap never sends one twice
MAX_STREAMS_PER_MEMBER = 5  # open streams per signed-in person
RETRY_MS = 3000  # what an EventSource waits before it reconnects

# Kinds written for agents. `ticket.*`, `message.created` and `assignment.changed` come from
# the outbox events of the same name; `chat.*` from the Cevanos connector; `notification`
# from `hd_notification`.
AGENT_KINDS = frozenset(
    {
        "ticket.created",
        "ticket.updated",
        "ticket.resolved",
        "message.created",
        "assignment.changed",
        "chat.accepted",
        "chat.taken_over",
        "chat.closed",
        "chat.reopened",
        "notification",
    }
)
# Kinds a portal customer gets, for their own tickets only.
CUSTOMER_KINDS = frozenset({"message.created", "ticket.updated", "ticket.resolved"})
# Messages a customer may see: never a private note or a system line.
CUSTOMER_MESSAGE_KINDS = frozenset({"customer", "agent", "ai"})


@dataclass(frozen=True)
class Published:
    """What one outbox event becomes in the stream."""

    agent_kind: str | None
    agent_payload: Mapping[str, Any]
    customer_kind: str | None = None
    customer_payload: Mapping[str, Any] | None = None


def _ticket_ids(payload: Mapping[str, Any]) -> dict[str, Any]:
    ticket = payload.get("ticket") or {}
    out: dict[str, Any] = {}
    if ticket.get("id"):
        out["ticket_id"] = str(ticket["id"])
    if ticket.get("number") is not None:
        out["number"] = ticket["number"]
    return out


def from_outbox(event: str, payload: Mapping[str, Any]) -> Published | None:
    """The stream events for an outbox event, or None when it isn't published (a contact
    change, an Admin security alert, anything unknown)."""
    base = _ticket_ids(payload)
    if "ticket_id" not in base:
        return None  # not about a ticket
    if event == Event.ticket_created.value:
        return Published("ticket.created", base)
    if event == Event.ticket_updated.value:
        changes = [str(k) for k in payload.get("changes") or [] if isinstance(k, str)]
        mine = {**base, "changes": changes}
        # The customer sees the status they already see; other changes are not theirs.
        customer = mine if "status" in changes else None
        return Published(
            "ticket.updated",
            mine,
            "ticket.updated" if customer else None,
            {**base, "changes": ["status"]} if customer else None,
        )
    if event == Event.ticket_resolved.value:
        return Published("ticket.resolved", base, "ticket.resolved", base)
    if event == Event.message_created.value:
        message = payload.get("message") or {}
        kind = str(message.get("kind") or "")
        ids = {**base, "message_id": str(message.get("id") or ""), "message_kind": kind}
        shown = kind in CUSTOMER_MESSAGE_KINDS
        return Published("message.created", ids, "message.created" if shown else None,
                         ids if shown else None)  # fmt: skip
    if event == Event.assignment_changed.value:
        moved = {k: (str(payload[k]) if payload.get(k) else None) for k in ("from", "to", "by")}
        return Published("assignment.changed", {**base, **moved})
    if event == Event.handover_accepted.value:
        return Published("chat.accepted", {**base, "by": str(payload.get("by") or "")})
    return None  # contact.updated, security.alert, and anything added later


# The wire (Server-Sent Events). One frame per event, a blank line ends it.


def frame(seq: int, kind: str, data: Mapping[str, Any]) -> str:
    """`id` is the event's number: a client that reconnects sends it back as Last-Event-ID."""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    return f"id: {seq}\nevent: {kind}\ndata: {body}\n\n"


def hello() -> str:
    """The first thing sent: how long an EventSource waits before it reconnects."""
    return f"retry: {RETRY_MS}\n\n"


def heartbeat() -> str:
    return ": ping\n\n"


def reset() -> str:
    """The client's Last-Event-ID is older than what is kept: refetch what you show, then go on."""
    return "event: reset\ndata: {}\n\n"


def end(reason: str) -> str:
    """The stream is over for a reason the client should act on (signed out). Not a retry."""
    return f"event: end\ndata: {json.dumps({'reason': reason})}\n\n"


_EVENT_ID = re.compile(r"^\d{1,18}$")


def parse_last_event_id(value: str | None) -> int | None:
    """The `Last-Event-ID` a client sent; None for nothing or something that isn't one."""
    found = (value or "").strip()
    return int(found) if _EVENT_ID.match(found) else None


# Stream tickets: a browser's EventSource can't send an Authorization header, so a signed-in
# client first asks for a ticket (with its token) and opens the stream with that. The ticket is
# random, lasts a minute and works once, so the session token never goes in an address. It has
# the same shape as a session token (`<workspace id>.<random>`, see `domain.auth.new_token`),
# because the lookup needs the workspace; only its SHA-256 is stored.
