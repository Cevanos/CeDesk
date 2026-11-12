"""The inbound API channel (md C3): another system (an app, a shop) sends what its
customer wrote, with an API key that can write.

- A new request becomes a ticket on the `api` channel through `create_from_channel`, as an
  email or a portal ticket does: card numbers out, triage, rules, SLA and routing run, the
  line says "Ticket created by Priya (api)". The system can't pick the assignee.
- A later message is the customer's reply (`customer_reply`): it can reopen a resolved
  ticket, a thank-you keeps it resolved, reply rules run.
- `external_id`, the system's own id, makes both safe to send again: the same request
  gives back the ticket made the first time, the same message is saved once.
- Only tickets that came in through the API take customer messages this way; an email or
  chat customer writes on their own channel.
- Agents' replies don't go anywhere by themselves: the system reads them with
  `GET /tickets/{id}` (webhooks come with md PL3).
"""

from dataclasses import dataclass
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.portal import subject_from
from cedesk.helpdesk.domain.tickets import Channel, Priority, label
from cedesk.helpdesk.repo import inbound_api as repo
from cedesk.helpdesk.service import customer_replies, scope, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.customer_replies import CustomerReply
from cedesk.helpdesk.service.errors import Conflict, Forbidden, NotFound
from cedesk.helpdesk.service.members import require

SOURCE = "api"  # hd_ticket.source of a ticket sent with an external_id
NOT_FOUND = "Ticket not found."


@dataclass(frozen=True)
class InboundTicket:
    contact_email: str
    contact_name: str
    message: str
    subject: str | None = None  # None: the first 60 characters of the message
    priority: Priority = Priority.medium
    type_id: UUID | None = None
    team_id: UUID | None = None
    fields: dict[str, object] | None = None
    tags: tuple[str, ...] = ()
    external_id: str | None = None


@dataclass(frozen=True)
class Received:
    ticket_id: UUID
    number: int
    created: bool  # False: this external_id had made it before


def _key_only(caller: Caller) -> None:
    if caller.api_key_id is None:
        raise Forbidden("Customer messages come in with an API key. Agents reply or add notes.")


async def new_request(sm: Sessionmaker, caller: Caller, msg: InboundTicket) -> Received:
    _key_only(caller)
    require(caller, Action.create_ticket)
    if msg.external_id is not None:
        async with workspace_session(sm, caller.workspace_id) as s:
            found = await repo.ticket_by_external_id(s, SOURCE, msg.external_id)
        if found is not None:
            return Received(found["id"], int(found["number"]), created=False)
    new = tickets.NewTicket(
        subject=msg.subject if msg.subject is not None else subject_from(msg.message),
        subject_from_description=msg.subject is None,  # remade after redaction
        contact_email=msg.contact_email,
        contact_name=msg.contact_name,
        description=msg.message,
        priority=msg.priority,
        channel=Channel.api,
        type_id=msg.type_id,
        team_id=msg.team_id,
        fields=msg.fields or {},
        tags=msg.tags,
        source=SOURCE if msg.external_id is not None else None,
        source_id=msg.external_id,
    )
    try:
        view = await tickets.create_from_channel(sm, caller.workspace_id, new)
    except Conflict:
        # The same external_id sent twice at once: the other one made it.
        async with workspace_session(sm, caller.workspace_id) as s:
            found = await repo.ticket_by_external_id(s, SOURCE, str(msg.external_id))
        if found is None:
            raise
        return Received(found["id"], int(found["number"]), created=False)
    return Received(view.ticket["id"], int(view.ticket["number"]), created=True)


async def customer_message(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, body: str, *, external_id: str | None
) -> CustomerReply:
    _key_only(caller)
    require(caller, Action.create_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.ticket_channel(s, ticket_id)
        if row is None:
            raise NotFound(NOT_FOUND)
        await scope.check(s, caller, ticket_id, edit=False)
    if row["merged_into_number"] is not None:
        raise Conflict(
            f"This ticket was merged into {label(int(row['merged_into_number']))}. "
            f"Send the message to that one ({row['merged_into_id']})."
        )
    if row["channel"] != Channel.api.value:
        raise Conflict(
            "This ticket didn't come in through the API. The customer writes on its own "
            f"channel ({row['channel']})."
        )
    return await customer_replies.customer_reply(
        sm,
        caller.workspace_id,
        ticket_id,
        body,
        channel_message_id=f"{SOURCE}:{external_id}" if external_id is not None else None,
    )
