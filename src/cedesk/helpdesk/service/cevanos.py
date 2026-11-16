"""The Cevanos connector (md C4-C7, section 7.1).

- **Connection** (Admins): the workspace's Cevanos API key and webhook signing secret,
  sealed in the database and never shown again (only the key's last 4 characters), the
  team handovers go to, and whether resolving a ticket closes the chat. No connection:
  nothing here runs and the Cevanos features stay hidden.
- **Handover in** (`receive_event`): Cevanos's signed `conversation.escalated` webhook
  becomes a ticket with the chat copied in, waiting for Accept. Rules and routing (W8) run
  as for any new ticket. `classified` sets the mood, `reopened` / `closed` are noted.
- **Convert** (`convert`): an agent turns a Cevanos chat into a ticket the same way.
- **Out** (`CevanosHandler`, run by the worker after commit): Accept takes the chat over
  in Cevanos, an agent's reply is sent into the chat, and resolving can close it.
- **Customer messages in** (`ChatPoller`, run by the worker): Cevanos has no event for a
  new customer message, so open chats are read every few seconds, within the key's
  60-a-minute limit, and each new customer message runs the customer-reply logic.
"""

import logging
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.cevanos_client import Cevanos, CevanosApi, CevanosError
from cedesk.crypto import CannotOpen
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import ai as ai_rules
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import cevanos as rules
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import Channel, Status, label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import cevanos as repo
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit, customer_replies, push, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.delivery import Event, HandlerError
from cedesk.helpdesk.service.errors import (
    CevanosNotConnected,
    CevanosRefused,
    Conflict,
    Invalid,
    NotFound,
    NotSignedIn,
    Unavailable,
)
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.tickets import ChatLine, NewTicket, TicketView

logger = logging.getLogger("cedesk.cevanos")

NOT_CONNECTED = "Cevanos isn't connected. An Admin adds the API key in Settings."
UNREADABLE = (
    "The saved Cevanos key can't be read (CEDESK_SECRET_KEY changed since it was saved). "
    "An Admin enters it again in Settings."
)


# Connection (Admin settings)


@dataclass(frozen=True)
class ConnectionView:
    connected: bool
    readable: bool  # False: CEDESK_SECRET_KEY changed, the secrets must be entered again
    api_key_hint: str | None
    close_on_resolve: bool
    team_id: UUID | None
    team_name: str | None
    updated_at: datetime | None
    webhook_path: str


@dataclass(frozen=True)
class KeyCheck:
    ok: bool
    missing_scopes: Sequence[str]
    message: str


def _bound(workspace_id: UUID, what: str) -> str:
    return f"{workspace_id}:{what}"


def webhook_path(workspace_id: UUID) -> str:
    return f"/api/v1/cevanos/events/{workspace_id}"


def _view(cevanos: Cevanos, workspace_id: UUID, row: Any) -> ConnectionView:
    if row is None:
        return ConnectionView(
            connected=False, readable=True, api_key_hint=None, close_on_resolve=False,
            team_id=None, team_name=None, updated_at=None,
            webhook_path=webhook_path(workspace_id),
        )  # fmt: skip
    try:
        cevanos.sealer.open(row["api_key_sealed"], bound_to=_bound(workspace_id, "api_key"))
        readable = True
    except CannotOpen:
        readable = False
    return ConnectionView(
        connected=True,
        readable=readable,
        api_key_hint=str(row["api_key_hint"]),
        close_on_resolve=bool(row["close_on_resolve"]),
        team_id=row["team_id"],
        team_name=row["team_name"],
        updated_at=row["updated_at"],
        webhook_path=webhook_path(workspace_id),
    )


async def get_connection(sm: Sessionmaker, cevanos: Cevanos, caller: Caller) -> ConnectionView:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.connection(s)
    return _view(cevanos, caller.workspace_id, row)


