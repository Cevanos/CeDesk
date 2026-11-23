"""Bulk ticket changes (feature-gap plan 1.3); rules in domain/bulk.py.

Needs `bulk_edit`. Each ticket then goes through `tickets.change_ticket`, exactly as
`PATCH /tickets/{id}` (or the snooze routes) would: the same rights (`change_fields`,
`assign_*`), the role's team scope, rules, SLA, events and the ticket's own history
lines. Each ticket is its own transaction: one that fails (not found, another team's,
not allowed, a resolved ticket that can't be snoozed...) is reported with its error and
never undoes or stops the others. One audit entry (area `tickets`) records the request:
the action, its value, the tickets, and how many succeeded and failed.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import bulk as rules
from cedesk.helpdesk.domain import snooze as snooze_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.service import audit, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, ServiceError
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk.bulk")


@dataclass(frozen=True)
class Outcome:
    ticket_id: UUID
    ok: bool
    code: str | None = None  # as the error envelope's `code`: NotFound, Forbidden, ...
    message: str | None = None


def _code(exc: ServiceError) -> str:
    return exc.code or type(exc).__name__


async def change_many(
    sm: Sessionmaker,
    caller: Caller,
    ticket_ids: Sequence[UUID],
    action: rules.BulkAction,
    given: dict[str, Any],
    *,
    spend: Callable[[], None] | None = None,
) -> list[Outcome]:
    """`given`: the body's value fields that were sent (see domain/bulk.py NEEDS).
    `spend` uses one request of the caller's budget (it raises when the budget is used
    up). It runs after the role and the body are checked, so a refused or malformed
    request costs nothing."""
    require(caller, Action.bulk_edit)
    try:
        ids = rules.unique_ids(ticket_ids)
        changes = rules.changes(action, given)
        if changes.get("snooze_until") is not None:  # checked once, not on every ticket
            snooze_rules.check_until(changes["snooze_until"], datetime.now(UTC))
    except (rules.BulkError, snooze_rules.SnoozeError) as exc:
        raise Invalid(str(exc)) from exc
    if spend is not None:
        spend()

    results: list[Outcome] = []
    for ticket_id in ids:
        try:
            await tickets.change_ticket(sm, caller, ticket_id, changes)
        except ServiceError as exc:
            results.append(Outcome(ticket_id, False, _code(exc), exc.message))
        except Exception:  # never lose the others' results to one unexpected failure
            logger.exception("bulk change failed on one ticket", extra={"ticket_id": ticket_id})
            results.append(Outcome(ticket_id, False, "Internal", "Something went wrong."))
        else:
            results.append(Outcome(ticket_id, True))

    succeeded = sum(r.ok for r in results)
    async with workspace_session(sm, caller.workspace_id) as s:
        await audit.record(
            s, caller, Area.tickets, "bulk_changed", f"{len(ids)} tickets: {action.value}",
            detail={
                "action": action.value,
                "value": rules.audit_value(action, given),
                "ticket_ids": [str(i) for i in ids],
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
            },
        )  # fmt: skip
    return results
