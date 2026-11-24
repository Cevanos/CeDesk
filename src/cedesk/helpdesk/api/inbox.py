"""Agent inbox tools (feature-gap plan phase 1) on tickets: snooze, bulk changes,
followers and the email transcript. Saved views are in api/views.py and the auto-close
setting in api/auto_close.py."""

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import (
    CallerDep,
    SessionmakerDep,
    SettingsDep,
    fallback_sender,
    within,
)
from cedesk.helpdesk.api.schemas import Body, Email
from cedesk.helpdesk.api.ticket_schemas import TicketOut
from cedesk.helpdesk.api.tickets import detail_response
from cedesk.helpdesk.domain import bulk as bulk_rules
from cedesk.helpdesk.domain import transcript as transcript_rules
from cedesk.helpdesk.domain.bulk import BulkAction
from cedesk.helpdesk.domain.tickets import Priority, Status
from cedesk.helpdesk.service import bulk, followers, tickets, transcript

router = APIRouter(tags=["tickets"])

IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=100,
        description="The ETag from reading the ticket. 409 if the ticket changed since.",
    ),
]
Tag = Annotated[str, StringConstraints(max_length=60)]

# Snooze (1.1)


class SnoozeIn(Body):
    until: datetime = Field(
        description="When the ticket reopens by itself, with a time zone "
        "(2026-11-21T09:00:00Z). At least a minute ahead, at most a year."
    )


@router.post(
    "/tickets/{ticket_id}/snooze",
    response_model=TicketOut,
    summary="Snooze a ticket until a time (it waits in Pending, then reopens)",
    description="Sets the status to Pending and `snoozed_until`. At that time the ticket "
    "goes back to Open and the assignee and followers are told; a customer reply wakes it "
    "sooner. Snoozing again moves the time. Same rights as changing the ticket. 409 on a "
    "resolved ticket.",
    responses={
        409: {"description": "Resolved, or changed since read (If-Match)"},
        422: {"description": "Time in the past, under a minute ahead, or over a year"},
    },
)
async def snooze(
    ticket_id: UUID,
    body: SnoozeIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    if_match: IfMatch = None,
) -> TicketOut:
    view = await tickets.change_ticket(
        sm, caller, ticket_id, {"snooze_until": body.until}, if_match=if_match
    )
    return detail_response(view, response, caller)


@router.delete(
    "/tickets/{ticket_id}/snooze",
    response_model=TicketOut,
    summary="End a snooze now (the ticket reopens)",
    description="A snoozed ticket goes back to Open at once. A ticket that isn't snoozed "
    "is left as it is.",
)
async def unsnooze(
    ticket_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    if_match: IfMatch = None,
) -> TicketOut:
    view = await tickets.change_ticket(
        sm, caller, ticket_id, {"snooze_until": None}, if_match=if_match
    )
    return detail_response(view, response, caller)


# Bulk changes (1.3)


class BulkIn(Body):
    ticket_ids: list[UUID] = Field(
        min_length=1,
        max_length=bulk_rules.MAX_TICKETS,
        description=f"1-{bulk_rules.MAX_TICKETS} tickets; repeats count once.",
    )
    action: BulkAction = Field(
        description="assign (assignee_id), set_team (team_id), set_status (status), "
        "set_priority (priority), add_tags / remove_tags (tags), snooze (until), unsnooze."
    )
    assignee_id: UUID | None = Field(default=None, description="assign: null unassigns.")
    team_id: UUID | None = Field(default=None, description="set_team: null clears the team.")
    status: Status | None = None
    priority: Priority | None = None
    tags: list[Tag] | None = Field(default=None, max_length=20)
    until: datetime | None = Field(default=None, description="snooze: as POST .../snooze.")

    def given(self) -> dict[str, Any]:
        """The value fields that were sent (null counts: it clears assignee or team)."""
        return {k: getattr(self, k) for k in self.model_fields_set if k in bulk_rules.VALUE_FIELDS}

    @model_validator(mode="after")
    def one_value(self) -> Self:
        bulk_rules.changes(self.action, self.given())  # raises with the reason
        return self


class BulkError(BaseModel):
    code: str = Field(description="As the error envelope: NotFound, Forbidden, Conflict, ...")
    message: str


class BulkResultOut(BaseModel):
    ticket_id: UUID
    ok: bool
    error: BulkError | None = None


class BulkOut(BaseModel):
    results: list[BulkResultOut] = Field(description="One per ticket, in the order sent.")
    succeeded: int
    failed: int