async def save_connection(
    sm: Sessionmaker,
    cevanos: Cevanos,
    caller: Caller,
    *,
    api_key: str | None,
    webhook_secret: str | None,
    close_on_resolve: bool,
    team_id: UUID | None,
) -> ConnectionView:
    """The first save needs both secrets; later, a secret left out keeps the saved one."""
    require(caller, Action.settings)
    ws = caller.workspace_id
    try:
        key = rules.check_api_key(api_key) if api_key is not None else None
        secret = rules.check_secret(webhook_secret) if webhook_secret is not None else None
    except rules.BadSetting as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, ws) as s:
        current = await repo.connection(s)
        if current is None and (key is None or secret is None):
            raise Invalid("Enter the Cevanos API key and the webhook secret.")
        if team_id is not None and not await repo.team_exists(s, team_id):
            raise Invalid("That team does not exist.")
        sealer = cevanos.sealer
        if key is not None:
            key_sealed, hint = sealer.seal(key, bound_to=_bound(ws, "api_key")), rules.hint(key)
        else:
            assert current is not None
            key_sealed, hint = current["api_key_sealed"], current["api_key_hint"]
        if secret is not None:
            secret_sealed = sealer.seal(secret, bound_to=_bound(ws, "webhook_secret"))
        else:
            assert current is not None
            secret_sealed = current["webhook_secret_sealed"]
        await repo.save_connection(
            s, ws, api_key_sealed=key_sealed, api_key_hint=hint,
            webhook_secret_sealed=secret_sealed, close_on_resolve=close_on_resolve,
            team_id=team_id, updated_by=caller.member_id,
        )  # fmt: skip
        row = await repo.connection(s)
        changed = audit_rules.changes(
            current, row, fields=("api_key_hint", "close_on_resolve", "team_id")
        )
        if current is not None and secret is not None:
            changed["webhook_secret"] = [audit_rules.HIDDEN, audit_rules.HIDDEN]
        if current is None or changed:
            await audit.record(
                s,
                caller,
                Area.cevanos,
                "connected" if current is None else "changed",
                "Cevanos connection",
                detail={"changes": changed},
            )
    return _view(cevanos, ws, row)


async def remove_connection(sm: Sessionmaker, caller: Caller) -> None:
    """Forgets the key and secret. Tickets stay; their chats are no longer synced."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.delete_connection(s):
            raise NotFound(NOT_CONNECTED)
        await audit.record(s, caller, Area.cevanos, "disconnected", "Cevanos connection")


async def check_key(sm: Sessionmaker, cevanos: Cevanos, caller: Caller) -> KeyCheck:
    """Calls Cevanos's `GET /v1/me` with the saved key and lists missing scopes."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        link = await _open(s, cevanos, caller.workspace_id)
    try:
        raw = await link.api.me()
    except CevanosError as exc:
        if not exc.permanent:
            raise Unavailable(f"Cevanos can't be checked now: {exc}") from None
        return KeyCheck(ok=False, missing_scopes=[], message=f"Cevanos refused the key: {exc}")
    data = raw.get("data") if isinstance(raw, dict) else None
    scopes = data.get("scopes") if isinstance(data, dict) else None
    have = {str(x) for x in scopes} if isinstance(scopes, list) else set()
    missing = [x for x in rules.REQUIRED_SCOPES if x not in have]
    for_ai = [x for x in ai_rules.AI_SCOPES if x not in have]
    if missing:
        text = "The key works but lacks " + ", ".join(missing) + "."
        return KeyCheck(ok=False, missing_scopes=missing + for_ai, message=text)
    if for_ai:  # handovers work; the AI features (md 16) answer 409 until it has them
        text = "The key works. The AI features also need " + ", ".join(for_ai) + "."
        return KeyCheck(ok=True, missing_scopes=for_ai, message=text)
    return KeyCheck(ok=True, missing_scopes=[], message="The key works.")


@dataclass(frozen=True)
class Link:
    """An opened connection: a client with the workspace's key, and its settings."""

    workspace_id: UUID
    api: CevanosApi
    webhook_secret: str
    close_on_resolve: bool
    team_id: UUID | None


