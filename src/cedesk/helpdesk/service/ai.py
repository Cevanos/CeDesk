"""Cevanos AI features (md section 16). Rules in domain/ai.py.

Every call here needs the workspace's Cevanos connection: without one it is a 409
`cevanos_not_connected`, whatever the edition. The answer is shown to the agent and never
stored; nothing here writes to the database or the audit log. Card numbers are taken out
of every text first. A Cevanos that is down or slow is a 503 (try again); a key without
the needed scope is a 409 `cevanos_scope_missing` that says which scope.

- `available`: whether the AI buttons show (`GET /me` `ai`).
- `summary` (AI1), `improve` (AI2), `translate` (AI3), `draft` (AI4): for people signed
  in, not API keys (they spend the Cevanos workspace's AI credits). A summary or draft
  needs the right to read the ticket (team scope as always); a draft also needs `reply`.
- `numbers` (AI7): the AI card in Reports, with the `reports` right.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.cevanos_client import Cevanos, CevanosError
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import ai as rules
from cedesk.helpdesk.domain import reports as report_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import cevanos as cevanos_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.cevanos import Link, open_link
from cedesk.helpdesk.service.errors import (
    CevanosRefused,
    CevanosScopeMissing,
    Forbidden,
    Invalid,
    NotFound,
    ServiceError,
    Unavailable,
)
from cedesk.helpdesk.service.members import require

NOT_FOR_KEYS = "AI help is for people signed in, not API keys."
TICKET_NOT_FOUND = "Ticket not found."


async def available(sm: Sessionmaker, caller: Caller) -> bool:
    """True when the workspace has a Cevanos connection (the frontend shows the buttons)."""
    async with workspace_session(sm, caller.workspace_id) as s:
        return await cevanos_repo.connection(s) is not None


def failed(exc: CevanosError, needs: str) -> ServiceError:
    """A Cevanos refusal as the agent should see it."""
    if not exc.permanent:
        return Unavailable(f"Cevanos can't answer now ({exc}). Try again in a minute.")
    if exc.status == 403:
        return CevanosScopeMissing(
            f"The Cevanos key lacks the {needs} scope. An Admin adds it to the key in "
            "Cevanos, then tries again."
        )
    if exc.status == 401:
        return CevanosRefused("Cevanos refused the key. An Admin checks it in Settings, Cevanos.")
    return CevanosRefused(f"Cevanos refused this: {exc}")


async def ask[T](call: Callable[[], Awaitable[Any]], read: Callable[[Any], T], needs: str) -> T:
    """One Cevanos call and its answer read, or the error the agent should see."""
    try:
        raw = await call()
    except CevanosError as exc:
        raise failed(exc, needs) from None
    try:
        return read(raw)
    except rules.BadAnswer as exc:
        raise Unavailable(f"{exc} Try again in a minute.") from None


def _person(caller: Caller) -> None:
    if caller.api_key_id is not None:
        raise Forbidden(NOT_FOR_KEYS)


async def _link(sm: Sessionmaker, cevanos: Cevanos, caller: Caller) -> Link:
    async with workspace_session(sm, caller.workspace_id) as s:
        return await open_link(s, cevanos, caller.workspace_id)


def _checked[T](check: Callable[[], T]) -> T:
    try:
        return check()
    except rules.BadAsk as exc:
        raise Invalid(str(exc)) from None


async def _ticket_text(
    s: AsyncSession, caller: Caller, ticket_id: UUID, *, notes: bool
) -> tuple[str, Any]:
    detail = await ticket_repo.ticket_detail(s, ticket_id)
    if detail is None:
        raise NotFound(TICKET_NOT_FOUND)
    await scope.check(s, caller, ticket_id, edit=False)
    rows = await ticket_repo.messages(s, ticket_id, limit=rules.MESSAGES_MAX)
    lines = [
        rules.Line(kind=str(m["kind"]), author=m["author_name"], body=str(m["body"] or ""))
        for m in reversed(rows)
    ]
    text = rules.conversation(lines, notes=notes)
    if not text:
        raise Invalid("This ticket has no messages to work from yet.")
    return text, detail


async def summary(sm: Sessionmaker, cevanos: Cevanos, caller: Caller, ticket_id: UUID) -> str:
    """AI1: the conversation (internal notes too: it is for agents), summarised."""
    require(caller, Action.read_ticket)
    _person(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        text, _ = await _ticket_text(s, caller, ticket_id, notes=True)
        link = await open_link(s, cevanos, caller.workspace_id)
    return await ask(lambda: link.api.summarize(text=text), rules.summary, rules.AI_ASSIST)


@dataclass(frozen=True)
class Improved:
    text: str
    note: str | None = None  # for the agent, when Cevanos changed nothing


async def improve(
    sm: Sessionmaker, cevanos: Cevanos, caller: Caller, *, text: str, tone: str | None
) -> Improved:
    """AI2: the agent's text, rewritten in a tone, for the agent to edit and send; with a
    note when Cevanos sent it back unchanged."""
    require(caller, Action.read_ticket)
    _person(caller)
    text = rules.clean(_checked(lambda: rules.check_text(text)))
    tone = _checked(lambda: rules.check_tone(tone))
    link = await _link(sm, cevanos, caller)
    found = await ask(lambda: link.api.rewrite(text=text, tone=tone), rules.text, rules.AI_ASSIST)
    return Improved(found, rules.NO_CHANGES if rules.unchanged(text, found) else None)


@dataclass(frozen=True)
class Translation:
    text: str
    source: str | None  # the language Cevanos found, e.g. hi
    note: str | None = None  # for the agent: already in that language, or nothing to do


async def translate(
    sm: Sessionmaker, cevanos: Cevanos, caller: Caller, *, text: str, to: str
) -> Translation:
    """AI3: a customer's message or a reply, in another language."""
    require(caller, Action.read_ticket)
    _person(caller)
    text = rules.clean(_checked(lambda: rules.check_text(text)))
    to = _checked(lambda: rules.check_language(to))
    link = await _link(sm, cevanos, caller)
    found, source = await ask(
        lambda: link.api.translate(text=text, to=to), rules.translation, rules.AI_ASSIST
    )
    try:
        note = rules.untranslated(text, found, source, to)
    except rules.BadAnswer as exc:  # another language, sent back untranslated
        raise Unavailable(str(exc)) from None
    return Translation(found, source, note)


