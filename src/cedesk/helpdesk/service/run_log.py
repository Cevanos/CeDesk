"""The automation run log (feature-gap plan 5.5): every rule and workflow run on a ticket,
newest first, for the people who manage rules (the `settings` right).

A run shows what started it, its steps (the lines its actions wrote, a workflow's path),
its state (done, waiting, cancelled, blocked, failed) and result. It never holds a
message's text, an email body, or a webhook's URL or secret. Finished runs are kept 30
days (the worker's sweep). A waiting workflow run can be stopped by hand (audited, area
`flows`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.automation import RunState
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import automation as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

CURSOR = "automation_run"
NOT_FOUND = "Run not found."


@dataclass(frozen=True)
class RunPage:
    runs: Sequence[Any]
    next_cursor: str | None


async def list_runs(
    sm: Sessionmaker,
    caller: Caller,
    *,
    rule_id: UUID | None = None,
    flow_id: UUID | None = None,
    ticket_id: UUID | None = None,
    state: str | None = None,
    trigger: str | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> RunPage:
    require(caller, Action.settings)
    if state is not None and state not in {s.value for s in RunState}:
        raise Invalid(f"state is one of: {', '.join(s.value for s in RunState)}.")
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, CURSOR)
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.list_runs(
            s, rule_id=rule_id, flow_id=flow_id, ticket_id=ticket_id, state=state,
            trigger=trigger, before=before, limit=limit + 1,
        )  # fmt: skip
    page, next_cursor = paging.time_page(rows, limit, CURSOR)
    return RunPage(page, next_cursor)


async def get_run(sm: Sessionmaker, caller: Caller, run_id: UUID) -> Any:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_run(s, run_id)
    if row is None:
        raise NotFound(NOT_FOUND)
    return row


async def cancel_run(sm: Sessionmaker, caller: Caller, run_id: UUID) -> Any:
    """Stops a waiting workflow run: it does nothing more."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_run(s, run_id, lock=True)
        if row is None:
            raise NotFound(NOT_FOUND)
        if row["state"] != RunState.waiting.value:
            raise Conflict("Only a waiting run can be stopped.")
        await repo.cancel_waiting(s, run_id=run_id, reason="Stopped by hand.")
        await audit.record(
            s, caller, Area.flows, "run_cancelled",
            f"Workflow {row['name']} on {label(int(row['ticket_number']))}", target_id=run_id,
            detail={"flow_id": str(row["flow_id"]) if row["flow_id"] else None,
                    "ticket_id": str(row["ticket_id"])},
        )  # fmt: skip
        found = await repo.get_run(s, run_id)
    assert found is not None
    return found