async def _open(s: AsyncSession, cevanos: Cevanos, workspace_id: UUID) -> Link:
    """NotFound when there is no connection; Invalid when the secrets can't be read."""
    row = await repo.connection(s)
    if row is None:
        raise NotFound(NOT_CONNECTED)
    try:
        key = cevanos.sealer.open(row["api_key_sealed"], bound_to=_bound(workspace_id, "api_key"))
        secret = cevanos.sealer.open(
            row["webhook_secret_sealed"], bound_to=_bound(workspace_id, "webhook_secret")
        )
    except CannotOpen as exc:
        raise Invalid(UNREADABLE) from exc
    return Link(
        workspace_id=workspace_id,
        api=cevanos.api(key),
        webhook_secret=secret,
        close_on_resolve=bool(row["close_on_resolve"]),
        team_id=row["team_id"],
    )


async def open_link(s: AsyncSession, cevanos: Cevanos, workspace_id: UUID) -> Link:
    """For the AI features (md 16): 409 `cevanos_not_connected` without a connection,
    409 `cevanos_refused` when the saved key can't be read."""
    try:
        return await _open(s, cevanos, workspace_id)
    except NotFound:
        raise CevanosNotConnected(ai_rules.NOT_CONNECTED) from None
    except Invalid as exc:
        raise CevanosRefused(exc.message) from None


async def _line(s: AsyncSession, workspace_id: UUID, ticket_id: UUID, body: str) -> None:
    await ticket_repo.insert_message(
        s, workspace_id=workspace_id, ticket_id=ticket_id, kind="system", body=body,
        author_member_id=None, author_name=None,
    )  # fmt: skip


async def _emit(
    s: AsyncSession, workspace_id: UUID, ticket_id: UUID, event: events.Event, extra: Any
) -> None:
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    assert detail is not None
    await outbox.add(s, workspace_id, event.value, ticket_id, events.payload(event, detail, extra))


# Handover in


class Outcome(StrEnum):
    created = "created"  # a new ticket
    handed_over_again = "handed_over_again"  # the chat came back: the ticket waits again
    updated = "updated"  # mood set, or the chat's end or reopening noted
    duplicate = "duplicate"  # this event was already handled
    ignored = "ignored"  # not for CeDesk (another event, or a take-over in Cevanos)


@dataclass(frozen=True)
class Received:
    outcome: Outcome
    ticket_id: UUID | None = None


