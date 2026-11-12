"""Side conversations (md X5). Rules in domain/side_conversations.py.

- Starting one, or writing again in it, needs the right to reply and to change the ticket
  (team scope). The email is queued in the same transaction (`hd_email_out`, sent by the
  worker with its retries; a failure writes a line on the ticket).
- It goes out from the address the customer wrote to (else the default support address),
  with its own Message-ID, so the reply finds its way back (`receive_reply`, called by
  email in before it looks for a ticket).
- A reply is saved on the side conversation, never as a customer message, and the
  ticket's assignee is told (notification `side_reply`). The customer never sees any of it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import email as email_rules
from cedesk.helpdesk.domain import side_conversations as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import side_conversations as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_ADDRESS = "Add a support address first (Settings > Email): side conversations go out from it."


@dataclass(frozen=True)
class SideMessage:
    id: UUID
    direction: str  # out (we wrote) or in (their reply)
    body: str
    author_name: str
    from_address: str
    created_at: datetime


@dataclass(frozen=True)
class SideConversation:
    id: UUID
    subject: str
    to: list[str]
    from_address: str
    created_at: datetime
    last_message_at: datetime
    created_by_name: str | None
    messages: list[SideMessage]


def _message(row: Any) -> SideMessage:
    return SideMessage(
        row["id"],
        str(row["direction"]),
        str(row["body"]),
        str(row["author_name"]),
        str(row["from_address"]),
        row["created_at"],
    )


async def _line(s: Any, workspace_id: UUID, ticket_id: UUID, body: str) -> None:
    await ticket_repo.insert_message(
        s, workspace_id=workspace_id, ticket_id=ticket_id, kind="system", body=body,
        author_member_id=None, author_name=None,
    )  # fmt: skip


async def _sender(s: Any, ticket_id: UUID) -> email_rules.Address:
    rows = await email_repo.list_addresses(s)
    addresses = [
        email_rules.Address(str(r["address"]), str(r["name"]), "", bool(r["is_default"]))
        for r in rows
    ]
    first, _, _ = await email_repo.customer_emails(s, ticket_id)
    written_to = email_rules.addresses_in(first.get("to")) if isinstance(first, dict) else []
    sender = email_rules.pick_from(addresses, written_to)
    if sender is None:
        raise Conflict(NO_ADDRESS)
    return sender


async def start(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, *, to: list[str], subject: str | None,
    body: str,
) -> SideConversation:  # fmt: skip
    require(caller, Action.reply)
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await ticket_repo.ticket_detail(s, ticket_id)
        if ticket is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=True)
        if await repo.count_for_ticket(s, ticket_id) >= rules.PER_TICKET:
            raise Conflict(f"A ticket can have at most {rules.PER_TICKET} side conversations.")
        ours = {str(r["address"]) for r in await email_repo.list_addresses(s)}
        try:
            addresses = rules.recipients(to, customer=str(ticket["contact_email"]), ours=ours)
            text = rules.body(body)
        except rules.SideError as exc:
            raise Invalid(str(exc)) from exc
        sender = await _sender(s, ticket_id)
        title = rules.subject(subject, str(ticket["subject"]), label(int(ticket["number"])))
        me = await ticket_repo.member_brief(s, caller.member_id)
        name = str(me["name"]) if me else "Someone"
        side_id = await repo.create(
            s, caller.workspace_id, ticket_id=ticket_id, subject=title, to=addresses,
            from_address=sender.address, by=caller.member_id,
        )  # fmt: skip
        header_id = email_rules.new_message_id(sender.address)
        await repo.add_message(
            s, caller.workspace_id, side_id=side_id, direction="out", body=text,
            author_member_id=caller.member_id, author_name=name,
            from_address=sender.address, header_message_id=header_id,
        )  # fmt: skip
        await email_repo.enqueue(
            s, caller.workspace_id, ticket_id=ticket_id, message_id=None,
            from_address=sender.address, from_name=sender.name, to=addresses, cc=[],
            subject=title, body=text, header_message_id=header_id, in_reply_to=None,
            references=[],
        )  # fmt: skip
        await _line(s, caller.workspace_id, ticket_id,
                    f"Side conversation with {', '.join(addresses)} started by "
                    f"{caller.acting(name)}")  # fmt: skip
    return (await conversations(sm, caller, ticket_id, only=side_id))[0]


async def write_again(
    sm: Sessionmaker, caller: Caller, side_id: UUID, *, body: str
) -> SideConversation:
    """Another email in the same side conversation, threaded under the last one."""
    require(caller, Action.reply)
    try:
        text = rules.body(body)
    except rules.SideError as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        side = await repo.get(s, side_id)
        if side is None:
            raise NotFound("Side conversation not found.")
        await scope.check(s, caller, side["ticket_id"], edit=True)
        earlier = await repo.messages(s, [side_id])
        ids = [str(m["header_message_id"]) for m in earlier]
        me = await ticket_repo.member_brief(s, caller.member_id)
        name = str(me["name"]) if me else "Someone"
        from_address = str(side["from_address"])
        header_id = email_rules.new_message_id(from_address)
        await repo.add_message(
            s, caller.workspace_id, side_id=side_id, direction="out", body=text,
            author_member_id=caller.member_id, author_name=name, from_address=from_address,
            header_message_id=header_id,
        )  # fmt: skip
        sender_name = next(
            (str(r["name"]) for r in await email_repo.list_addresses(s)
             if str(r["address"]) == from_address),
            from_address,
        )  # fmt: skip
        await email_repo.enqueue(
            s, caller.workspace_id, ticket_id=side["ticket_id"], message_id=None,
            from_address=from_address, from_name=sender_name, to=list(side["to_addresses"]),
            cc=[], subject=rules.follow_up_subject(str(side["subject"])), body=text,
            header_message_id=header_id, in_reply_to=ids[-1] if ids else None,
            references=ids[-20:],
        )  # fmt: skip
        ticket_id = side["ticket_id"]
    return (await conversations(sm, caller, ticket_id, only=side_id))[0]


async def conversations(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, *, only: UUID | None = None
) -> list[SideConversation]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await ticket_repo.ticket_detail(s, ticket_id) is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=False)
        sides = [r for r in await repo.for_ticket(s, ticket_id) if only in (None, r["id"])]
        rows = await repo.messages(s, [r["id"] for r in sides])
    by_side: dict[UUID, list[SideMessage]] = {}
    for m in rows:
        by_side.setdefault(m["side_id"], []).append(_message(m))
    return [
        SideConversation(
            id=r["id"],
            subject=str(r["subject"]),
            to=list(r["to_addresses"]),
            from_address=str(r["from_address"]),
            created_at=r["created_at"],
            last_message_at=r["last_message_at"],
            created_by_name=r["created_by_name"],
            messages=by_side.get(r["id"], []),
        )
        for r in sides
    ]


async def receive_reply(
    sm: Sessionmaker,
    workspace_id: UUID,
    *,
    thread_ids: list[str],
    message_id: str,
    from_address: str,
    from_name: str,
    text: str,
) -> UUID | None:
    """For email in: when the email answers a side conversation, saves it there, tells the
    ticket's assignee, and returns the ticket; else None (it is for a ticket, or new)."""
    async with workspace_session(sm, workspace_id) as s:
        side = await repo.by_email_ids(s, thread_ids)
        if side is None:
            return None
        ticket_id = UUID(str(side["ticket_id"]))
        added = await repo.add_message(
            s, workspace_id, side_id=side["id"], direction="in",
            body=text[: rules.BODY_MAX] or "(empty)", author_member_id=None,
            author_name=from_name or from_address, from_address=from_address,
            header_message_id=message_id[:200],
        )  # fmt: skip
        if added is None:
            return ticket_id  # the same email again
        detail = await ticket_repo.ticket_detail(s, ticket_id)
        await _line(s, workspace_id, ticket_id, f"{from_address} replied in a side conversation")
        assignee = detail["assignee_id"] if detail is not None else None
        if detail is not None and assignee is not None:
            where = label(int(detail["number"]))
            await message_repo.notify(
                s,
                workspace_id=workspace_id,
                member_ids=[assignee],
                ticket_id=ticket_id,
                kind="side_reply",
                body=f"{from_address} replied in a side conversation on {where}",
            )
    return ticket_id
