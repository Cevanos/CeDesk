"""Ticket tools (md X1-X4, X6, X8, X9): merge, links and the Related card, child
tickets, approvals, tasks and the time log."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from cedesk.helpdesk.api.deps import (
    CallerDep,
    IdempotencyKey,
    SessionmakerDep,
    idempotency,
    mark_replayed,
)
from cedesk.helpdesk.api.ticket_schemas import TicketOut
from cedesk.helpdesk.api.tickets import detail_response
from cedesk.helpdesk.domain.ticket_tools import MINUTES_MAX, TEXT_MAX, Decision, DueIn
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.service import ticket_tools
from cedesk.helpdesk.service.ticket_tools import Related

router = APIRouter(tags=["ticket tools"])

_Text = Annotated[str, Field(max_length=TEXT_MAX * 2)]  # trimmed and checked by the service


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Merge and links ---


class MergeIn(_In):
    into: UUID = Field(description="The open ticket that keeps the conversation.")


class LinkIn(_In):
    ticket_id: UUID = Field(description="The ticket to link this one to.")


class RelatedTicket(BaseModel):
    id: UUID
    label: str
    subject: str
    status: str
    assignee_name: str | None
    team_name: str | None


class RelatedOut(BaseModel):
    parent: RelatedTicket | None = Field(description="Set on a child ticket.")
    merged_into: RelatedTicket | None = Field(description="Set when this ticket was merged.")
    children: list[RelatedTicket]
    links: list[RelatedTicket]
    merged_from: list[RelatedTicket] = Field(description="Tickets merged into this one.")


def _related_ticket(row: Any) -> RelatedTicket:
    return RelatedTicket(
        label=label(row["number"]),
        **{k: row[k] for k in ("id", "subject", "status", "assignee_name", "team_name")},
    )


def _related(r: Related) -> RelatedOut:
    return RelatedOut(
        parent=_related_ticket(r.parent) if r.parent else None,
        merged_into=_related_ticket(r.merged_into) if r.merged_into else None,
        children=[_related_ticket(t) for t in r.children],
        links=[_related_ticket(t) for t in r.links],
        merged_from=[_related_ticket(t) for t in r.merged_from],
    )


@router.post(
    "/tickets/{ticket_id}/merge",
    response_model=TicketOut,
    summary="Merge this ticket into another open ticket; returns the one merged into",
)
async def merge(
    ticket_id: UUID, body: MergeIn, caller: CallerDep, sm: SessionmakerDep, response: Response
) -> TicketOut:
    view = await ticket_tools.merge(sm, caller, ticket_id, body.into)
    return detail_response(view, response, caller)


@router.get(
    "/tickets/{ticket_id}/related",
    response_model=RelatedOut,
    summary="The Related card: parent, children, links, and merges (up to 50 each)",
)
async def related(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> RelatedOut:
    return _related(await ticket_tools.related(sm, caller, ticket_id))


@router.post(
    "/tickets/{ticket_id}/links",
    response_model=RelatedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Link two similar tickets (both ways); returns the Related card",
)
async def link(ticket_id: UUID, body: LinkIn, caller: CallerDep, sm: SessionmakerDep) -> RelatedOut:
    return _related(await ticket_tools.link(sm, caller, ticket_id, body.ticket_id))


@router.delete(
    "/tickets/{ticket_id}/links/{other_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a link",
)
async def unlink(ticket_id: UUID, other_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await ticket_tools.unlink(sm, caller, ticket_id, other_id)


# --- Child tickets ---


class ChildIn(_In):
    team_id: UUID = Field(description="The team that should handle the child ticket.")
    subject: Annotated[str, Field(max_length=200)] | None = Field(
        default=None, description="Default: the parent's subject."
    )
    note: _Text | None = Field(
        default=None,
        description="A private note for the other team. Default: 'Opened from T-1001: …'.",
    )
    assignee_id: UUID | None = Field(default=None, description="Default: routing picks one.")


@router.post(
    "/tickets/{ticket_id}/children",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Make a child ticket for another team; returns the new ticket",
)
async def create_child(
    ticket_id: UUID,
    body: ChildIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> TicketOut:
    idem = idempotency(
        idempotency_key, f"POST /tickets/{ticket_id}/children", body.model_dump_json().encode()
    )
    view = await ticket_tools.create_child(
        sm,
        caller,
        ticket_id,
        team_id=body.team_id,
        subject=body.subject,
        note=body.note,
        assignee_id=body.assignee_id,
        idem=idem,
    )
    mark_replayed(idem, response)
    return detail_response(view, response, caller)


# --- Approvals ---


class ApprovalIn(_In):
    approver_id: UUID = Field(description="A Supervisor or Admin; not yourself.")
    what: _Text | None = Field(
        default=None,
        description="What to approve. Default: from the amount and the ticket's order_id.",
    )
    amount: Annotated[Decimal, Field(max_digits=14, decimal_places=2)] | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3)] | None = Field(
        default=None, description="With an amount; default INR."
    )


class DecisionIn(_In):
    decision: Decision
    note: _Text | None = None


class ApprovalOut(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_label: str
    what: str
    amount: Decimal | None
    currency: str | None
    state: str = Field(description="pending, approved or rejected.")
    decision_note: str | None
    requester_id: UUID | None = Field(description="Null for a Cevanos Governance hold.")
    requester_name: str | None = Field(description="Null for a Cevanos Governance hold.")
    approver_id: UUID | None = Field(
        description="Null for a Cevanos Governance hold: anyone who can approve decides it."
    )
    approver_name: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    created_at: datetime
    cevanos_hold_id: str | None = Field(
        description="Set when Cevanos's Governance holds an action (a refund...) for this "
        "approval (md X7): the decision is sent to Cevanos, which then acts or not."
    )


def _approval(row: Any) -> ApprovalOut:
    return ApprovalOut.model_validate({**dict(row), "ticket_label": label(row["ticket_number"])})


@router.post(
    "/tickets/{ticket_id}/approvals",
    response_model=ApprovalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask someone to approve something on this ticket",
)
async def request_approval(
    ticket_id: UUID, body: ApprovalIn, caller: CallerDep, sm: SessionmakerDep
) -> ApprovalOut:
    row = await ticket_tools.request_approval(
        sm,
        caller,
        ticket_id,
        approver_id=body.approver_id,
        what=body.what,
        amount=body.amount,
        currency=body.currency,
    )
    return _approval(row)


@router.get(
    "/tickets/{ticket_id}/approvals",
    response_model=list[ApprovalOut],
    summary="This ticket's approval requests, newest first",
)
async def ticket_approvals(
    ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep
) -> list[ApprovalOut]:
    return [_approval(r) for r in await ticket_tools.ticket_approvals(sm, caller, ticket_id)]


@router.get(
    "/approvals/waiting",
    response_model=list[ApprovalOut],
    summary="Requests waiting for my decision, oldest first (up to 100)",
)
async def waiting_approvals(caller: CallerDep, sm: SessionmakerDep) -> list[ApprovalOut]:
    return [_approval(r) for r in await ticket_tools.waiting_approvals(sm, caller)]


@router.post(
    "/approvals/{approval_id}/decide",
    response_model=ApprovalOut,
    summary="Approve or reject (the named approver, or an Admin)",
)
async def decide_approval(
    approval_id: UUID, body: DecisionIn, caller: CallerDep, sm: SessionmakerDep
) -> ApprovalOut:
    row = await ticket_tools.decide_approval(
        sm, caller, approval_id, decision=body.decision, note=body.note
    )
    return _approval(row)


# --- Tasks ---


class TaskIn(_In):
    text: _Text
    owner_id: UUID | None = Field(default=None, description="Default: you.")
    due_in: DueIn | None = Field(
        default=None, description="1h, 4h, or tomorrow (9:00 in the workspace's time zone)."
    )
    due_at: datetime | None = Field(default=None, description="Any time, with a time zone.")


class TaskPatch(_In):
    text: _Text | None = None
    owner_id: UUID | None = None
    due_in: DueIn | None = None
    due_at: datetime | None = Field(default=None, description="null clears the due time.")
    done: bool | None = None


class TaskOut(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_label: str
    text: str
    owner_id: UUID
    owner_name: str
    due_at: datetime | None
    overdue: bool
    done_at: datetime | None
    done_by_name: str | None
    created_at: datetime


def _task(row: Any, now: datetime) -> TaskOut:
    overdue = row["done_at"] is None and row["due_at"] is not None and row["due_at"] < now
    return TaskOut.model_validate(
        {**dict(row), "ticket_label": label(row["ticket_number"]), "overdue": overdue}
    )


@router.post(
    "/tickets/{ticket_id}/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a task to this ticket",
)
async def add_task(
    ticket_id: UUID, body: TaskIn, caller: CallerDep, sm: SessionmakerDep
) -> TaskOut:
    row = await ticket_tools.add_task(
        sm,
        caller,
        ticket_id,
        text=body.text,
        owner_id=body.owner_id,
        due_in=body.due_in,
        due_at=body.due_at,
    )
    return _task(row, datetime.now(UTC))


@router.get(
    "/tickets/{ticket_id}/tasks",
    response_model=list[TaskOut],
    summary="This ticket's tasks, open ones first",
)
async def ticket_tasks(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> list[TaskOut]:
    now = datetime.now(UTC)
    return [_task(r, now) for r in await ticket_tools.ticket_tasks(sm, caller, ticket_id)]


@router.get(
    "/tasks/mine",
    response_model=list[TaskOut],
    summary="My open tasks, the ones due soonest first",
)
async def my_tasks(
    caller: CallerDep, sm: SessionmakerDep, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> list[TaskOut]:
    now = datetime.now(UTC)
    return [_task(r, now) for r in await ticket_tools.my_tasks(sm, caller, limit)]


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskOut,
    summary="Change a task, give it to someone else, or mark it done (done: true/false)",
)
async def change_task(
    task_id: UUID, body: TaskPatch, caller: CallerDep, sm: SessionmakerDep
) -> TaskOut:
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    row = await ticket_tools.change_task(sm, caller, task_id, changes)
    return _task(row, datetime.now(UTC))


# --- Time log ---


class TimeIn(_In):
    minutes: Annotated[int, Field(ge=1, le=MINUTES_MAX)]
    note: _Text | None = None
    billable: bool = False


class TimeOut(BaseModel):
    id: UUID
    member_id: UUID | None
    member_name: str | None
    minutes: int
    note: str | None
    billable: bool
    logged_at: datetime


class TimeLogOut(BaseModel):
    total_minutes: int
    billable_minutes: int
    entries: list[TimeOut] = Field(description="Newest first.")


@router.post(
    "/tickets/{ticket_id}/time",
    response_model=TimeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Log time spent on this ticket",
)
async def log_time(
    ticket_id: UUID, body: TimeIn, caller: CallerDep, sm: SessionmakerDep
) -> TimeOut:
    row = await ticket_tools.log_time(
        sm, caller, ticket_id, minutes=body.minutes, note=body.note, billable=body.billable
    )
    return TimeOut.model_validate(dict(row))


@router.get(
    "/tickets/{ticket_id}/time",
    response_model=TimeLogOut,
    summary="This ticket's time log with the total and the billable total",
)
async def time_log(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> TimeLogOut:
    log = await ticket_tools.time_log(sm, caller, ticket_id)
    return TimeLogOut(
        total_minutes=log.total_minutes,
        billable_minutes=log.billable_minutes,
        entries=[TimeOut.model_validate(dict(r)) for r in log.entries],
    )


@router.delete(
    "/time/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a time entry (your own; an Admin can remove any)",
)
async def delete_time(entry_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await ticket_tools.delete_time(sm, caller, entry_id)
