"""Request and response bodies for the workflow settings: saved replies, rules and macros."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.saved_replies import MAX_BODY

Shortcut = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=33),
    Field(description="What the agent types after `/`, e.g. `refund`. Stored lower-case."),
]
ReplyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_BODY),
    Field(
        description="The text. Placeholders such as {{customer.first_name}} are filled "
        "when the reply is sent."
    ),
]


class SavedReplyIn(Body):
    shortcut: Shortcut
    body: ReplyText


class SavedReplyPatch(Body):
    shortcut: Shortcut | None = None
    body: ReplyText | None = None


class SavedReplyOut(BaseModel):
    id: UUID
    shortcut: str = Field(description="Without the slash: `refund` is typed as `/refund`.")
    body: str
    source: str = Field(
        description="default (came with the workspace), manual, calendly, or an import "
        "source such as intercom."
    )
    created_at: datetime
    updated_at: datetime


# Rules

RuleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
RuleEvent = Annotated[
    Literal["created", "customer_replied", "sla_breached"],
    Field(
        description="created: a new ticket. customer_replied: the customer wrote on an "
        "Open or Pending ticket. sla_breached: an open ticket's SLA clock ran out (checked "
        "once a minute; once per clock). Agent edits never run rules."
    ),
]
Conditions = Annotated[
    list[dict[str, Any]],
    Field(
        min_length=1,
        max_length=10,
        description="All must match. Each is one of: "
        '{"kind": "text_contains_any", "words": ["refund"]} (subject and customer messages, '
        'any case); {"kind": "channel_in", "channels": ["email"]}; '
        '{"kind": "to_address_contains", "text": "billing@"}; '
        '{"kind": "status_in", "statuses": ["pending"]}; '
        '{"kind": "sentiment_in", "sentiments": ["negative"]}; '
        '{"kind": "sla_in", "states": ["breached"]} (the SLA pill\'s clock: on_track, '
        "close, breached, met; not on created).",
    ),
]
Actions = Annotated[
    list[dict[str, Any]],
    Field(
        min_length=1,
        max_length=10,
        description="Run in order. Each is one of: "
        '{"kind": "set_type", "type_id": "...", "only_if_empty": false}; '
        '{"kind": "set_team", "team_id": "..."}; '
        '{"kind": "set_priority", "priority": "high", "keep_urgent": true}; '
        '{"kind": "set_status", "status": "open"}; {"kind": "add_tag", "tag": "upset"}.',
    ),
]


class RuleIn(Body):
    name: RuleName
    enabled: bool = True
    event: RuleEvent
    conditions: Conditions
    actions: Actions


class RulePatch(Body):
    name: RuleName | None = None
    enabled: bool | None = None
    event: RuleEvent | None = None
    conditions: Conditions | None = None
    actions: Actions | None = None


class RuleOut(BaseModel):
    id: UUID
    name: str
    enabled: bool
    position: int = Field(description="Rules run in this order, lowest first.")
    event: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    problems: list[str] = Field(
        description="What to fix, e.g. the team it sets was deleted. Empty when fine."
    )
    created_at: datetime
    updated_at: datetime


class RuleOrderBody(Body):
    ids: list[UUID] = Field(max_length=100, description="Every rule id, in the new order.")


class CustomerReplyBody(Body):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50000)]


# Macros

MacroName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Steps = Annotated[
    list[dict[str, Any]],
    Field(
        min_length=1,
        max_length=20,
        description="Run in order. Each is one of: "
        '{"kind": "reply", "text": "Hi {{customer.first_name}}, ..."} (placeholders are '
        'filled when it runs); {"kind": "note", "text": "..."}; '
        '{"kind": "set_status", "status": "resolved"}; '
        '{"kind": "set_priority", "priority": "low"}; '
        '{"kind": "set_type", "type_id": "...", "only_if_empty": false} (fields the new '
        "type lacks are cleared); "
        '{"kind": "set_team", "team_id": "..."} (re-routes the ticket); '
        '{"kind": "add_tag", "tag": "refund"}. At most one reply and one note.',
    ),
]


class MacroIn(Body):
    name: MacroName
    steps: Steps


class MacroPatch(Body):
    name: MacroName | None = None
    steps: Steps | None = None


class MacroOut(BaseModel):
    id: UUID
    name: str
    position: int = Field(description="The order in the Macros menu, lowest first.")
    steps: list[dict[str, Any]]
    problems: list[str] = Field(
        description="What to fix, e.g. the team it sets was deleted. Empty when fine."
    )
    created_at: datetime
    updated_at: datetime


class MacroOrderBody(Body):
    ids: list[UUID] = Field(max_length=100, description="Every macro id, in the new order.")
