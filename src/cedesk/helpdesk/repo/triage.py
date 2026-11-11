"""SQL for triage and redaction settings, and the triage columns on a ticket. RLS applies."""

import json
from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


async def settings_row(session: AsyncSession) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT s.topic_keywords, s.redact_cards, s.updated_at, m.name AS updated_by_name "
            "FROM hd_triage_settings s LEFT JOIN member m ON m.id = s.updated_by"
        )
    )
    return result.mappings().first()


async def save_settings(
    session: AsyncSession,
    workspace_id: UUID,
    keywords: Mapping[str, Sequence[str]] | None,
    redact_cards: bool,
    member_id: UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_triage_settings (workspace_id, topic_keywords, redact_cards, "
            "updated_by) VALUES (:ws, CAST(:keywords AS jsonb), :redact, :by) "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "topic_keywords = EXCLUDED.topic_keywords, redact_cards = EXCLUDED.redact_cards, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {
            "ws": workspace_id,
            "keywords": json.dumps(keywords) if keywords is not None else None,
            "redact": redact_cards,
            "by": member_id,
        },
    )


async def subject(session: AsyncSession, ticket_id: UUID) -> str | None:
    result = await session.execute(
        text("SELECT subject FROM hd_ticket WHERE id = :id"), {"id": ticket_id}
    )
    value = result.scalar_one_or_none()
    return str(value) if value is not None else None


async def set_triage(
    session: AsyncSession, ticket_id: UUID, topic: str, sentiment: str, language: str | None
) -> None:
    """Only the triage columns; `updated_at` stays (the customer's message moves it).
    A mood from Cevanos's AI is kept: it read the whole chat, the keywords only guess."""
    await session.execute(
        text(
            "UPDATE hd_ticket SET topic = :topic, sentiment = CASE WHEN sentiment_by_cevanos "
            "THEN sentiment ELSE :sentiment END, "
            "language = coalesce(:language, language) WHERE id = :id"
        ),
        {"id": ticket_id, "topic": topic, "sentiment": sentiment, "language": language},
    )


async def customer_words(session: AsyncSession, ticket_id: UUID, *, recent: int, chars: int) -> str:
    """What triage reads on a reply: the customer's first message (what the ticket is
    about) and their `recent` newest ones, oldest first, each cut to `chars`. Bounded, so a
    long chat (every line a reply) costs the same on its 500th message as on its 5th."""
    result = await session.execute(
        text(
            "(SELECT created_at, id, left(body, :chars) AS body FROM hd_message "
            " WHERE ticket_id = :id AND kind = 'customer' ORDER BY created_at, id LIMIT 1) "
            "UNION "
            "(SELECT created_at, id, left(body, :chars) FROM hd_message "
            " WHERE ticket_id = :id AND kind = 'customer' "
            " ORDER BY created_at DESC, id DESC LIMIT :recent) "
            "ORDER BY created_at, id"
        ),
        {"id": ticket_id, "recent": recent, "chars": chars},
    )
    return " ".join(str(r.body) for r in result)
