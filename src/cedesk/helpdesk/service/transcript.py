"""Email transcript (feature-gap plan 1.6); rules in domain/transcript.py.

- Needs `send_transcript` and the right to change the ticket (the role's team scope):
  sending a conversation out of the help desk is more than reading it.
- To the ticket's customer (the default) or one other address. Not to a support address
  of a help desk on this server (it would come back in as a new ticket), not to a chat
  customer's placeholder address, not to a demo ticket's made-up customer.
- Only what the customer saw: customer messages, replies and the AI agent's lines.
  Never private notes, system lines or side conversations.
- Queued in the outbound email queue from the workspace's default support address (or
  the server's fallback sender); the text is wiped from the queue once sent. A system
  line on the ticket says who sent it to whom; the audit entry (area `tickets`) records
  who, which ticket, how many messages and whether it went to the customer or to another
  address (only that address's domain), never the text.
- Rate-limited per member and per workspace by the route (429).
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import transcript as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller, normalise_email
from cedesk.helpdesk.domain.csat import deliverable
from cedesk.helpdesk.domain.email import new_message_id
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.seeds import DEMO_SOURCE
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import inbox as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.password_reset import Sender

NO_SENDER = "Add a support email address first (Settings > Email): transcripts are sent from it."
NOTHING = "This ticket has no messages the customer saw yet."


@dataclass(frozen=True)
class Sent:
    to: str
    to_customer: bool
    messages: int


async def send(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    to: str | None,
    fallback: Sender | None,
    spend: Callable[[], None] | None = None,
) -> Sent:
    """`to`: another address; None sends it to the ticket's customer. `spend` uses one of
    the caller's (and the workspace's) transcripts; it raises when they are used up. It runs
    after every check, so a refused request costs nothing (as bulk changes)."""
    require(caller, Action.read_ticket)
    require(caller, Action.send_transcript)
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=True)
        ticket = await ticket_repo.ticket_detail(s, ticket_id)
        if ticket is None:
            raise NotFound("Ticket not found.")
        customer = normalise_email(str(ticket["contact_email"]))
        address = normalise_email(to) if to is not None else customer
        to_customer = address == customer
        if to_customer:
            if ticket["source"] == DEMO_SOURCE:
                raise Invalid("A demo ticket's customer doesn't exist. Send it to an address.")
            if not deliverable(customer):
                raise Invalid(
                    "This customer has no email address (a chat customer). Send it to an "
                    "address instead."
                )
        elif await email_repo.is_support_address(s, address):
            raise Invalid("That's a help desk support address. Send it to a person's address.")
        default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
        sender = Sender(str(default["address"]), str(default["name"])) if default else fallback
        if sender is None:
            raise Conflict(NO_SENDER)
        public = await repo.public_messages(s, ticket_id)
        if not public:
            raise Conflict(NOTHING)
        if spend is not None:
            spend()
        ticket_label = label(int(ticket["number"]))
        text, count = rules.body(
            ticket_label=ticket_label,
            ticket_subject=str(ticket["subject"]),
            customer_name=str(ticket["contact_name"]),
            messages=public,
        )
        await email_repo.enqueue(
            s,
            caller.workspace_id,
            ticket_id=ticket_id,
            message_id=None,
            from_address=sender.address,
            from_name=sender.name,
            to=[address],
            cc=[],
            subject=rules.subject(ticket_label, str(ticket["subject"])),
            body=text,
            header_message_id=new_message_id(sender.address),
            in_reply_to=None,
            references=[],
            sensitive=True,  # the conversation: wiped from the queue once sent
        )
        me = await ticket_repo.member_brief(s, caller.member_id)
        who = caller.acting(str(me["name"]) if me else "someone")
        whom = "the customer" if to_customer else address
        await ticket_repo.insert_message(
            s,
            workspace_id=caller.workspace_id,
            ticket_id=ticket_id,
            kind="system",
            body=f"Transcript emailed to {whom} by {who}"[:500],
            author_member_id=None,
            author_name=None,
        )
        await audit.record(
            s, caller, Area.tickets, "transcript_sent", ticket_label, target_id=ticket_id,
            detail={
                "to": "customer" if to_customer else "other",
                "domain": None if to_customer else rules.domain_of(address),
                "messages": count,
            },
        )  # fmt: skip
    return Sent(to=address, to_customer=to_customer, messages=count)
