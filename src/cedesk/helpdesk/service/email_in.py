"""Email in (md C8, 7.4): an email sent to a support address becomes a ticket, or a
customer message on the ticket it answers.

1. Read it; drop automatic emails (out-of-office, bounces, mailing lists) and emails sent
   from one of our own support addresses, so two systems never answer each other forever.
2. Find the workspace from the first To/CC (or delivery) address that is a support address.
3. Seen before (same Message-ID)? Nothing happens.
4. Find the ticket: In-Reply-To / References match a message on it; else a '[T-1001]'
   label in the subject, only when the sender is that ticket's customer. A merged ticket
   passes it on to the ticket it was merged into.
5. Found: `customer_reply` (reopen, thank-you, reply rules). Else a new ticket (channel
   email, rules, auto-assignment) with the address it was sent to recorded.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import email_verify as verify
from cedesk.helpdesk.domain import inbound as rules
from cedesk.helpdesk.domain.email import normalise_address
from cedesk.helpdesk.domain.tickets import Channel
from cedesk.helpdesk.repo import email as repo
from cedesk.helpdesk.service.attachments import IncomingFiles
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.customer_replies import customer_reply
from cedesk.helpdesk.service.email import verify_forwarding
from cedesk.helpdesk.service.errors import Conflict
from cedesk.helpdesk.service.tickets import NewTicket, create_from_channel
from cedesk.storage import ObjectStore

logger = logging.getLogger("cedesk.email_in")

MAX_MERGE_HOPS = 5


class Outcome(StrEnum):
    created = "created"  # a new ticket
    added = "added"  # on an open or pending ticket
    rules_ran = "rules_ran"
    reopened = "reopened"
    thanked = "thanked"  # a thank-you on a resolved ticket; it stays resolved
    duplicate = "duplicate"  # this email was already received
    automatic = "automatic"  # an auto-reply, bounce or list email: ignored
    from_us = "from_us"  # sent from one of our support addresses: ignored
    no_workspace = "no_workspace"  # not sent to any support address
    verification = "verification"  # a forwarding check (md C10): never a ticket
    unreadable = "unreadable"


@dataclass(frozen=True)
class Received:
    outcome: Outcome
    ticket_id: UUID | None = None
    workspace_id: UUID | None = None


async def _find_ticket(sm: Sessionmaker, workspace_id: UUID, email: rules.Inbound) -> UUID | None:
    async with workspace_session(sm, workspace_id) as s:
        ticket_id = await repo.ticket_for_email_ids(s, workspace_id, email.thread_ids())
        if ticket_id is None:
            number = rules.ticket_number(email.subject)
            if number is not None:
                ticket_id = await repo.ticket_for_contact_number(s, number, email.from_address)
        for _ in range(MAX_MERGE_HOPS):
            if ticket_id is None:
                break
            target = await repo.merged_into(s, ticket_id)
            if target is None:
                break
            ticket_id = target
        return ticket_id


async def receive(
    sm: Sessionmaker,
    raw: bytes,
    *,
    recipients: Sequence[str] = (),
    store: ObjectStore | None = None,
    file_max_bytes: int = 0,
) -> Received:
    """Handles one email. `recipients`: the delivery addresses the mail service reports
    (envelope To), tried after the To and CC headers; a forwarded email often needs it.
    The email's files are kept in `store`, each up to `file_max_bytes` (the API and the
    worker always pass one)."""
    try:
        email = rules.parse(raw)
    except rules.InboundError as exc:
        logger.warning("email dropped", extra={"error": str(exc), "event": "email_in"})
        return Received(Outcome.unreadable)
    extra = [a for a in (normalise_address(r) for r in recipients) if a is not None]

    # Our own forwarding check coming back (before the "from us" and auto-reply checks:
    # it is from the address itself, and a forwarder may mark it automatic).
    code = verify.code_in(email.subject)
    if code is not None:
        async with sm() as s, s.begin():
            found = await repo.workspace_for_addresses(s, [*email.to, *email.cc, *extra])
        if found is None:
            return Received(Outcome.no_workspace)
        await verify_forwarding(sm, found[0], code)
        return Received(Outcome.verification, workspace_id=found[0])
    if email.auto:
        return Received(Outcome.automatic)

    async with sm() as s, s.begin():
        if await repo.is_support_address(s, email.from_address):
            return Received(Outcome.from_us)
        found = await repo.workspace_for_addresses(s, [*email.to, *email.cc, *extra])
    if found is None:
        return Received(Outcome.no_workspace)
    workspace_id, written_to = found

    async with workspace_session(sm, workspace_id) as s:
        if await repo.email_seen(s, workspace_id, email.message_id):
            return Received(Outcome.duplicate, workspace_id=workspace_id)

    stored = email.stored()
    if written_to not in email.to:
        stored["to"] = [written_to, *email.to][:20]  # so From picks it for our reply
    files = IncomingFiles(store, email.files, file_max_bytes) if store is not None else None
    ticket_id = await _find_ticket(sm, workspace_id, email)
    if ticket_id is not None:
        result = await customer_reply(
            sm, workspace_id, ticket_id, email.text,
            email=stored, channel_message_id=email.message_id,
            author_name=email.from_name, files=files,
        )  # fmt: skip
        return Received(Outcome(result.outcome.value), ticket_id, workspace_id)

    new = NewTicket(
        subject=email.subject or "(no subject)",
        contact_email=email.from_address,
        contact_name=email.from_name,
        description=email.text,
        channel=Channel.email,
        source="email",
        source_id=email.message_id[:200],
        to_address=written_to,
        first_email=stored,
        first_channel_message_id=email.message_id[:200],
    )
    try:
        view = await create_from_channel(sm, workspace_id, new, files=files)
    except Conflict:  # the same email, delivered twice at the same moment
        return Received(Outcome.duplicate, workspace_id=workspace_id)
    return Received(Outcome.created, view.ticket["id"], workspace_id)


class Mailbox(Protocol):
    """Where the worker reads new emails from (IMAP)."""

    async def fetch(self) -> list[tuple[str, bytes]]: ...  # (id, raw email), unread only

    async def done(self, ids: Sequence[str]) -> None: ...  # mark read


async def poll(
    sm: Sessionmaker, mailbox: Mailbox, store: ObjectStore | None = None, file_max_bytes: int = 0
) -> list[Received]:
    """Reads the mailbox's unread emails. Each one handled (or ignored on purpose) is
    marked read; one that hit an error stays unread and is tried on the next pass."""
    handled: list[str] = []
    results: list[Received] = []
    for mail_id, raw in await mailbox.fetch():
        try:
            results.append(await receive(sm, raw, store=store, file_max_bytes=file_max_bytes))
        except Exception:
            logger.exception("email in failed", extra={"event": "email_in"})
            continue
        handled.append(mail_id)
    if handled:
        await mailbox.done(handled)
    return results
