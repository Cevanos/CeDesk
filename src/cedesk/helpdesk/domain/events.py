"""Events written to the outbox (md 3.2 step 5, PL4, PL5).

Pure: builds the payload from a ticket row; the service writes it in the same
transaction as the change. The payload carries ids and short labels only, never message
bodies or customer data beyond what a webhook receiver needs to look the ticket up.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID


class Event(StrEnum):
    ticket_created = "ticket.created"
    ticket_updated = "ticket.updated"
    ticket_resolved = "ticket.resolved"
    message_created = "message.created"
    assignment_changed = "assignment.changed"


# Changes that make a `ticket.updated` (PL4): status (other than resolving), priority,
# team, assignee, type. Also subject, fields and tags, so integrations stay in sync.
WATCHED = ("subject", "status", "priority", "type_id", "team_id", "assignee_id", "fields", "tags")


def ticket_brief(ticket: Mapping[Any, Any]) -> dict[str, Any]:
    """PL5: `ticket: {id, subject, status, priority, team, topic}` plus the number."""
    return {
        "id": str(ticket["id"]),
        "number": ticket["number"],
        "subject": ticket["subject"],
        "status": ticket["status"],
        "priority": ticket["priority"],
        "team": ticket["team_name"],
        "topic": ticket["topic"],
    }


def changed(before: Mapping[Any, Any], after: Mapping[Any, Any]) -> list[str]:
    return [key for key in WATCHED if before.get(key) != after.get(key)]


def after_change(
    before: Mapping[Any, Any], after: Mapping[Any, Any], actor: UUID | None
) -> list[tuple[Event, dict[str, Any]]]:
    """The events one ticket edit produces, in order. Resolving gives `ticket.resolved`
    instead of `ticket.updated` for the status; any other change in the same edit still
    gives `ticket.updated`."""
    keys = changed(before, after)
    events: list[tuple[Event, dict[str, Any]]] = []
    resolved = "status" in keys and after["status"] == "resolved"
    other = [k for k in keys if not (resolved and k == "status")]
    if other:
        events.append((Event.ticket_updated, {"changes": other}))
    if resolved:
        events.append((Event.ticket_resolved, {}))
    if "assignee_id" in keys:
        moved = assignment(before["assignee_id"], after["assignee_id"], actor)
        events.append((Event.assignment_changed, moved))
    return events


def assignment(old: UUID | None, new: UUID | None, actor: UUID | None) -> dict[str, Any]:
    """`by` is the member who made the change; null when routing chose."""
    return {
        "from": str(old) if old else None,
        "to": str(new) if new else None,
        "by": str(actor) if actor else None,
    }


def message(message_id: UUID, kind: str, author: UUID | None) -> dict[str, Any]:
    """No body: receivers read the message through the API with their own rights."""
    author_id = str(author) if author else None
    return {"message": {"id": str(message_id), "kind": kind, "author_member_id": author_id}}


def payload(event: Event, ticket: Mapping[Any, Any], extra: Mapping[Any, Any]) -> dict[str, Any]:
    return {"event": event.value, "ticket": ticket_brief(ticket), **extra}