async def draft(sm: Sessionmaker, cevanos: Cevanos, caller: Caller, ticket_id: UUID) -> str:
    """AI4: a suggested reply, for the reply box. Never sent by itself (D10). Written
    without internal notes (the reply is for the customer), by the chat's AI agent when
    the ticket came from a Cevanos chat (its knowledge), else Cevanos's default agent."""
    require(caller, Action.reply)
    _person(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        text, _ = await _ticket_text(s, caller, ticket_id, notes=False)
        chat = await cevanos_repo.chat_by_ticket(s, ticket_id)
        link = await open_link(s, cevanos, caller.workspace_id)
    agent_id = str(chat["agent_id"]) if chat and chat["agent_id"] else None
    return await ask(
        lambda: link.api.draft(text=text, agent_id=agent_id), rules.text, rules.AI_ASSIST
    )


@dataclass(frozen=True)
class AiReport:
    start: datetime
    end: datetime
    numbers: rules.AiNumbers


async def numbers(
    sm: Sessionmaker,
    cevanos: Cevanos,
    caller: Caller,
    *,
    start: datetime | None,
    end: datetime | None,
) -> AiReport:
    """AI7: the AI card in Reports, for a period (as the other reports: by default the
    last 30 days). Cevanos's numbers for the whole workspace (they name no customer)."""
    require(caller, Action.reports)
    try:
        start, end = report_rules.period(start, end, datetime.now(UTC))
    except report_rules.BadPeriod as exc:
        raise Invalid(str(exc)) from exc
    link = await _link(sm, cevanos, caller)
    found = await ask(
        lambda: link.api.analytics(start=start.isoformat(), end=end.isoformat()),
        rules.analytics,
        rules.ANALYTICS_READ,
    )
    return AiReport(start, end, found)
