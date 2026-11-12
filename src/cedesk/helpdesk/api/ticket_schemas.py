"""Request and response bodies for tickets, ticket types and teams."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from cedesk.helpdesk.api.schemas import Body, Email, Name
from cedesk.helpdesk.domain.routing import TEAM_MAX_OPEN
from cedesk.helpdesk.domain.tickets import Channel, FieldKind, Priority, Sentiment, Status

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50000)]
Tag = Annotated[str, StringConstraints(max_length=60)]
FieldValue = StrictStr | StrictInt | StrictFloat | None


# Ticket types


class FieldDefIn(Body):
    key: str = Field(description="Stored on tickets: a-z, 0-9 and _, starting with a letter.")
    label: str
    kind: FieldKind
    options: list[str] = Field(default=[], description="Select fields only.")
    required: bool = False


class FieldDefOut(BaseModel):
    key: str
    label: str
    kind: FieldKind
    options: list[str]
    required: bool


class TicketTypeIn(Body):
    name: Short
    fields: list[FieldDefIn] = Field(default=[], max_length=30)
    required_skill: Short | None = None
    for_customers: bool = Field(
        default=True, description="Customers can pick it in the portal and the web form."
    )


class TicketTypePatch(Body):
    name: Short | None = None
    fields: list[FieldDefIn] | None = Field(
        default=None,
        max_length=30,
        description="The whole new list: add, remove, rename or reorder fields.",
    )
    required_skill: Short | None = Field(default=None, description="null clears it.")
    for_customers: bool | None = Field(
        default=None, description="false hides it from the portal and the web form."
    )


class TicketTypeOut(BaseModel):
    id: UUID
    name: str
    fields: list[FieldDefOut]
    position: int
    required_skill: str | None
    for_customers: bool


class ReorderBody(Body):
    ids: list[UUID] = Field(max_length=500, description="Every ticket type id, in the new order.")


# Teams


class TeamIn(Body):
    name: Short
    auto_assign: bool = True
    max_open_per_agent: int = Field(default=5, ge=1, le=TEAM_MAX_OPEN)
    member_ids: list[UUID] = Field(default=[], max_length=500)


class TeamPatch(Body):
    name: Short | None = None
    auto_assign: bool | None = None
    max_open_per_agent: int | None = Field(default=None, ge=1, le=TEAM_MAX_OPEN)
    member_ids: list[UUID] | None = Field(default=None, max_length=500)


class TeamOut(BaseModel):
    id: UUID
    name: str
    auto_assign: bool
    max_open_per_agent: int
    member_ids: list[UUID]


# Tickets


class ContactIn(Body):
    email: Email
    name: Name


class TicketIn(Body):
    subject: Subject
    contact: ContactIn
    description: Text | None = Field(default=None, description="The customer's first message.")
    priority: Priority = Priority.medium
    channel: Channel = Channel.web
    type_id: UUID | None = None
    team_id: UUID | None = None
    assignee_id: UUID | None = None
    fields: dict[str, FieldValue] = Field(default={}, max_length=30)
    tags: list[Tag] = Field(default=[], max_length=20)


class TicketPatch(Body):
    """Send only what changes. `null` clears type, team, assignee or one field."""

    subject: Subject | None = None
    status: Status | None = None
    priority: Priority | None = None
    type_id: UUID | None = None
    team_id: UUID | None = None
    assignee_id: UUID | None = None
    fields: dict[str, FieldValue] | None = Field(default=None, max_length=30)
    add_tags: list[Tag] = Field(default=[], max_length=20)
    remove_tags: list[Tag] = Field(default=[], max_length=20)

    @model_validator(mode="after")
    def no_null_for_required(self) -> Self:
        for name in ("subject", "status", "priority", "fields"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, mode="python")


class ContactOut(BaseModel):
    id: UUID
    name: str
    email: str
    vip: bool


class ContactCard(ContactOut):
    customer_since: datetime
    other_tickets: int
    csat_average: float | None = Field(
        default=None, description="Average of this customer's ratings, 1-5; null: none yet."
    )
    csat_count: int = 0


class CsatOut(BaseModel):
    score: int = Field(description="1-5")
    comment: str | None
    source: str = Field(description="portal or email")
    rated_at: datetime


class SlaClockOut(BaseModel):
    target_minutes: int
    due_at: datetime
    stopped_at: datetime | None = Field(description="The reply or resolution; null while running.")
    state: Literal["on_track", "close", "breached", "met"] = Field(
        description="close: under 25% of the target left. breached stays after the reply. "
        "met: done in time."
    )
    minutes_left: float = Field(description="Business minutes; negative once overdue.")


class SlaOut(BaseModel):
    """The ticket's SLA, worked out when it is read (W13)."""

    policy: str | None = Field(description="The SLA policy that matched; null: priority targets.")
    current: Literal["first_reply", "resolve"] = Field(
        description="The clock the pill shows: first reply until it is replied, then resolve."
    )
    state: Literal["on_track", "close", "breached", "met"] = Field(
        description="The current clock's state, for the pill."
    )
    paused: bool = Field(
        description="Outside business hours (or a holiday) and still running. Pending does "
        "not pause."
    )
    first_reply: SlaClockOut
    resolve: SlaClockOut


