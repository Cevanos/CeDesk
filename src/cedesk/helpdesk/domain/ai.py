"""Cevanos AI features in CeDesk (md section 16). No database, no HTTP.

CeDesk has no AI of its own (D11): every feature here asks Cevanos, with the workspace's
Cevanos key, and only when the workspace has a Cevanos connection (the gate is the
connection, not the edition). Nothing an answer says is stored unless the agent sends or
saves it. Card numbers are taken out of every text before it leaves CeDesk.

What each feature sends and reads back (Cevanos `/v1`, `{"data": ...}`):
- AI1 summary: the ticket's conversation -> `{"summary"}`
- AI2 improve: the agent's text and a tone -> `{"text"}`
- AI3 translate: a text and a language -> `{"text", "from"}`
- AI4 draft: the conversation (no internal notes) and the chat's AI agent -> `{"text"}`
- AI5 orders and payments: a contact's, from Cevanos's connected stores
- AI6 contact: a chat's real name and email
- AI7 analytics: handover rate, AI-resolved, CSAT of AI answers
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from cedesk.helpdesk.domain.apps import Order, Payment
from cedesk.helpdesk.domain.redaction import redact

AI_ASSIST = "ai:assist"
CONTACTS_READ = "contacts:read"
CONVERSATIONS_READ = "conversations:read"
ANALYTICS_READ = "analytics:read"
AI_SCOPES = (AI_ASSIST, CONTACTS_READ, ANALYTICS_READ)
"""Scopes only the AI features need: a key without them still runs handovers."""

NOT_CONNECTED = "Connect Cevanos in Settings to use this."

TONES = ("professional", "friendly", "empathetic", "concise")
TEXT_MAX = 8000  # what an agent sends to improve or translate (one reply's worth)
CONVERSATION_MAX = 24_000  # characters of a ticket sent for a summary or a draft
MESSAGES_MAX = 60  # its newest messages, at most
ANSWER_MAX = 16_000  # what CeDesk passes on from Cevanos, at most
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")

SPEAKERS = {"customer": "Customer", "agent": "Agent", "ai": "AI", "note": "Internal note"}
"""How each message kind is introduced; system lines are never sent."""


class BadAsk(ValueError):
    """What the agent sent can't go to Cevanos (empty, too long, unknown tone)."""


class BadAnswer(ValueError):
    """Cevanos answered without what was asked for."""


@dataclass(frozen=True)
class Line:
    kind: str  # customer, agent, ai, note, system
    author: str | None
    body: str


def check_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise BadAsk("Write something first.")
    if len(text) > TEXT_MAX:
        raise BadAsk(f"At most {TEXT_MAX} characters at a time.")
    return text


def check_tone(tone: str | None) -> str:
    tone = (tone or TONES[0]).strip().lower()
    if tone not in TONES:
        raise BadAsk(f"Tone is one of: {', '.join(TONES)}.")
    return tone


def check_language(code: str) -> str:
    code = code.strip()
    if not _LANGUAGE.match(code):
        raise BadAsk("Give the language as a code, e.g. en, hi or pt-BR.")
    return code


def clean(text: str) -> str:
    """What leaves CeDesk: card numbers taken out (they never reach the database, but an
    agent may type or paste one)."""
    return redact(text)[0]


def conversation(lines: Sequence[Line], *, notes: bool) -> str:
    """The ticket as text for Cevanos: `Customer (Priya): ...` per message, oldest first.
    Only the newest messages that fit; system lines never; notes only when asked (the
    summary is for agents; a draft is for the customer, so it never sees a note)."""
    out: list[str] = []
    size = 0
    for line in reversed(lines[-MESSAGES_MAX:]):
        if line.kind not in SPEAKERS or (line.kind == "note" and not notes):
            continue
        who = SPEAKERS[line.kind] + (f" ({line.author})" if line.author else "")
        entry = f"{who}: {line.body.strip()}"
        if size + len(entry) > CONVERSATION_MAX:
            if not out:  # one long message: its end is cut, never the whole ticket
                out.append(entry[:CONVERSATION_MAX])
            break
        out.append(entry)
        size += len(entry) + 2
    return clean("\n\n".join(reversed(out)))


