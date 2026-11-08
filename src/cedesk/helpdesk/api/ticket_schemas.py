"""Request and response bodies for tickets, ticket types and teams."""

from datetime import datetime
from typing import Annotated, Any, Self
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
from cedesk.helpdesk.domain.tickets import Channel, FieldKind, Priority, Status

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


class TicketTypePatch(Body):
    name: Short | None = None
    fields: list[FieldDefIn] | None = Field(
        default=None,
        max_length=30,
        description="The whole new list: add, remove, rename or reorder fields.",
    )
    required_skill: Short | None = Field(default=None, description="null clears it.")


class TicketTypeOut(BaseModel):
    id: UUID
    name: str
    fields: list[FieldDefOut]
    position: int
    required_skill: str | None


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


class MessageOut(BaseModel):
    id: UUID
    kind: str
    body: str
    author_member_id: UUID | None
    author_name: str | None
    created_at: datetime
    mention_ids: list[UUID] = []


class TicketOut(TicketRowOut):
    fields: dict[str, Any]
    first_reply_at: datetime | None
    reopen_count: int
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