async def receive_event(
    sm: Sessionmaker,
    cevanos: Cevanos,
    workspace_id: UUID,
    body: bytes,
    signature: str | None,
    *,
    now: float | None = None,
) -> Received:
    """One webhook from Cevanos. The signature is checked before anything else is read."""
    async with workspace_session(sm, workspace_id) as s:
        try:
            link = await _open(s, cevanos, workspace_id)
        except Invalid:
            logger.warning(
                "cevanos webhook: secret unreadable", extra={"workspace_id": workspace_id}
            )
            raise NotSignedIn("The webhook secret can't be checked.") from None
    try:
        rules.verify(link.webhook_secret, body, signature, now=int(now or time.time()))
    except rules.BadSignature as exc:
        raise NotSignedIn(f"Bad signature: {exc}") from None
    try:
        event = rules.parse_event(body)
        if event.event not in rules.HANDLED_EVENTS:
            return Received(Outcome.ignored)
        conversation_id = event.conversation_id
    except rules.BadEvent as exc:
        raise Invalid(str(exc)) from exc

    if event.event == "conversation.escalated":
        if rules.is_echo(event):
            return Received(Outcome.ignored)  # someone took it over in Cevanos (or us)
        handover = rules.handover(event)
        return await _handover(sm, link, conversation_id, handover, event_id=event.event_id)
    async with workspace_session(sm, workspace_id) as s:
        chat = await repo.chat_by_conversation(s, conversation_id)
        if chat is None:
            return Received(Outcome.ignored)  # a chat that was never handed to CeDesk
        ticket_id = chat["ticket_id"]
        if event.event == "conversation.classified":
            mood = rules.SENTIMENT.get(str(event.data.get("sentiment")))
            if mood is None:
                return Received(Outcome.ignored, ticket_id)
            await repo.set_sentiment(s, ticket_id, mood)
            await _emit(s, workspace_id, ticket_id, events.Event.ticket_updated,
                        {"changes": ["sentiment"]})  # fmt: skip
            return Received(Outcome.updated, ticket_id)
        if event.event == "conversation.closed":
            if chat["closed_at"] is not None:
                return Received(Outcome.duplicate, ticket_id)
            why = rules.ENDED_BY.get(str(event.data.get("ended_by")), "closed")
            await repo.update_chat(s, ticket_id, closed_at=datetime.now(UTC))
            await _line(s, workspace_id, ticket_id, f"The chat ended in Cevanos ({why})")
            return Received(Outcome.updated, ticket_id)
        # conversation.reopened
        if event.data.get("reopened_by") != "person":
            # The customer wrote again after it went quiet: Cevanos's AI has the chat
            # again. A new handover arrives as `conversation.escalated`.
            await _line(s, workspace_id, ticket_id,
                        "The customer wrote again in Cevanos; its AI is answering")  # fmt: skip
            return Received(Outcome.updated, ticket_id)
        before = await ticket_repo.lock_ticket(s, ticket_id)
        assert before is not None
        await repo.update_chat(
            s, ticket_id, closed_at=None, next_poll_at=datetime.now(UTC), poll_failures=0
        )
        await _line(s, workspace_id, ticket_id, "The chat was reopened in Cevanos")
        if before["status"] == Status.resolved.value:
            keep = ("subject", "priority", "type_id", "team_id", "assignee_id", "tags")
            new = {k: before[k] for k in keep} | {"fields": dict(before["fields"])}
            new |= {"status": Status.open.value, "reopen_count": before["reopen_count"] + 1}
            await ticket_repo.update_ticket(s, ticket_id, new)
            await _line(s, workspace_id, ticket_id, "Status set to Open (chat reopened)")
            await _emit(s, workspace_id, ticket_id, events.Event.ticket_updated,
                        {"changes": ["status"]})  # fmt: skip
        return Received(Outcome.updated, ticket_id)


def _history(messages: Sequence[rules.ChatMessage]) -> list[ChatLine]:
    names = {"customer": None, "agent": rules.AI_NAME, "operator": rules.TEAM_NAME}
    return [
        ChatLine(
            kind=rules.ROLE_KIND[m.role],
            body=m.text.strip()[: customer_replies.BODY_MAX],
            author_name=names[m.role],
            channel_message_id=m.id,
        )
        for m in rules.kept(messages)
    ]


async def _read_chat(link: Link, conversation_id: str) -> list[rules.ChatMessage]:
    raw = await link.api.messages(conversation_id)
    try:
        return rules.parse_messages(raw)
    except rules.BadEvent as exc:
        raise CevanosError(str(exc), permanent=False) from None


