"""Keyword triage and card redaction (md I1-I5, 6.5), and their settings (Admin).

Every way a customer message comes in goes through here:
- a new ticket (`tickets._create`: email, portal, web form, Cevanos handover, an agent
  logging a call): `clean` before saving, then `for_new_ticket` before the rules run, so
  a rule on sentiment ("Upset customer goes first") sees the mood;
- a customer reply (`customer_replies.customer_reply`): `clean`, then `retriage`. Topic
  and language come from everything the customer wrote, the mood from the newest message;
- an import (md 8.2): call `retriage(s, ticket_id)` once the ticket's messages are
  saved. Rules and webhooks don't run on imports; triage does (I4).

Redaction runs on customer text only (subject included), when the workspace has it on
(the default), and writes "N card numbers removed from the customer's message".
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import redaction
from cedesk.helpdesk.domain import triage as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import triage as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid
from cedesk.helpdesk.service.members import require

BODY_MAX = 50_000  # hd_message.body
SUBJECT_MAX = 250  # hd_ticket.subject
RECENT_MESSAGES = 20  # retriage reads the first customer message and the 20 newest
MESSAGE_CHARS = 5_000  # of each


@dataclass(frozen=True)
class Config:
    keywords: Mapping[str, Sequence[str]]
    redact_cards: bool


@dataclass(frozen=True)
class TriageSettings:
    keywords: dict[str, list[str]]
    keywords_are_default: bool
    redact_cards: bool
    updated_at: datetime | None
    updated_by_name: str | None


def _defaults() -> dict[str, list[str]]:
    return {t: list(rules.DEFAULT_KEYWORDS[t]) for t in rules.TOPICS}


async def config(s: AsyncSession) -> Config:
    row = await repo.settings_row(s)
    if row is None:
        return Config(rules.DEFAULT_KEYWORDS, True)
    keywords = row["topic_keywords"]
    return Config(keywords if keywords is not None else rules.DEFAULT_KEYWORDS,
                  bool(row["redact_cards"]))  # fmt: skip


# Redaction


@dataclass(frozen=True)
class Cleaned:
    text: str
    numbers: frozenset[str] = frozenset()  # the card numbers removed (digits)


def clean(cfg: Config, value: str, limit: int = BODY_MAX) -> Cleaned:
    """Customer text with card numbers removed, when the workspace has that on."""
    if not cfg.redact_cards:
        return Cleaned(value)
    out, numbers = redaction.redact_numbers(value)
    return Cleaned(out[:limit], frozenset(numbers))


def clean_email(cfg: Config, email: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None,
                                                                    frozenset[str]]:  # fmt: skip
    """The stored email headers, with a card number in the subject removed too."""
    if email is None:
        return None, frozenset()
    out = dict(email)
    if not isinstance(out.get("subject"), str):
        return out, frozenset()
    subject = clean(cfg, out["subject"], 998)
    out["subject"] = subject.text
    return out, subject.numbers


def removed_line(count: int) -> str:
    return redaction.line(count)


# Triage


def for_new_ticket(cfg: Config, customer_text: str) -> rules.Triage:
    return rules.triage(customer_text, keywords=cfg.keywords)


async def retriage(
    s: AsyncSession, ticket_id: UUID, *, newest: str | None = None, cfg: Config | None = None
) -> rules.Triage | None:
    """Works the ticket's topic, mood and language out again from what the customer
    wrote: the subject, their first message and their newest ones (`customer_words`).
    `newest`: their newest message, which alone sets the mood (unless Cevanos's AI set
    it). Inside the caller's transaction. A language that can't be told (digits only)
    keeps the old one."""
    cfg = cfg or await config(s)
    subject = await repo.subject(s, ticket_id)
    if subject is None:
        return None
    words = await repo.customer_words(s, ticket_id, recent=RECENT_MESSAGES, chars=MESSAGE_CHARS)
    found = rules.triage(
        f"{subject} {words}",
        mood_text=newest,
        keywords=cfg.keywords,
    )
    await repo.set_triage(s, ticket_id, found.topic, found.sentiment.value, found.language)
    return found


# Settings (Admin)


async def get_settings(sm: Sessionmaker, caller: Caller) -> TriageSettings:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.settings_row(s)
    if row is None:
        return TriageSettings(_defaults(), True, True, None, None)
    saved = row["topic_keywords"]
    return TriageSettings(
        keywords={t: list(saved.get(t, [])) for t in rules.TOPICS} if saved else _defaults(),
        keywords_are_default=saved is None,
        redact_cards=bool(row["redact_cards"]),
        updated_at=row["updated_at"],
        updated_by_name=row["updated_by_name"],
    )


async def save_settings(
    sm: Sessionmaker,
    caller: Caller,
    *,
    keywords: Mapping[str, Sequence[str]] | None,
    redact_cards: bool,
) -> TriageSettings:
    """`keywords` None: back to the built-in lists. New lists apply to the next customer
    message; existing tickets keep their topic until the customer writes again."""
    require(caller, Action.settings)
    try:
        cleaned = rules.clean_keywords(keywords) if keywords is not None else None
    except rules.BadKeywords as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.settings_row(s)
        await repo.save_settings(s, caller.workspace_id, cleaned, redact_cards, caller.member_id)
        changed = audit_rules.changes(
            before or {"topic_keywords": None, "redact_cards": True},
            await repo.settings_row(s),
            fields=("topic_keywords", "redact_cards"),
        )
        if changed:
            await audit.record(
                s,
                caller,
                Area.triage,
                "changed",
                "Triage and redaction",
                detail={"changes": changed},
            )
    return await get_settings(sm, caller)