class TicketRowOut(BaseModel):
    id: UUID
    number: int
    label: str = Field(description="T-1001")
    subject: str
    status: Status
    priority: Priority
    channel: Channel
    type_id: UUID | None
    type_name: str | None
    team_id: UUID | None
    team_name: str | None
    assignee_id: UUID | None
    assignee_name: str | None
    contact: ContactOut
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    # The list chips (T10). Filled by the handover, triage, imports and ticket tools.
    accepted: bool = Field(description="false: a handover waiting for an agent to accept it.")
    topic: str | None
    sentiment: Sentiment | None
    language: str | None
    source: str | None = Field(description="cevanos, intercom, email, ... or null.")
    parent_id: UUID | None
    merged_into_id: UUID | None
    child_count: int
    sla: SlaOut | None = None


class MessageFileOut(BaseModel):
    id: UUID = Field(description="GET /attachments/{id} gives a download link.")
    name: str
    size: int = Field(description="Bytes.")
    mime: str
    scan_status: str = Field(
        description="not_scanned, clean, infected, or scan_failed (could not be checked)."
    )


class MessageOut(BaseModel):
    id: UUID
    kind: str
    body: str
    author_member_id: UUID | None
    author_name: str | None
    created_at: datetime
    mention_ids: list[UUID] = []
    email: dict[str, Any] | None = Field(
        default=None, description="Email messages: from, to, cc, message_id."
    )
    channel_message_id: str | None = None
    attachments: list[MessageFileOut] = Field(
        default=[], description="Files sent with it (by the agent, or in the customer's email)."
    )
    delivery: Literal["pending", "sent", "failed"] | None = Field(
        default=None,
        description="Replies on email tickets: whether the email went out. "
        "pending: queued or retrying; sent; failed: gave up (see the ticket's system line).",
    )


class TicketOut(TicketRowOut):
    can_edit: bool = Field(
        default=True,
        description="False: show the ticket read-only (notes still allowed), with "
        "`view_only_reason` in the banner (md A5).",
    )
    view_only_reason: str | None = Field(
        default=None, description='e.g. "Your role Billing desk can edit Billing tickets only."'
    )
    fields: dict[str, Any]
    csat: CsatOut | None = Field(
        default=None, description="The customer's rating of this ticket, if any."
    )
    first_reply_at: datetime | None
    reopen_count: int
    conversation_id: str | None = Field(description="The Cevanos chat, for handovers.")
    handover: dict[str, Any] | None = Field(description="The AI summary and reason.")
    source_id: str | None
    contact: ContactCard
    messages: list[MessageOut] = Field(description="The latest 50 messages, oldest to newest.")
    older_messages_cursor: str | None = Field(
        description="Send as `cursor` to GET /tickets/{id}/messages for older messages; "
        "null when every message is shown."
    )


class Counts(BaseModel):
    open: int
    pending: int
    resolved: int


class TicketListOut(BaseModel):
    tickets: list[TicketRowOut]
    counts: Counts = Field(description="Per status, ignoring the other filters (for the tabs).")
    next_cursor: str | None = Field(
        description="Send as `cursor`, with the same filters and sort, for the next page. "
        "null on the last page."
    )


class ContactPatch(Body):
    """Send only what changes."""

    name: Name | None = None
    vip: bool | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class ContactProfileOut(ContactOut):
    customer_since: datetime
    ticket_count: int


class ContactTicketsOut(BaseModel):
    contact: ContactProfileOut
    tickets: list[TicketRowOut] = Field(description="Newest first.")
    next_cursor: str | None
