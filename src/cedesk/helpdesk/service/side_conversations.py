"""Side conversations (md X5). Rules in domain/side_conversations.py.

- Starting one, or writing again in it, needs the right to reply and to change the ticket
  (team scope). The email is queued in the same transaction (`hd_email_out`, sent by the
  worker with its retries; a failure writes a line on the ticket).
- It goes out from the address the customer wrote to (else the default support address),
  with its own Message-ID, so the reply finds its way back (`receive_reply`, called by
  email in before it looks for a ticket).
- A reply is saved on the side conversation, never as a customer message, and the
  ticket's assignee is told (notification `side_reply`). The customer never sees any of it.
- In Slack (the `slack` app): the bot posts the agent's message in a channel it is in
  (at once, so the thread is known), and the thread is the side conversation. Slack sends
  each message in it to `receive_slack`, which checks Slack's signature with the app's
  signing secret before it reads anything; a person's reply comes back as an email reply
  does. Writing again posts in the same thread.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from cedesk.crypto import Sealer
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import email as email_rules
from cedesk.helpdesk.domain import side_conversations as rules
from cedesk.helpdesk.domain import slack as slack_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import messages as message_repo
from cedesk.helpdesk.repo import side_conversations as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import scope, slack_bot
from cedesk.helpdesk.service.app_post import AppCallError
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import (
    Conflict,
    Invalid,
    NotFound,
    NotSignedIn,
    ServiceError,
    Unavailable,
)
from cedesk.helpdesk.service.jira import PostFactory
from cedesk.helpdesk.service.members import require

NO_ADDRESS = "Add a support address first (Settings > Email): side conversations go out from it."
NO_SLACK = "Install the Slack side conversations app first (Settings > Apps)."
BAD_SIGNATURE = "The request isn't signed by this workspace's Slack app."
NAME_WAIT = 1.5  # seconds for Slack's users.info before a reply keeps the Slack id

logger = logging.getLogger("cedesk.slack")


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
    via: str  # email or slack
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


async def _line(s: Any, workspace_id: UUID, ticket_id: UUID, body: str) -> UUID:
    return await ticket_repo.insert_message(
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
    sm: Sessionmaker,
    caller: Caller,
    side_id: UUID,
    *,
    body: str,
    sealer: Sealer | None = None,
    post: PostFactory | None = None,
) -> SideConversation:
    """Another email in the same side conversation, threaded under the last one; in Slack,
    another message in the thread (`sealer` and `post` reach the bot)."""
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
        me = await ticket_repo.member_brief(s, caller.member_id)
        if side["via"] == "email":  # in the same transaction as the checks
            await _again_by_email(s, caller, side, text, _name(me))
    if side["via"] == "slack":
        if sealer is None or post is None:
            raise Unavailable("Slack can't be reached from here.")
        return await _again_in_slack(sm, sealer, post, caller, side, text, me)
    return (await conversations(sm, caller, side["ticket_id"], only=side_id))[0]


async def _again_by_email(s: Any, caller: Caller, side: Any, text: str, name: str) -> None:
    earlier = await repo.messages(s, [side["id"]])
    ids = [str(m["header_message_id"]) for m in earlier]
    from_address = str(side["from_address"])
    header_id = email_rules.new_message_id(from_address)
    await repo.add_message(
        s, caller.workspace_id, side_id=side["id"], direction="out", body=text,
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
            via=str(r["via"]),
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
        await _replied(s, workspace_id, ticket_id, from_address,
                       f"{from_address} replied in a side conversation")  # fmt: skip
    return ticket_id


async def _replied(s: Any, workspace_id: UUID, ticket_id: UUID, who: str, line: str) -> UUID:
    """A reply came back: a line on the ticket, and the assignee is told. The line's id."""
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    line_id = await _line(s, workspace_id, ticket_id, line)
    assignee = detail["assignee_id"] if detail is not None else None
    if detail is not None and assignee is not None:
        where = label(int(detail["number"]))
        await message_repo.notify(
            s,
            workspace_id=workspace_id,
            member_ids=[assignee],
            ticket_id=ticket_id,
            kind="side_reply",
            body=f"{who} replied in a side conversation on {where}"[:500],
        )
    return line_id


# Slack (the `slack` app)


async def _bot(
    sm: Sessionmaker, sealer: Sealer, post: PostFactory, workspace_id: UUID
) -> slack_bot.Bot:
    bot = await slack_bot.load(sm, sealer, post, workspace_id)
    if bot is None:
        raise Conflict(NO_SLACK)
    return bot


async def _say(
    bot: slack_bot.Bot, channel: str, text: str, *, thread_ts: str | None = None
) -> tuple[str, str]:
    try:
        return await bot.say(channel, text, thread_ts=thread_ts)
    except AppCallError as exc:
        error: ServiceError = Invalid(str(exc)) if exc.permanent else Unavailable(str(exc))
        raise error from None


def _name(me: Any) -> str:
    return str(me["name"]) if me else "Someone"


