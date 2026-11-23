"""A customer writes on an existing ticket (md 6.4, W16, W17).

Called by whatever brings the customer's message in: the Cevanos connector and email
(Pranav), the customer portal (Phase 2 block 2), and the "simulate" route for testing.
There is no signed-in member here, so this takes the workspace id directly.

- Resolved, and the message is only a thank-you ("thanks", "thank you", "thx", ...): the
  ticket stays resolved, with the line "Thank-you detected · ticket stays resolved".
- Resolved, anything else: the ticket reopens (`reopen_count` + 1) and the assignee is told.
- Open or Pending: the `customer_replied` rules run (the default "Reopen when the customer
  replies" rule turns Pending into Open), then the assignee is told.
- Snoozed (plan 1.1): the reply wakes it first: Open again, with the line "Customer
  replied · snooze ended"; then as for Open.
- The ticket's followers hear of the reply too (plan 1.5), except the assignee, who was
  just told.
- Pass the channel's message id when there is one: a retried delivery of the same message
  is then ignored (outcome `duplicate`), so nothing is saved or counted twice.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain import snooze as snooze_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.rules import Event as RuleEvent
from cedesk.helpdesk.domain.rules import is_thanks_only
from cedesk.helpdesk.domain.tickets import Status, label
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.repo import tickets as repo
from cedesk.helpdesk.service import automation as runs
from cedesk.helpdesk.service import followers, routing, sla, triage
from cedesk.helpdesk.service import rules as automation
from cedesk.helpdesk.service.attachments import IncomingFiles, save_incoming
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

BODY_MAX = 50_000
CHANNEL_ID_MAX = 200  # as hd_message.channel_message_id allows
THANKED = "Thank-you detected · ticket stays resolved"
REOPENED = "Customer replied · ticket reopened"


class Outcome(StrEnum):
    added = "added"  # Open or Pending, no rule changed anything
    rules_ran = "rules_ran"
    reopened = "reopened"
    thanked = "thanked"
    duplicate = "duplicate"  # that channel message was already saved; nothing changed


@dataclass(frozen=True)
class CustomerReply:
    message_id: UUID
    outcome: Outcome


async def _line(s: AsyncSession, workspace_id: UUID, ticket_id: UUID, body: str) -> None:
    await repo.insert_message(
        s,
        workspace_id=workspace_id,
        ticket_id=ticket_id,
        kind="system",
        body=body,
        author_member_id=None,
        author_name=None,
    )


async def _emit(
    s: AsyncSession,
    workspace_id: UUID,
    ticket_id: UUID,
    event: events.Event,
    extra: Mapping[str, Any],
) -> None:
    detail = await repo.ticket_detail(s, ticket_id)
    assert detail is not None
    await outbox.add(s, workspace_id, event.value, ticket_id, events.payload(event, detail, extra))


async def customer_reply(
    sm: Sessionmaker,
    workspace_id: UUID,
    ticket_id: UUID,
    body: str,
    *,
    email: Mapping[str, Any] | None = None,
    channel_message_id: str | None = None,
    author_name: str | None = None,
    files: IncomingFiles | None = None,
) -> CustomerReply:
    """Adds the customer's message and does what md 6.4 says, in one transaction.
    `author_name`: who wrote it when that is not the ticket's contact (someone in CC).
    `files`: the ones that came with it (an email's attachments)."""
    body = body.strip()
    if not body:
        raise Invalid("The message is empty.")
    if len(body) > BODY_MAX:
        raise Invalid(f"A message can be at most {BODY_MAX} characters.")
    if channel_message_id is not None and not 1 <= len(channel_message_id) <= CHANNEL_ID_MAX:
        raise Invalid(f"channel_message_id is 1-{CHANNEL_ID_MAX} characters.")

    async with workspace_session(sm, workspace_id) as s:
        before = await repo.lock_ticket(s, ticket_id)
        detail = await repo.ticket_detail(s, ticket_id)
        if before is None or detail is None:
            raise NotFound("Ticket not found.")
        if channel_message_id is not None:
            # Checked under the ticket lock, so two deliveries of one message take turns.
            # The unique index (migration 0015) is the backstop.
            seen = await repo.message_by_channel_id(s, ticket_id, channel_message_id)
            if seen is not None:
                return CustomerReply(seen, Outcome.duplicate)
        # Card numbers out before saving (I5); the rules and the thank-you check read
        # the cleaned text too.
        cfg = await triage.config(s)
        cleaned = triage.clean(cfg, body)
        body = cleaned.text
        email, email_numbers = triage.clean_email(cfg, email)
        removed = len(cleaned.numbers | email_numbers)
        if before["snoozed_until"] is not None:
            before = await _wake(s, workspace_id, before)
        message_id = await repo.insert_message(
            s,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            kind="customer",
            body=body,
            author_member_id=None,
            author_name=(author_name or str(detail["contact_name"]))[:120],
            email=email,
            channel_message_id=channel_message_id,
        )
        if removed:
            await _line(s, workspace_id, ticket_id, triage.removed_line(removed))
        # Workflow runs waiting for this customer's reply go on now (plan 5.3).
        await runs.customer_replied(s, ticket_id)
        # Topic and language from all they wrote; the mood from this message (I4).
        await triage.retriage(s, ticket_id, newest=body, cfg=cfg)
        await save_incoming(
            s, files, workspace_id=workspace_id, ticket_id=ticket_id, message_id=message_id
        )
        await message_repo.ticket_touched(s, ticket_id)
        await _emit(
            s, workspace_id, ticket_id, events.Event.message_created,
            events.message(message_id, "customer", None),
        )  # fmt: skip

        after: dict[str, Any] | None = None
        reply_note = f"{detail['contact_name']} replied on {label(before['number'])}"
        tell = before["assignee_id"]  # who hears about the reply (not for a thank-you)
        if before["status"] == Status.resolved.value:
            if is_thanks_only(body):
                await _line(s, workspace_id, ticket_id, THANKED)
                return CustomerReply(message_id, Outcome.thanked)
            after = {
                k: before[k]
                for k in ("subject", "priority", "type_id", "team_id", "assignee_id", "tags")
            } | {
                "status": Status.open.value,
                "reopen_count": before["reopen_count"] + 1,
                "fields": dict(before["fields"]),
            }
            await repo.update_ticket(s, ticket_id, after)
            await _line(s, workspace_id, ticket_id, REOPENED)
            outcome = Outcome.reopened
        else:
            run = await automation.apply(
                s, workspace_id, ticket_id, RuleEvent.customer_replied, new_message=body
            )
            outcome = Outcome.added
            if run is not None:
                after = run.after
                outcome = Outcome.rules_ran
                # A rule that moves the ticket to another team routes it again, as when
                # an agent changes the team (W8).
                if (
                    run.team_changed
                    and after["team_id"] is not None
                    and after["status"] != Status.resolved.value
                ):
                    pick = await routing.route(
                        s,
                        ticket_id=ticket_id,
                        team_id=after["team_id"],
                        channel=str(before["channel"]),
                        type_id=after["type_id"],
                    )
                    after["assignee_id"] = pick.agent.member_id if pick.agent else None
                    await repo.set_assignee(s, ticket_id, after["assignee_id"])
                    await _line(s, workspace_id, ticket_id, pick.line)
                    if pick.agent is not None:
                        # One notification for the new assignee, saying both things.
                        await message_repo.notify(
                            s,
                            workspace_id=workspace_id,
                            member_ids=[pick.agent.member_id],
                            ticket_id=ticket_id,
                            kind="assigned",
                            body=f"{reply_note}, and it was auto-assigned to you: "
                            f"{after['subject']}"[:500],
                        )
                        tell = None
                    # Nobody free in the new team: the previous assignee still hears
                    # that the customer wrote (`tell` stays as it was).
            if run is None or not run.team_changed:
                tell = (after or before)["assignee_id"]

        if after is not None:
            await sla.refresh(s, ticket_ids=[ticket_id])  # reopened, or a rule changed it
            for event, extra in events.after_change(before, after, None):
                await _emit(s, workspace_id, ticket_id, event, extra)
        subject = (after or before)["subject"]
        if tell is not None:
            await message_repo.notify(
                s,
                workspace_id=workspace_id,
                member_ids=[tell],
                ticket_id=ticket_id,
                kind="customer_reply",
                body=f"{reply_note}: {subject}"[:500],
            )
        await followers.tell(s, workspace_id, ticket_id, f"{reply_note}: {subject}",
                             exclude=[tell])  # fmt: skip
    return CustomerReply(message_id, outcome)


async def _wake(s: AsyncSession, workspace_id: UUID, before: Any) -> Any:
    """A snoozed ticket the customer wrote on: Open again (the database clears the snooze
    with the status), a line, `ticket.updated`. Returns the ticket as it is now."""
    after = {
        k: before[k]
        for k in (
            "subject",
            "priority",
            "type_id",
            "team_id",
            "assignee_id",
            "tags",
            "reopen_count",
        )
    } | {"status": Status.open.value, "fields": dict(before["fields"]), "snoozed_until": None}
    await repo.update_ticket(s, before["id"], after)
    await sla.refresh(s, ticket_ids=[before["id"]])
    await _line(s, workspace_id, before["id"], snooze_rules.CUSTOMER_WOKE_LINE)
    for event, extra in events.after_change(before, after, None):
        await _emit(s, workspace_id, before["id"], event, extra)
    now = await repo.lock_ticket(s, before["id"])
    assert now is not None
    return now


async def simulate(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, body: str, *, allowed: bool
) -> CustomerReply:
    """Admin-only stand-in for a customer, so reopen, thank-you and the reply rules can be
    tried before the connector, email and portal exist. Off (404) in production."""
    if not allowed:  # first, so a production server never shows the route exists
        raise NotFound("Not available on this server.")
    require(caller, Action.settings)
    return await customer_reply(sm, caller.workspace_id, ticket_id, body)