def _data(raw: Any) -> Any:
    if not isinstance(raw, Mapping) or "data" not in raw:
        raise BadAnswer("Cevanos sent an answer CeDesk can't read.")
    return raw["data"]


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadAnswer(f"Cevanos sent no {what}.")
    return value.strip()[:ANSWER_MAX]


def summary(raw: Any) -> str:
    data = _data(raw)
    return _text(data.get("summary") if isinstance(data, Mapping) else None, "summary")


def text(raw: Any) -> str:
    data = _data(raw)
    return _text(data.get("text") if isinstance(data, Mapping) else None, "text")


def translation(raw: Any) -> tuple[str, str | None]:
    data = _data(raw)
    if not isinstance(data, Mapping):
        raise BadAnswer("Cevanos sent no translation.")
    found = data.get("from")
    source = found.strip()[:20] if isinstance(found, str) and found.strip() else None
    return _text(data.get("text"), "translation"), source


# When Cevanos sends the text back as it was (live, Phase 3 retest: every translation came
# back untranslated, and "Shorter" never changed a reply), the agent is told so instead
# of being shown the same text as if it were the result.
NOT_TRANSLATED = "Cevanos didn't translate this. Try again later."
NOTHING_TO_TRANSLATE = "Cevanos found nothing to translate in this text."
NO_CHANGES = "No changes suggested. Try another tone."
LANGUAGES = {"en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "de": "German",
             "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "ar": "Arabic",
             "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}  # fmt: skip


def unchanged(before: str, after: str) -> bool:
    """The same text, spaces aside (a change of case or spelling is a change)."""
    return " ".join(before.split()) == " ".join(after.split())


def _base(code: str) -> str:
    return code.split("-")[0].strip().lower()


def untranslated(text: str, found: str, source: str | None, to: str) -> str | None:
    """Why a translation that came back as the text itself is shown as it is (a note for
    the agent), or None when it was translated. Raises `BadAnswer` when it should have
    been translated: Cevanos found another language but sent the text back."""
    if not unchanged(text, found):
        return None
    if source is None:
        return NOTHING_TO_TRANSLATE
    if _base(source) == _base(to):
        name = LANGUAGES.get(_base(to))
        return f"This is already in {name}." if name else "This is already in that language."
    if len(_WORD.findall(text)) < 2:
        # An order number, a code or a single name comes back as it was, rightly (live
        # retest: "ORD-1042" to Hindi, with from: en), so it isn't Cevanos failing.
        return NOTHING_TO_TRANSLATE
    raise BadAnswer(NOT_TRANSLATED)


_WORD = re.compile(r"[^\W\d_]{2,}")
"""A word worth translating: two or more letters, in any script."""


# AI6: the chat's contact


@dataclass(frozen=True)
class Contact:
    id: str | None
    name: str | None
    email: str | None


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


def contact(raw: Any) -> Contact | None:
    """The chat's contact on `GET /v1/conversations/{id}`; None when it has none."""
    data = _data(raw)
    found = data.get("contact") if isinstance(data, Mapping) else None
    if not isinstance(found, Mapping):
        return None
    name, email, cid = found.get("name"), found.get("email"), found.get("id")
    name = name.strip()[:200] if isinstance(name, str) and name.strip() else None
    email = email.strip().lower() if isinstance(email, str) else None
    if email and (not _EMAIL.match(email) or email.endswith(".invalid")):
        email = None
    cid = str(cid)[:100] if isinstance(cid, str | int) and str(cid) else None
    if name is None and email is None and cid is None:
        return None
    return Contact(id=cid, name=name, email=email)


def contact_ids(raw: Any) -> list[str]:
    """`GET /v1/contacts?email=`: the ids of the contacts with that email."""
    data = _data(raw)
    rows = data if isinstance(data, list) else []
    return [str(r["id"])[:100] for r in rows if isinstance(r, Mapping) and r.get("id")][:3]


# AI5: orders and payments from Cevanos's stores


@dataclass(frozen=True)
class Unread:
    source: str
    message: str


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _money(value: Any) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return "0.00"


def _opt(value: Any, limit: int = 200) -> str | None:
    return str(value)[:limit] if isinstance(value, str | int) and str(value) else None


def _unread(data: Mapping[str, Any]) -> list[Unread]:
    out = []
    for u in data.get("unavailable") or []:
        if isinstance(u, Mapping) and u.get("source"):
            out.append(Unread(str(u["source"])[:50], str(u.get("message") or "unavailable")[:200]))
    return out


def orders(raw: Any) -> tuple[list[Order], list[Unread]]:
    """`{"data": {"orders": [...], "unavailable": [{"source", "message"}]}}`."""
    data = _data(raw)
    if not isinstance(data, Mapping):
        raise BadAnswer("Cevanos sent no orders.")
    found = []
    for o in data.get("orders") or []:
        if not isinstance(o, Mapping) or not o.get("id"):
            continue
        items = o.get("items")
        found.append(Order(
            id=str(o["id"])[:100],
            number=_opt(o.get("number"), 50) or str(o["id"])[:50],
            created_at=_when(o.get("created_at")),
            payment=_opt(o.get("payment"), 50),
            fulfillment=_opt(o.get("fulfillment"), 50),
            total=_money(o.get("total")),
            currency=(_opt(o.get("currency"), 3) or "").upper(),
            items=items if isinstance(items, int) and items >= 0 else 0,
            url=_opt(o.get("url"), 500),
        ))  # fmt: skip
    return found, _unread(data)


def payments(raw: Any) -> tuple[list[Payment], list[Unread]]:
    """`{"data": {"payments": [...], "unavailable": [...]}}`."""
    data = _data(raw)
    if not isinstance(data, Mapping):
        raise BadAnswer("Cevanos sent no payments.")
    found = []
    for p in data.get("payments") or []:
        if not isinstance(p, Mapping) or not p.get("id"):
            continue
        found.append(Payment(
            id=str(p["id"])[:100],
            amount=_money(p.get("amount")),
            currency=(_opt(p.get("currency"), 3) or "").upper(),
            status=_opt(p.get("status"), 50) or "unknown",
            refunded=bool(p.get("refunded")),
            created_at=_when(p.get("created_at")),
            description=_opt(p.get("description"), 300),
            url=_opt(p.get("url"), 500),
        ))  # fmt: skip
    return found, _unread(data)


# AI7: the AI numbers in Reports


@dataclass(frozen=True)
class AiNumbers:
    conversations: int
    handed_over: int
    handover_rate: float | None
    ai_resolved: int
    ai_resolved_rate: float | None
    ai_csat: float | None  # 1-5
    ai_csat_responses: int


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _rate(value: Any, top: float) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= top:
        return round(float(value), 4)
    return None


def analytics(raw: Any) -> AiNumbers:
    data = _data(raw)
    if not isinstance(data, Mapping):
        raise BadAnswer("Cevanos sent no numbers.")
    return AiNumbers(
        conversations=_count(data.get("conversations")),
        handed_over=_count(data.get("handed_over")),
        handover_rate=_rate(data.get("handover_rate"), 1),
        ai_resolved=_count(data.get("ai_resolved")),
        ai_resolved_rate=_rate(data.get("ai_resolved_rate"), 1),
        ai_csat=_rate(data.get("ai_csat"), 5),
        ai_csat_responses=_count(data.get("ai_csat_responses")),
    )