async def _handover(
    sm: Sessionmaker,
    link: Link,
    conversation_id: str,
    handover: dict[str, Any],
    *,
    event_id: str | None,
    converted: bool = False,
) -> Received:
    ws = link.workspace_id
    async with workspace_session(sm, ws) as s:
        chat = await repo.chat_by_conversation(s, conversation_id)
        existing = chat["ticket_id"] if chat else await repo.ticket_by_source(s, conversation_id)
        if existing is not None:
            if converted:
                detail = await ticket_repo.ticket_detail(s, existing)
                number = label(detail["number"]) if detail else "a ticket"
                raise Conflict(f"This chat already has a ticket: {number}.")
            if chat is None:  # the ticket was made but its chat row was not (a crash)
                await repo.add_chat(
                    s, ws, existing, conversation_id=conversation_id,
                    agent_id=handover.get("agent_id"), channel_kind=handover.get("channel_kind"),
                    last_message_id=None, handover_event_id=event_id,
                )  # fmt: skip
                return Received(Outcome.duplicate, existing)
            return await _again(s, ws, existing, chat, handover, event_id)

    unread: str | None = None
    try:
        messages = await _read_chat(link, conversation_id)
    except CevanosError as exc:
        if converted:
            if exc.permanent:
                raise NotFound(f"Cevanos has no such chat for this key ({exc}).") from None
            raise Unavailable(f"Cevanos can't be reached now: {exc}") from None
        if not exc.permanent:  # Cevanos sends the webhook again later
            raise Unavailable(f"Cevanos can't be reached now: {exc}") from None
        messages, unread = [], str(exc)  # the handover must not be lost: a ticket anyway

    order_id = rules.order_id(messages)
    if order_id:
        handover["order_id"] = order_id
    new = NewTicket(
        subject=rules.subject(messages, handover.get("channel_kind")),
        contact_email=rules.contact_email(conversation_id),
        contact_name=rules.CHAT_CUSTOMER,
        channel=Channel.chat,
        team_id=link.team_id,
        conversation_id=conversation_id,
        accepted=False,
        handover=handover,
        source="cevanos",
        source_id=conversation_id,
        history=_history(messages),
    )
    try:
        view = await tickets.create_from_channel(sm, ws, new)
    except Conflict:  # two deliveries at once: the other one made it
        async with workspace_session(sm, ws) as s:
            made = await repo.ticket_by_source(s, conversation_id)
        if converted:
            raise Conflict("This chat already has a ticket.") from None
        return Received(Outcome.duplicate, made)
    ticket_id = view.ticket["id"]
    async with workspace_session(sm, ws) as s:
        await repo.add_chat(
            s, ws, ticket_id, conversation_id=conversation_id,
            agent_id=handover.get("agent_id"), channel_kind=handover.get("channel_kind"),
            last_message_id=messages[-1].id if messages else None, handover_event_id=event_id,
        )  # fmt: skip
        if unread:
            await _line(s, ws, ticket_id, f"The chat couldn't be read from Cevanos: {unread}")
        # Its assignee, else the team's Available agents, hear of it (bell and phone).
        await push.tell_handover(s, ws, ticket_id, int(view.ticket["number"]))
    return Received(Outcome.created, ticket_id)


async def _again(
    s: AsyncSession,
    ws: UUID,
    ticket_id: UUID,
    chat: Any,
    handover: dict[str, Any],
    event_id: str | None,
) -> Received:
    """The chat was handed over before. A resent event, or one while the ticket still
    waits for Accept, changes nothing; otherwise the ticket waits for Accept again."""
    before = await ticket_repo.lock_ticket(s, ticket_id)
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    assert before is not None and detail is not None
    waiting = not detail["accepted"] and before["status"] != Status.resolved.value
    if event_id is None or chat["handover_event_id"] == event_id or waiting:
        return Received(Outcome.duplicate, ticket_id)
    await repo.reopen_for_handover(s, ticket_id, event_id)
    reason = handover.get("reason") or "handover"
    await _line(s, ws, ticket_id, f"Handed over again by Cevanos ({reason}). Accept it to reply.")
    await push.tell_handover(s, ws, ticket_id, int(detail["number"]))
    if before["status"] == Status.resolved.value:
        await _emit(s, ws, ticket_id, events.Event.ticket_updated, {"changes": ["status"]})
    return Received(Outcome.handed_over_again, ticket_id)


async def convert(
    sm: Sessionmaker, cevanos: Cevanos, caller: Caller, conversation_id: str
) -> TicketView:
    """md 7.1 point 5: an agent turns a Cevanos chat into a ticket. Then Accept it."""
    require(caller, Action.create_ticket)
    try:
        conversation_id = rules.check_conversation_id(conversation_id)
    except rules.BadSetting as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        link = await _open(s, cevanos, caller.workspace_id)
    handover = {"reason": rules.CONVERTED, "converted_by": str(caller.member_id)}
    received = await _handover(sm, link, conversation_id, handover, event_id=None, converted=True)
    assert received.ticket_id is not None
    return await tickets.get_ticket(sm, caller, received.ticket_id)