@router.post(
    "/tickets/bulk",
    response_model=BulkOut,
    summary=f"One change on up to {bulk_rules.MAX_TICKETS} tickets, each checked on its own",
    description="Each ticket is changed exactly as `PATCH /tickets/{id}` would change it "
    "(same rights, team scope, rules, SLA, events and history lines), in its own "
    "transaction: a ticket that fails is listed with its error and never stops or undoes "
    "the others. The answer is 200 even when some (or all) failed: read `results`. "
    f"At most {bulk_rules.PER_MINUTE} bulk requests a minute per person (429).",
    responses={
        403: {"description": "The role can't make bulk changes"},
        422: {"description": "No tickets, too many, or the action's value is missing"},
        429: {"description": "Too many bulk requests; see Retry-After"},
    },
)
async def bulk_change(
    body: BulkIn, caller: CallerDep, sm: SessionmakerDep, request: Request
) -> BulkOut:
    results = await bulk.change_many(
        sm,
        caller,
        body.ticket_ids,
        body.action,
        body.given(),
        spend=lambda: within(
            request.app.state.bulk_requests, str(caller.member_id), "bulk changes"
        ),
    )
    out = [
        BulkResultOut(
            ticket_id=r.ticket_id,
            ok=r.ok,
            error=None if r.ok else BulkError(code=str(r.code), message=str(r.message)),
        )
        for r in results
    ]
    succeeded = sum(r.ok for r in results)
    return BulkOut(results=out, succeeded=succeeded, failed=len(results) - succeeded)


# Followers (1.5)


class FollowerOut(BaseModel):
    member_id: UUID
    name: str
    email: str
    is_active: bool
    added_by: UUID | None = Field(description="null: they followed it themselves or by "
                                  "being assigned or @mentioned.")  # fmt: skip
    added_by_name: str | None
    created_at: datetime


class FollowersOut(BaseModel):
    followers: list[FollowerOut]
    following: bool = Field(description="Whether the signed-in member follows it.")


class FollowIn(Body):
    member_id: UUID = Field(description="Yourself, or (with the right to change the "
                            "ticket) a colleague.")  # fmt: skip


def _followers(rows: Any, me: UUID) -> FollowersOut:
    found = [FollowerOut.model_validate(dict(r)) for r in rows]
    return FollowersOut(followers=found, following=any(f.member_id == me for f in found))


@router.get(
    "/tickets/{ticket_id}/followers",
    response_model=FollowersOut,
    summary="Who follows the ticket",
)
async def list_followers(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> FollowersOut:
    rows = await followers.list_followers(sm, caller, ticket_id)
    return _followers(rows, caller.member_id)


@router.post(
    "/tickets/{ticket_id}/followers",
    response_model=FollowersOut,
    summary="Follow the ticket, or add a colleague as a follower (audited)",
    description="Followers are told about public replies and status changes. Adding "
    "someone already following changes nothing. Adding someone else needs the right to "
    "change the ticket; they must be active and able to see it.",
    responses={422: {"description": "Not a member, deactivated, or can't see the ticket"}},
)
async def add_follower(
    ticket_id: UUID, body: FollowIn, caller: CallerDep, sm: SessionmakerDep
) -> FollowersOut:
    rows = await followers.add_follower(sm, caller, ticket_id, body.member_id)
    return _followers(rows, caller.member_id)


@router.delete(
    "/tickets/{ticket_id}/followers/{member_id}",
    response_model=FollowersOut,
    summary="Unfollow, or remove a colleague as a follower (audited)",
    responses={404: {"description": "No such ticket, or that person isn't following it"}},
)
async def remove_follower(
    ticket_id: UUID, member_id: UUID, caller: CallerDep, sm: SessionmakerDep
) -> FollowersOut:
    rows = await followers.remove_follower(sm, caller, ticket_id, member_id)
    return _followers(rows, caller.member_id)


# Email transcript (1.6)


class TranscriptIn(Body):
    to: Email | None = Field(
        default=None, description="Another address; leave out (or null) for the customer."
    )


class TranscriptOut(BaseModel):
    to: str
    to_customer: bool
    messages: int = Field(description="How many messages the transcript holds.")


@router.post(
    "/tickets/{ticket_id}/transcript",
    response_model=TranscriptOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email the conversation (public messages only) to the customer or an address",
    description="Customer messages, replies and the AI agent's lines, oldest first, with "
    "files listed by name. Never private notes, system lines or side conversations. Sent "
    "from the default support address; queued, like replies. A line on the ticket and an "
    "audit entry record it (never the text). Needs the right to send transcripts and to "
    f"change the ticket. At most {transcript_rules.PER_HOUR} an hour per person and "
    f"{transcript_rules.PER_HOUR_WORKSPACE} per workspace (429).",
    responses={
        409: {"description": "No support address, or nothing the customer saw yet"},
        422: {"description": "A chat or demo customer with no address, or a support address"},
        429: {"description": "Too many transcripts; see Retry-After"},
    },
)
async def send_transcript(
    ticket_id: UUID,
    body: TranscriptIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    request: Request,
) -> TranscriptOut:

    def spend() -> None:
        within(request.app.state.transcript_requests, str(caller.member_id), "transcripts")
        within(request.app.state.transcript_workspace, str(caller.workspace_id), "transcripts")

    sent = await transcript.send(
        sm, caller, ticket_id, to=body.to, fallback=fallback_sender(settings), spend=spend
    )
    return TranscriptOut(to=sent.to, to_customer=sent.to_customer, messages=sent.messages)
