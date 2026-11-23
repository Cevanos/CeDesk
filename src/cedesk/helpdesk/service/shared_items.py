"""Who sees and who changes an item that is personal or shared (saved reports, dashboards).

A personal item is its owner's alone: to anyone else it doesn't exist (404). A shared one is
seen by everyone who may use the feature, and only roles with `share_views` change or delete
it. One rule in one place, so the two kinds of item can't drift apart.
"""

from collections.abc import Mapping
from typing import Any

from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.service.errors import Forbidden, NotFound


def visible(caller: Caller, row: Mapping[Any, Any] | None, not_found: str) -> Any:
    """The item if the caller may see it; someone else's personal item doesn't exist."""
    if row is None or (not row["shared"] and row["owner_id"] != caller.member_id):
        raise NotFound(not_found)
    return row


def may_change(caller: Caller, row: Mapping[Any, Any], what: str) -> None:
    """Refuses a change to a shared item without the right to share (`what`: "reports")."""
    if row["shared"] and not caller.can(Action.share_views):
        raise Forbidden(f"Your role can't change shared {what}.")


def can_change(caller: Caller, row: Mapping[Any, Any]) -> bool:
    """For a list: whether to show edit and delete."""
    return caller.can(Action.share_views) if row["shared"] else row["owner_id"] == caller.member_id