# Out: take-over, replies, close (outbox handler)


class CevanosHandler:
    """Runs after commit, in order per ticket, retried by the outbox (md 3.2 step 7)."""

    name = "cevanos"
    events = frozenset(
        {events.Event.handover_accepted.value, events.Event.message_created.value,
         events.Event.ticket_resolved.value}
    )  # fmt: skip

    def __init__(self, sm: Sessionmaker, cevanos: Cevanos) -> None:
        self._sm = sm
        self._cevanos = cevanos

    async def handle(self, event: Event, effect_key: str) -> None:
        if event.ticket_id is None:
            return
        ws, ticket_id = event.workspace_id, event.ticket_id
        async with workspace_session(self._sm, ws) as s:
            chat = await repo.chat_by_ticket(s, ticket_id)
            if chat is None:
                return  # not a Cevanos chat
            try:
                link = await _open(s, self._cevanos, ws)
            except (NotFound, Invalid) as exc:
                raise HandlerError(exc.message, permanent=True) from None
            work = await self._what(s, event, chat, link)
        if work is None:
            return
        action, parts, name = work
        conversation_id = str(chat["conversation_id"])
        try:
            if action == "take_over":
                await link.api.take_over(conversation_id, operator_name=name, key=effect_key)
            elif action == "close":
                await link.api.close(conversation_id, key=effect_key)
            else:
                for i, part in enumerate(parts):
                    key = effect_key if len(parts) == 1 else f"{effect_key}.{i + 1}"
                    await link.api.reply(conversation_id, body=part, operator_name=name, key=key)
        except CevanosError as exc:
            if exc.permanent:
                async with workspace_session(self._sm, ws) as s:
                    await _line(s, ws, ticket_id, _not_done(action, str(exc)))
            raise HandlerError(str(exc), permanent=exc.permanent) from None
        async with workspace_session(self._sm, ws) as s:
            now = datetime.now(UTC)
            if action == "take_over":
                await repo.update_chat(s, ticket_id, taken_over_at=now)
            elif action == "close":
                await repo.update_chat(s, ticket_id, closed_at=now)
                await _line(s, ws, ticket_id, "The chat was closed in Cevanos")

    async def _what(
        self, s: AsyncSession, event: Event, chat: Any, link: Link
    ) -> tuple[str, list[str], str] | None:
        """(action, reply parts, the agent's name), or None when there is nothing to do."""
        if event.event == events.Event.handover_accepted.value:
            member = await ticket_repo.member_brief(s, UUID(str(event.payload.get("by"))))
            return "take_over", [], str(member["name"]) if member else "An agent"
        if event.event == events.Event.ticket_resolved.value:
            if not link.close_on_resolve or chat["closed_at"] is not None:
                return None
            return "close", [], ""
        info = event.payload.get("message") or {}
        if info.get("kind") != "agent":
            return None  # notes, customer messages and system lines stay in CeDesk
        message = await repo.message(s, UUID(str(info["id"])))
        if message is None:
            return None
        name = str(message["author_name"] or "An agent")
        return "reply", rules.split_reply(str(message["body"])), name


def _not_done(action: str, error: str) -> str:
    what = {"take_over": "Not taken over in Cevanos", "close": "The chat was not closed"}
    return f"{what.get(action, 'Not sent to the chat')}: {error}"[:500]


# Customer messages in (polling)


@dataclass
class PollResult:
    read: int = 0
    added: int = 0
    failed: int = 0


