"""Bulk ticket changes (feature-gap plan 1.3), the rules that need no database.

One request carries up to MAX_TICKETS ticket ids and one action. Each ticket is changed
on its own, through the same path and checks as a single edit (`PATCH /tickets/{id}`),
so rules, SLA, events and the ticket's history behave the same; one ticket failing never
stops or undoes the others, and every ticket gets its own result.
"""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

MAX_TICKETS = 100
PER_MINUTE = 10  # bulk requests per member per minute (each can touch 100 tickets)


class BulkAction(StrEnum):
    assign = "assign"  # assignee_id (null: unassign)
    set_team = "set_team"  # team_id (null: no team)
    set_status = "set_status"  # status
    set_priority = "set_priority"  # priority
    add_tags = "add_tags"  # tags
    remove_tags = "remove_tags"  # tags
    snooze = "snooze"  # until
    unsnooze = "unsnooze"


# Which body field each action needs (None: none).
NEEDS: dict[BulkAction, str | None] = {
    BulkAction.assign: "assignee_id",
    BulkAction.set_team: "team_id",
    BulkAction.set_status: "status",
    BulkAction.set_priority: "priority",
    BulkAction.add_tags: "tags",
    BulkAction.remove_tags: "tags",
    BulkAction.snooze: "until",
    BulkAction.unsnooze: None,
}
VALUE_FIELDS = frozenset(f for f in NEEDS.values() if f is not None)


class BulkError(ValueError):
    """The message is shown to the member."""


def unique_ids(ids: Sequence[UUID]) -> list[UUID]:
    """In the order sent, each once."""
    out = list(dict.fromkeys(ids))
    if not out:
        raise BulkError("Pick at least one ticket.")
    if len(out) > MAX_TICKETS:
        raise BulkError(f"Change at most {MAX_TICKETS} tickets at once.")
    return out


def changes(action: BulkAction, given: dict[str, Any]) -> dict[str, Any]:
    """The `change_ticket` changes for one action. `given`: the body fields that were sent
    (only those), so null can mean "clear" for assignee and team."""
    needed = NEEDS[action]
    extra = sorted(set(given) - ({needed} if needed else set()))
    if extra:
        raise BulkError(f"{action.value} doesn't take {', '.join(extra)}.")
    if needed is not None and needed not in given:
        raise BulkError(f"{action.value} needs {needed}.")
    value = given.get(needed) if needed else None
    if action in (BulkAction.set_status, BulkAction.set_priority, BulkAction.snooze):
        if value is None:
            raise BulkError(f"{needed} can't be null.")
    if action in (BulkAction.add_tags, BulkAction.remove_tags) and not value:
        raise BulkError("Give at least one tag.")
    match action:
        case BulkAction.assign:
            return {"assignee_id": value}
        case BulkAction.set_team:
            return {"team_id": value}
        case BulkAction.set_status:
            return {"status": str(value)}
        case BulkAction.set_priority:
            return {"priority": str(value)}
        case BulkAction.add_tags:
            return {"add_tags": list(value or [])}
        case BulkAction.remove_tags:
            return {"remove_tags": list(value or [])}
        case BulkAction.snooze:
            assert isinstance(value, datetime)
            return {"snooze_until": value}
        case BulkAction.unsnooze:
            return {"snooze_until": None}


def audit_value(action: BulkAction, given: dict[str, Any]) -> Any:
    """What the audit entry records as the action's value (ids, enums, tags, a time)."""
    needed = NEEDS[action]
    if needed is None:
        return None
    value = given.get(needed)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [str(v) for v in value]
    return None if value is None else str(value)
