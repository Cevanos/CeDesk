"""Side conversations (md X5): email another team from a ticket, or write in a Slack
channel; the customer never sees it, and the reply comes back to the ticket."""

import time
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SealerDep, SessionmakerDep
from cedesk.helpdesk.api.jira import PostDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import side_conversations as rules
from cedesk.helpdesk.domain import slack as slack_rules
from cedesk.helpdesk.service import side_conversations as sides
from cedesk.helpdesk.service.errors import TooLarge
from cedesk.helpdesk.service.side_conversations import SideConversation

router = APIRouter(tags=["side conversations"])


class StartIn(Body):
    to: list[str] = Field(min_length=1, max_length=rules.MAX_TO, description="Email addresses.")
    subject: str | None = Field(
        default=None,
        max_length=rules.SUBJECT_MAX,
        description='Default: "About <ticket subject>". The ticket\'s label is added.',
    )
    body: str = Field(min_length=1, max_length=rules.BODY_MAX)


class SlackStartIn(Body):
    channel: str = Field(
        min_length=1, max_length=81, description="#warehouse, or a channel id (C0123ABCD)."
    )
    body: str = Field(min_length=1, max_length=rules.BODY_MAX)


class AgainIn(Body):
    body: str = Field(min_length=1, max_length=rules.BODY_MAX)


class SideMessageOut(BaseModel):
    id: UUID
    direction: str = Field(description="out: sent from CeDesk; in: their reply.")
    body: str
    author_name: str
    from_address: str = Field(description="Email: the sender's address. Slack: empty.")
    created_at: datetime


class SideOut(BaseModel):
    id: UUID
    via: str = Field(description="email or slack.")
    subject: str
    to: list[str] = Field(description="Email: the addresses. Slack: the channel.")
    from_address: str = Field(description="Email: the support address it goes out from.")
    created_at: datetime
    last_message_at: datetime
    created_by_name: str | None
    messages: list[SideMessageOut]


def _out(side: SideConversation) -> SideOut:
    data = side.__dict__ | {"messages": [SideMessageOut(**m.__dict__) for m in side.messages]}
    return SideOut(**data)


_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Can't reply, or the ticket is another team's (view only)"},
    404: {"description": "No such ticket or side conversation"},
    409: {"description": "No support address (or Slack app) yet, or 20 already"},
    422: {
        "description": "Not an address, the customer, one of our own addresses, empty; "
        "Slack: no such channel, or the bot isn't in it"
    },
    503: {"description": "Slack didn't answer; try again"},
}


@router.get(
    "/tickets/{ticket_id}/side-conversations",
    response_model=list[SideOut],
    summary="Side conversations on a ticket (agents only; never shown to the customer)",
)
async def list_sides(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> list[SideOut]:
    return [_out(s) for s in await sides.conversations(sm, caller, ticket_id)]


@router.post(
    "/tickets/{ticket_id}/side-conversations",
    response_model=SideOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a side conversation by email",
    description="Sent from the ticket's support address by the worker. Their reply comes "
    "back here (matched by the email's reply headers), the assignee is told "
    "(notification side_reply), and the customer sees none of it. The ticket gets a "
    "system line.",
    responses=_ERRORS,
)
async def start_side(
    ticket_id: UUID, body: StartIn, caller: CallerDep, sm: SessionmakerDep
) -> SideOut:
    made = await sides.start(
        sm, caller, ticket_id, to=body.to, subject=body.subject, body=body.body
    )
    return _out(made)


@router.post(
    "/tickets/{ticket_id}/side-conversations/slack",
    response_model=SideOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a side conversation in a Slack channel",
    description="Needs the Slack side conversations app. Its bot posts your message in the "
    "channel (invite it there first) with the ticket's number and subject, never the "
    "customer's messages. Replies in that thread come back here and the assignee is told "
    "(notification side_reply). The customer sees none of it. The ticket gets a system line.",
    responses=_ERRORS,
)
async def start_in_slack(
    ticket_id: UUID, body: SlackStartIn, caller: CallerDep, sm: SessionmakerDep,
    sealer: SealerDep, post: PostDep,
) -> SideOut:  # fmt: skip
    made = await sides.start_in_slack(
        sm, sealer, post, caller, ticket_id, channel=body.channel, body=body.body
    )
    return _out(made)


@router.post(
    "/side-conversations/{side_id}/messages",
    response_model=SideOut,
    status_code=status.HTTP_201_CREATED,
    summary="Write again in a side conversation",
    description="Email: threaded under the earlier emails, to the same addresses. Slack: "
    "another message in the same thread.",
    responses=_ERRORS,
)
async def write_again(
    side_id: UUID, body: AgainIn, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep,
    post: PostDep,
) -> SideOut:  # fmt: skip
    return _out(
        await sides.write_again(sm, caller, side_id, body=body.body, sealer=sealer, post=post)
    )


async def _raw(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > slack_rules.EVENT_MAX:
        raise TooLarge("Too large for a Slack event.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > slack_rules.EVENT_MAX:
            raise TooLarge("Too large for a Slack event.")
    return bytes(body)


@router.post(
    "/slack/events/{workspace_id}",
    summary="Slack's Events API (the Slack side conversations app's Request URL)",
    description="Public, for Slack only: every request must carry Slack's signature "
    "(X-Slack-Signature, X-Slack-Request-Timestamp, at most 5 minutes old), made with this "
    "workspace's signing secret, or it is refused before anything is read. Answers the URL "
    "check's challenge; a person's reply in a side conversation's thread is saved on it.",
    responses={401: {"description": "Not signed by this workspace's Slack app"}},
)
async def slack_events(
    workspace_id: UUID, request: Request, sm: SessionmakerDep, sealer: SealerDep,
    post: PostDep,
) -> dict[str, str]:  # fmt: skip
    body = await _raw(request)
    return await sides.receive_slack(
        sm, sealer, post, workspace_id, body=body,
        timestamp=request.headers.get("x-slack-request-timestamp"),
        signature=request.headers.get("x-slack-signature"), now=time.time(),
    )  # fmt: skip