@dataclass
class ChatPoller:
    """Reads open chats for new customer messages. Keeps, per workspace, the reads of the
    last minute (to stay under the key's limit) and a pause after a 429."""

    sm: Sessionmaker
    cevanos: Cevanos
    batch: int = 20
    per_minute: int = rules.READS_PER_MINUTE
    _reads: dict[UUID, deque[float]] = field(default_factory=dict)
    _paused_until: dict[UUID, float] = field(default_factory=dict)

    def _room(self, workspace_id: UUID, now: float) -> int:
        if self._paused_until.get(workspace_id, 0) > now:
            return 0
        reads = self._reads.setdefault(workspace_id, deque())
        while reads and reads[0] <= now - 60:
            reads.popleft()
        return max(0, min(self.batch, self.per_minute - len(reads)))

    async def poll(self) -> PollResult:
        result = PollResult()
        async with self.sm() as s, s.begin():
            workspace_ids = await auth_repo.all_workspace_ids(s)
        for workspace_id in workspace_ids:
            try:
                await self.poll_workspace(workspace_id, result)
            except Exception:
                logger.exception("cevanos poll failed", extra={"workspace_id": workspace_id})
        return result

    async def poll_workspace(self, ws: UUID, result: PollResult | None = None) -> PollResult:
        result = result if result is not None else PollResult()
        room = self._room(ws, time.monotonic())
        if room == 0:
            return result
        async with workspace_session(self.sm, ws) as s:
            if await repo.connection(s) is None:
                return result
            try:
                link = await _open(s, self.cevanos, ws)
            except Invalid:
                return result  # shown to Admins on the settings page
            chats = await repo.claim_due(s, room, rules.POLL_LEASE_SECONDS)
        for chat in chats:
            self._reads[ws].append(time.monotonic())
            result.read += 1
            try:
                result.added += await self._chat(link, chat)
            except _Paused:
                break
            except CevanosError:
                result.failed += 1
        return result

    async def _chat(self, link: Link, chat: Any) -> int:
        ws, ticket_id = link.workspace_id, chat["ticket_id"]
        failures = int(chat["poll_failures"])
        try:
            messages = await _read_chat(link, str(chat["conversation_id"]))
        except CevanosError as exc:
            async with workspace_session(self.sm, ws) as s:
                if exc.status == 429:
                    wait = exc.retry_after or rules.PAUSE_SECONDS
                    self._paused_until[ws] = time.monotonic() + wait
                    at = datetime.now(UTC) + timedelta(seconds=wait)
                    await repo.update_chat(s, ticket_id, next_poll_at=at)
                    raise _Paused from None
                if exc.permanent:
                    await repo.update_chat(s, ticket_id, closed_at=datetime.now(UTC))
                    await _line(s, ws, ticket_id, f"Stopped reading this chat from Cevanos: {exc}")
                else:
                    wait = rules.poll_wait(failures + 1)
                    at = datetime.now(UTC) + timedelta(seconds=wait)
                    await repo.update_chat(
                        s, ticket_id, next_poll_at=at, poll_failures=failures + 1
                    )
            raise
        added = 0
        for m in rules.after(messages, chat["last_message_id"]):
            if m.role != "customer" or not m.text.strip():
                continue  # the AI is silent after a handover; our own replies come back too
            try:
                done = await customer_replies.customer_reply(
                    self.sm, ws, ticket_id, m.text.strip()[: customer_replies.BODY_MAX],
                    channel_message_id=m.id,
                )  # fmt: skip
            except (Invalid, NotFound):
                continue
            if done.outcome is not customer_replies.Outcome.duplicate:
                added += 1
        async with workspace_session(self.sm, ws) as s:
            values: dict[str, Any] = {
                "next_poll_at": datetime.now(UTC) + timedelta(seconds=rules.POLL_SECONDS),
                "poll_failures": 0,
            }
            if messages:
                values["last_message_id"] = messages[-1].id
            await repo.update_chat(s, ticket_id, **values)
        return added


class _Paused(Exception):
    """Cevanos said 429: this workspace's reads stop until the pause ends."""