async def start_in_slack(
    sm: Sessionmaker, sealer: Sealer, post: PostFactory, caller: Caller, ticket_id: UUID, *,
    channel: str, body: str,
) -> SideConversation:  # fmt: skip
    """The bot posts the agent's message in the channel (it must be a member there); its
    thread is the side conversation."""
    require(caller, Action.reply)
    try:
        where = slack_rules.channel(channel)
        text = rules.body(body)
    except (slack_rules.BadSlack, rules.SideError) as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await ticket_repo.ticket_detail(s, ticket_id)
        if ticket is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=True)
        if await repo.count_for_ticket(s, ticket_id) >= rules.PER_TICKET:
            raise Conflict(f"A ticket can have at most {rules.PER_TICKET} side conversations.")
        name = _name(await ticket_repo.member_brief(s, caller.member_id))
    ticket_label = label(int(ticket["number"]))
    bot = await _bot(sm, sealer, post, caller.workspace_id)
    said = slack_rules.side_text(ticket_label, str(ticket["subject"]), text, caller.acting(name))
    channel_id, ts = await _say(bot, where, said)
    async with workspace_session(sm, caller.workspace_id) as s:
        side_id = await repo.create(
            s, caller.workspace_id, ticket_id=ticket_id,
            subject=rules.subject(None, str(ticket["subject"]), ticket_label), to=[where],
            from_address="", by=caller.member_id, slack=(channel_id, ts),
        )  # fmt: skip
        await repo.add_message(
            s, caller.workspace_id, side_id=side_id, direction="out", body=text,
            author_member_id=caller.member_id, author_name=name, from_address="",
            header_message_id=slack_rules.message_key(channel_id, ts),
        )  # fmt: skip
        started = f"Side conversation in Slack {where} started by {caller.acting(name)}"
        await _line(s, caller.workspace_id, ticket_id, started)
    return (await conversations(sm, caller, ticket_id, only=side_id))[0]


async def _again_in_slack(
    sm: Sessionmaker, sealer: Sealer, post: PostFactory, caller: Caller, side: Any, text: str,
    me: Any,
) -> SideConversation:  # fmt: skip
    """Another message in the side conversation's Slack thread."""
    name = _name(me)
    bot = await _bot(sm, sealer, post, caller.workspace_id)
    said = slack_rules.thread_text(text, caller.acting(name))
    channel_id, ts = await _say(bot, str(side["slack_channel"]), said,
                                thread_ts=str(side["slack_ts"]))  # fmt: skip
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.add_message(
            s, caller.workspace_id, side_id=side["id"], direction="out", body=text,
            author_member_id=caller.member_id, author_name=name, from_address="",
            header_message_id=slack_rules.message_key(channel_id, ts),
        )  # fmt: skip
    return (await conversations(sm, caller, side["ticket_id"], only=side["id"]))[0]


async def receive_slack(
    sm: Sessionmaker,
    sealer: Sealer,
    post: PostFactory,
    workspace_id: UUID,
    *,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    now: float,
) -> dict[str, str]:
    """One request from Slack's Events API to this workspace's URL. Nothing is read before
    the signature is checked with the workspace's signing secret (401 otherwise). The URL
    check gets its challenge back; a person's reply in a side conversation's thread is
    saved there; anything else is ignored (Slack only needs a 200)."""
    bot = await slack_bot.load(sm, sealer, post, workspace_id)
    if bot is None or not slack_rules.signature_ok(
        bot.signing_secret, timestamp, body, signature, now
    ):
        raise NotSignedIn(BAD_SIGNATURE)
    try:
        payload = json.loads(body)
    except ValueError:
        raise Invalid("Slack sent something that isn't JSON.") from None
    if not isinstance(payload, dict):
        return {}
    if payload.get("type") == "url_verification":
        return {"challenge": str(payload.get("challenge") or "")[:200]}
    reply = slack_rules.thread_reply(payload) if payload.get("type") == "event_callback" else None
    if reply is None:
        return {}
    async with workspace_session(sm, workspace_id) as s:
        side = await repo.by_slack_thread(s, reply.channel, reply.thread_ts)
    if side is None:
        return {}  # a thread that isn't a side conversation
    # Saved first, under the person's Slack id, with the ticket's line and the assignee's
    # notification in the same transaction: Slack wants its answer within 3 s, and asking
    # Slack for the name may be slow. The name follows if Slack says it in time; a crash
    # in between loses only the nicer name.
    who = slack_bot.stand_in(reply.user)
    where = str(next(iter(side["to_addresses"])))
    async with workspace_session(sm, workspace_id) as s:
        added = await repo.add_message(
            s, workspace_id, side_id=side["id"], direction="in",
            body=_slack_body(reply, {}), author_member_id=None, author_name=who,
            from_address="", header_message_id=slack_rules.message_key(reply.channel, reply.ts),
        )  # fmt: skip
        if added is None:
            return {}  # Slack sent the same message again
        line_id = await _replied(s, workspace_id, UUID(str(side["ticket_id"])), who,
                                 _slack_line(who, where))  # fmt: skip
    name = await _name_in_time(bot, reply.user)
    if name is None:
        return {}
    async with workspace_session(sm, workspace_id) as s:
        await repo.name_message(
            s, added, author_name=name, body=_slack_body(reply, {reply.user: name})
        )
        await repo.rename_line(s, line_id, _slack_line(name, where))
    return {}


def _slack_line(who: str, where: str) -> str:
    return f"{who} replied in the side conversation in Slack {where}"


def _slack_body(reply: slack_rules.ThreadReply, names: dict[str, str]) -> str:
    return slack_rules.reply_body(reply, names)[: rules.BODY_MAX] or "(empty)"


async def _name_in_time(bot: slack_bot.Bot, user: str) -> str | None:
    """The replier's Slack name, or None when Slack is slow or fails (never an error)."""
    try:
        return await asyncio.wait_for(bot.name_of(user), NAME_WAIT)
    except Exception as exc:  # a slow or broken lookup must not lose the reply
        logger.warning("slack: name lookup failed (%s)", type(exc).__name__)
        return None
