"""The archive tier (md O3): resolved tickets older than the archive age, found by number
or customer and opened read only; and the archive age setting."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import archive as rules
from cedesk.helpdesk.service import archive

router = APIRouter(prefix="/archive", tags=["archive"])


class ArchivedOut(BaseModel):
    id: UUID
    number: int
    label: str
    subject: str
    status: str
    priority: str
    channel: str
    contact_id: UUID | None
    contact_email: str | None
    contact_name: str | None
    team_name: str | None
    assignee_name: str | None
    created_at: datetime
    resolved_at: datetime | None
    archived_at: datetime


class ArchivedMessageOut(BaseModel):
    kind: str = Field(description="customer, agent, ai, note or system.")
    author_name: str | None
    body: str
    created_at: str
    files: list[dict[str, Any]] = Field(description="name, size and type of each file.")
    email: dict[str, Any] | None = Field(
        description="An email's headers (from, to, cc, subject, message ids); else null."
    )


class ArchivedTimeOut(BaseModel):
    minutes: int
    billable: bool
    note: str | None
    member_name: str | None = Field(description="As it was when archived.")
    logged_at: datetime


class ArchivedTaskOut(BaseModel):
    text: str
    owner_name: str | None
    due_at: datetime | None
    done_at: datetime | None
    created_at: datetime


class ArchivedApprovalOut(BaseModel):
    what: str
    amount: Decimal | None
    currency: str | None
    state: str = Field(description="pending, approved or rejected.")
    decision_note: str | None
    requester_name: str | None
    approver_name: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    created_at: datetime


class ArchivedLinkOut(BaseModel):
    ticket_id: UUID = Field(
        description="The other ticket: open it live, or in the archive if it moved too."
    )
    number: int
    label: str
    kind: str
    created_at: datetime


class ArchivedSideMessageOut(BaseModel):
    direction: str = Field(description="out: sent from CeDesk; in: their reply.")
    author_name: str | None
    from_address: str = Field(description="Email: the sender's address. Slack: empty.")
    body: str
    created_at: datetime


class ArchivedSideOut(BaseModel):
    subject: str
    via: str = Field(description="email or slack.")
    to: list[str] = Field(description="Email: the addresses. Slack: the channel.")
    from_address: str = Field(description="Email: the support address it went out from.")
    created_at: datetime
    last_message_at: datetime | None
    messages: list[ArchivedSideMessageOut]


class ArchivedJiraIssueOut(BaseModel):
    key: str
    url: str
    made_here: bool = Field(description="Created from CeDesk; false: an existing one linked.")
    created_at: datetime


class ArchivedEmailOut(BaseModel):
    from_address: str
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    body_text: str | None = Field(description="Null for one that carried a secret.")
    sensitive: bool = Field(
        description="It carried a secret (a reset link), so its body isn't kept."
    )
    status: str = Field(description="sent or failed.")
    sent_at: datetime | None
    created_at: datetime


class ArchivedCevanosChatOut(BaseModel):
    conversation_id: str = Field(description="The Cevanos chat it was handed over from.")
    agent_id: str | None
    channel_kind: str | None
    handover: dict[str, Any] | None = Field(description="The handover card: reason and more.")
    taken_over_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class ArchivedRelatedOut(BaseModel):
    """The rest of the ticket's history, kept when it was archived. Lists are oldest
    first; empty for tickets archived before this was kept."""

    time_log: list[ArchivedTimeOut]
    tasks: list[ArchivedTaskOut]
    approvals: list[ArchivedApprovalOut]
    links: list[ArchivedLinkOut] = Field(description="The tickets it was linked to.")
    side_conversations: list[ArchivedSideOut] = Field(description="With their messages.")
    jira_issues: list[ArchivedJiraIssueOut]
    emails: list[ArchivedEmailOut] = Field(description="The emails sent from it.")
    cevanos_chat: ArchivedCevanosChatOut | None = Field(
        description="A handover's Cevanos chat (its messages are the ticket's); else null."
    )


class ArchivedTicketOut(ArchivedOut):
    type_name: str | None = Field(description="As it was when archived.")
    tags: list[str]
    fields: dict[str, Any]
    messages: list[ArchivedMessageOut]
    files: list[dict[str, Any]] = Field(description="Every file on the ticket: name, size, type.")
    rating: dict[str, Any] | None = Field(description="score, comment, source, rated_at.")
    related: ArchivedRelatedOut


class SettingOut(BaseModel):
    after_days: int | None = Field(description=f"Null: the default ({rules.DEFAULT_DAYS}).")
    effective_days: int
    updated_at: datetime | None
    updated_by_name: str | None


class SettingIn(Body):
    after_days: int | None = Field(
        description=f"{rules.MIN_DAYS} to {rules.MAX_DAYS}; null for the default "
        f"({rules.DEFAULT_DAYS})."
    )


@router.get(
    "/tickets",
    response_model=list[ArchivedOut],
    summary="Find archived tickets by number or customer",
    description=f"Resolved tickets older than the archive age (default {rules.DEFAULT_DAYS} "
    "days) leave the ticket list and live here. Give `number` (1042 or T-1042), or the "
    f"customer's `email` or `contact_id`; newest first, at most {rules.SEARCH_LIMIT}. A "
    "team-only role sees its team's.",
    responses={422: {"description": "Neither a number nor a customer, or a bad number"}},
)
async def find(
    caller: CallerDep,
    sm: SessionmakerDep,
    number: Annotated[str | None, Query(max_length=20)] = None,
    email: Annotated[str | None, Query(max_length=254)] = None,
    contact_id: UUID | None = None,
) -> list[ArchivedOut]:
    found = await archive.search(sm, caller, number=number, contact_id=contact_id, email=email)
    return [ArchivedOut(**f.__dict__) for f in found]


@router.get(
    "/tickets/{ticket_id}",
    response_model=ArchivedTicketOut,
    summary="Open an archived ticket (read only)",
    description="Everything that was on it: messages (internal notes too, with an email's "
    "headers), files' names, rating, and in `related`: time logged, tasks, approvals, "
    "links, side conversations, Jira issues, emails sent and the Cevanos chat. The type's, "
    "team's and assignee's names are as they were when it was archived. A role that sees "
    "one team only gets a 404 for another team's. It can't be changed, replied to or reopened; a "
    "customer's late email answer to it becomes a new ticket that follows it.",
    responses={404: {"description": "Not in the archive, or another team's"}},
)
async def open_one(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ArchivedTicketOut:
    got = await archive.get(sm, caller, ticket_id)
    data = got.__dict__ | {
        "messages": [ArchivedMessageOut(**m.__dict__) for m in got.messages],
        "related": ArchivedRelatedOut.model_validate(got.related),
    }
    return ArchivedTicketOut(**data)


@router.get("/settings", response_model=SettingOut, summary="The archive age (Settings)")
async def get_setting(caller: CallerDep, sm: SessionmakerDep) -> SettingOut:
    return SettingOut(**(await archive.get_setting(sm, caller)).__dict__)


@router.put(
    "/settings",
    response_model=SettingOut,
    summary="Set the archive age (Settings; audited)",
    description="Resolved tickets older than this move to the archive at the next nightly "
    "run. Retention still deletes them when it says so.",
)
async def set_setting(body: SettingIn, caller: CallerDep, sm: SessionmakerDep) -> SettingOut:
    return SettingOut(**(await archive.set_setting(sm, caller, body.after_days)).__dict__)
