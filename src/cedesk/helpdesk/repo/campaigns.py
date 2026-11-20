"""SQL for email campaigns and the suppression list (feature-gap plan phase 6). Run
inside a `workspace_session`.

The suppression key in SQL is the same as `domain.campaigns.email_hash`:
sha256("<workspace id>:<lower-case address>"). Contact addresses are stored lower-case.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.campaigns import ROLE_LOCALS
from cedesk.helpdesk.repo import contacts as contact_repo

Row = RowMapping


def _hash(column: str) -> str:
    return f"sha256(convert_to(CAST(:ws AS text) || ':' || lower({column}), 'UTF8'))"


# A contact that may get campaign email: a real address, not a mail system's role box and
# not a support address of any workspace (the global directory).
_MAILABLE = (
    "(c.email NOT LIKE '%.invalid' AND split_part(c.email, '@', 1) <> ALL(:roles) "
    "AND NOT EXISTS (SELECT 1 FROM email_address_directory d WHERE d.address = c.email))"
)
# Suppressed: the address, or any other address of theirs (merged in), is on the list.
_SUPPRESSED = (
    f"(EXISTS (SELECT 1 FROM hd_suppression x WHERE x.email_hash = {_hash('c.email')}) "  # noqa: S608
    "OR EXISTS (SELECT 1 FROM hd_contact_alias a JOIN hd_suppression x "
    f"ON x.email_hash = {_hash('a.email')} WHERE a.contact_id = c.id))"
)


def _audience_params(workspace_id: UUID, filters: Mapping[str, Any]) -> dict[str, Any]:
    return contact_repo.segment_params(filters) | {
        "ws": str(workspace_id),
        "roles": sorted(ROLE_LOCALS),
    }


# Settings


async def get_settings(session: AsyncSession) -> Row | None:
    result = await session.execute(
        text("SELECT postal_address, updated_at FROM hd_campaign_settings")
    )
    return result.mappings().first()


async def save_settings(
    session: AsyncSession, workspace_id: UUID, *, postal_address: str, by: UUID
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_campaign_settings (workspace_id, postal_address, updated_by) "
            "VALUES (:ws, :postal, :by) ON CONFLICT (workspace_id) DO UPDATE SET "
            "postal_address = EXCLUDED.postal_address, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()"
        ),
        {"ws": workspace_id, "postal": postal_address, "by": by},
    )


# Suppression list

_SUPPRESSION = (
    "SELECT x.id, x.email, x.reason, x.source, x.campaign_id, k.name AS campaign_name, "
    "x.note, x.created_by, m.name AS created_by_name, x.created_at "
    "FROM hd_suppression x LEFT JOIN hd_campaign k ON k.id = x.campaign_id "
    "LEFT JOIN member m ON m.id = x.created_by "
)


async def suppression_page(
    session: AsyncSession,
    *,
    before: tuple[datetime, UUID] | None,
    limit: int,
    reason: str | None,
    email_hash: bytes | None,
) -> Sequence[Row]:
    """Newest first. `email_hash`: only that address (a search for one address)."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            _SUPPRESSION + "WHERE (CAST(:reason AS text) IS NULL OR x.reason = :reason) "
            "AND (CAST(:hash AS bytea) IS NULL OR x.email_hash = :hash) "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (x.created_at, x.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY x.created_at DESC, x.id DESC LIMIT :limit"
        ),
        {"reason": reason, "hash": email_hash, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def suppression_counts(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        text("SELECT reason, count(*) FROM hd_suppression GROUP BY reason")
    )
    return {str(r[0]): int(r[1]) for r in result.all()}


async def get_suppression(session: AsyncSession, suppression_id: UUID) -> Row | None:
    result = await session.execute(text(_SUPPRESSION + "WHERE x.id = :id"), {"id": suppression_id})
    return result.mappings().first()


async def suppress(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    email_hash: bytes,
    email: str | None,
    reason: str,
    source: str,
    campaign_id: UUID | None = None,
    note: str | None = None,
    created_by: UUID | None = None,
) -> UUID | None:
    """Adds the address; None when it was already on the list (that entry is kept: the
    first reason stays, so an Admin's note can't turn an unsubscribe into something else)."""
    result = await session.execute(
        text(
            "INSERT INTO hd_suppression (workspace_id, email_hash, email, reason, source, "
            "campaign_id, note, created_by) VALUES (:ws, :hash, :email, :reason, :source, "
            ":campaign, :note, :by) ON CONFLICT (workspace_id, email_hash) DO NOTHING "
            "RETURNING id"
        ),
        {
            "ws": workspace_id, "hash": email_hash, "email": email, "reason": reason,
            "source": source, "campaign": campaign_id, "note": note, "by": created_by,
        },
    )  # fmt: skip
    found = result.scalar_one_or_none()
    return UUID(str(found)) if found is not None else None


async def delete_suppression(session: AsyncSession, suppression_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_suppression WHERE id = :id RETURNING id"), {"id": suppression_id}
    )
    return result.first() is not None


async def suppressions_for_hashes(session: AsyncSession, hashes: Sequence[bytes]) -> Sequence[Row]:
    result = await session.execute(
        text(_SUPPRESSION + "WHERE x.email_hash = ANY(:hashes) ORDER BY x.created_at"),
        {"hashes": list(hashes)},
    )
    return result.mappings().all()


async def forget_addresses(
    session: AsyncSession, workspace_id: UUID, hashes: Sequence[bytes]
) -> int:
    """Privacy erase: each address stays on the list by its hash only, so the person is
    never mailed again. Addresses not on the list are added as `erased`."""
    changed = 0
    for h in hashes:
        result = await session.execute(
            text("UPDATE hd_suppression SET email = NULL, note = NULL WHERE email_hash = :h"),
            {"h": h},
        )
        if not result.rowcount:  # type: ignore[attr-defined]
            await suppress(
                session, workspace_id, email_hash=h, email=None, reason="erased", source="erase"
            )
        changed += 1
    return changed


# Campaigns

_CAMPAIGN = (
    "SELECT k.id, k.name, k.segment_id, coalesce(g.name, k.segment_name) AS segment_name, "
    "k.subject, k.body, k.from_address_id, coalesce(a.address, k.from_address) AS from_address, "
    "coalesce(k.from_name, a.name) AS from_name, k.track_opens, k.state, k.scheduled_at, "
    "k.started_at, k.finished_at, k.recipient_count, k.last_error, k.created_by, "
    "m.name AS created_by_name, k.created_at, k.updated_at "
    "FROM hd_campaign k LEFT JOIN hd_segment g ON g.id = k.segment_id "
    "LEFT JOIN hd_email_address a ON a.id = k.from_address_id "
    "LEFT JOIN member m ON m.id = k.created_by "
)


async def count_campaigns(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_campaign"))
    return int(result.scalar_one())


async def insert_campaign(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    fields: Mapping[str, Any],
    created_by: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_campaign (workspace_id, name, segment_id, subject, body, "
            "from_address_id, track_opens, created_by) VALUES (:ws, :name, :segment_id, "
            ":subject, :body, :from_address_id, :track_opens, :by) RETURNING id"
        ),
        {"ws": workspace_id, "by": created_by, **fields},
    )
    return UUID(str(result.scalar_one()))


async def get_campaign(
    session: AsyncSession, campaign_id: UUID, *, lock: bool = False
) -> Row | None:
    sql = _CAMPAIGN + "WHERE k.id = :id" + (" FOR UPDATE OF k" if lock else "")
    result = await session.execute(text(sql), {"id": campaign_id})
    return result.mappings().first()


async def campaign_page(
    session: AsyncSession,
    *,
    before: tuple[datetime, UUID] | None,
    limit: int,
    state: str | None,
) -> Sequence[Row]:
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            _CAMPAIGN + "WHERE (CAST(:state AS text) IS NULL OR k.state = :state) "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (k.created_at, k.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY k.created_at DESC, k.id DESC LIMIT :limit"
        ),
        {"state": state, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def update_campaign(
    session: AsyncSession, campaign_id: UUID, fields: Mapping[str, Any]
) -> None:
    """`fields`: some of name, segment_id, subject, body, from_address_id, track_opens
    (fixed names, checked by the service)."""
    allowed = {"name", "segment_id", "subject", "body", "from_address_id", "track_opens"}
    names = [k for k in fields if k in allowed]
    if not names:
        return
    sets = ", ".join(f"{k} = :{k}" for k in names)
    await session.execute(
        text(f"UPDATE hd_campaign SET {sets}, updated_at = now() WHERE id = :id"),  # noqa: S608
        {"id": campaign_id, **{k: fields[k] for k in names}},
    )


async def delete_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    await session.execute(text("DELETE FROM hd_campaign WHERE id = :id"), {"id": campaign_id})


async def set_state(
    session: AsyncSession,
    campaign_id: UUID,
    state: str,
    *,
    scheduled_at: datetime | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign SET state = :state, scheduled_at = :at, last_error = :error, "
            "finished_at = CASE WHEN :state IN ('sent', 'cancelled', 'failed') THEN now() "
            "ELSE NULL END, updated_at = now() WHERE id = :id"
        ),
        {"id": campaign_id, "state": state, "at": scheduled_at, "error": error},
    )


async def mark_started(
    session: AsyncSession,
    campaign_id: UUID,
    *,
    segment_name: str,
    from_address: str,
    from_name: str,
    recipients: int,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign SET state = 'sending', started_at = now(), "
            "segment_name = :segment, from_address = :address, from_name = :name, "
            "recipient_count = :n, last_error = NULL, updated_at = now() WHERE id = :id"
        ),
        {
            "id": campaign_id,
            "segment": segment_name,
            "address": from_address,
            "name": from_name,
            "n": recipients,
        },
    )


async def due_to_start(session: AsyncSession, now: datetime) -> list[UUID]:
    result = await session.execute(
        text(
            "SELECT id FROM hd_campaign WHERE state = 'scheduled' AND scheduled_at <= :now "
            "ORDER BY scheduled_at, id"
        ),
        {"now": now},
    )
    return [UUID(str(r[0])) for r in result.all()]


async def sending(session: AsyncSession) -> list[UUID]:
    result = await session.execute(
        text("SELECT id FROM hd_campaign WHERE state = 'sending' ORDER BY started_at, id")
    )
    return [UUID(str(r[0])) for r in result.all()]


_STATS = """
    SELECT campaign_id,
        count(*) AS recipients,
        count(*) FILTER (WHERE status = 'queued') AS queued,
        count(*) FILTER (WHERE status = 'sending') AS sending,
        count(*) FILTER (WHERE status IN ('sent', 'bounced')) AS sent,
        count(*) FILTER (WHERE status = 'failed') AS failed,
        count(*) FILTER (WHERE status = 'bounced') AS bounced,
        count(*) FILTER (WHERE status = 'skipped') AS skipped,
        count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
        count(*) FILTER (WHERE first_opened_at IS NOT NULL) AS opened,
        coalesce(sum(open_count), 0) AS opens,
        count(*) FILTER (WHERE unsubscribed_at IS NOT NULL) AS unsubscribed,
        count(*) FILTER (WHERE complained_at IS NOT NULL) AS complained
    FROM hd_campaign_recipient WHERE campaign_id = ANY(:ids) GROUP BY campaign_id
"""


async def stats(session: AsyncSession, campaign_ids: Sequence[UUID]) -> dict[UUID, Row]:
    if not campaign_ids:
        return {}
    result = await session.execute(text(_STATS), {"ids": list(campaign_ids)})
    return {r["campaign_id"]: r for r in result.mappings().all()}


# Recipients: who, and the copy at start


async def audience(session: AsyncSession, workspace_id: UUID, filters: Mapping[str, Any]) -> Row:
    """How many of a segment's contacts a campaign would go to, and why the rest won't."""
    sql = (
        "SELECT count(*) AS in_segment, "  # noqa: S608 (constants)
        "count(*) FILTER (WHERE NOT mailable) AS not_mailable, "
        "count(*) FILTER (WHERE mailable AND suppressed) AS suppressed, "
        "count(*) FILTER (WHERE mailable AND NOT suppressed) AS recipients "
        f"FROM (SELECT {_MAILABLE} AS mailable, {_SUPPRESSED} AS suppressed "
        f"{contact_repo.segment_from()}) a"
    )
    result = await session.execute(text(sql), _audience_params(workspace_id, filters))
    return result.mappings().one()


async def first_in_segment(
    session: AsyncSession, workspace_id: UUID, filters: Mapping[str, Any]
) -> UUID | None:
    """A sample contact for the preview: the first the campaign would go to."""
    sql = (
        f"SELECT c.id {contact_repo.segment_from()} AND {_MAILABLE} "
        f"AND NOT {_SUPPRESSED} ORDER BY lower(c.name), c.id LIMIT 1"
    )
    result = await session.execute(text(sql), _audience_params(workspace_id, filters))
    found = result.scalar_one_or_none()
    return UUID(str(found)) if found is not None else None


async def copy_recipients(
    session: AsyncSession, workspace_id: UUID, campaign_id: UUID, filters: Mapping[str, Any]
) -> int:
    """The segment's mailable, unsuppressed contacts become the campaign's recipients."""
    sql = (
        "INSERT INTO hd_campaign_recipient (workspace_id, campaign_id, contact_id, email, "
        f"name) SELECT c.workspace_id, :campaign, c.id, c.email, c.name "
        f"{contact_repo.segment_from()} AND {_MAILABLE} AND NOT {_SUPPRESSED} "
        "ON CONFLICT DO NOTHING"
    )
    params = _audience_params(workspace_id, filters) | {"campaign": campaign_id}
    result = await session.execute(text(sql), params)
    return int(result.rowcount)  # type: ignore[attr-defined]


async def contact_for_preview(session: AsyncSession, contact_id: UUID) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, name, email, attributes FROM hd_contact "
            "WHERE id = :id AND erased_at IS NULL"
        ),
        {"id": contact_id},
    )
    return result.mappings().first()


# The send queue


async def sent_since(session: AsyncSession, since: datetime) -> int:
    """Campaign emails this workspace sent (or is sending) since then."""
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_campaign_recipient WHERE (sent_at >= :since) "
            "OR status = 'sending'"
        ),
        {"since": since},
    )
    return int(result.scalar_one())


async def queued_total(session: AsyncSession) -> int:
    """Recipients still waiting in campaigns that are sending or paused (they will go)."""
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_campaign_recipient r JOIN hd_campaign k "
            "ON k.id = r.campaign_id WHERE r.status = 'queued' "
            "AND k.state IN ('sending', 'paused')"
        )
    )
    return int(result.scalar_one())


async def expire_claims(session: AsyncSession, now: datetime, error: str) -> int:
    """A recipient claimed by a worker that never reported back: failed, never resent
    (the email may have gone out)."""
    result = await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'failed', failed_at = now(), "
            "error = :error, claimed_until = NULL "
            "WHERE status = 'sending' AND claimed_until < :now"
        ),
        {"now": now, "error": error},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


_CLAIM_SUPPRESSED = (
    f"(EXISTS (SELECT 1 FROM hd_suppression x WHERE x.email_hash = {_hash('r.email')}) "  # noqa: S608
    "OR EXISTS (SELECT 1 FROM hd_contact_alias a JOIN hd_suppression x "
    f"ON x.email_hash = {_hash('a.email')} WHERE a.contact_id = r.contact_id))"
)


async def claim(
    session: AsyncSession, workspace_id: UUID, campaign_id: UUID, limit: int, now: datetime
) -> Sequence[Row]:
    """Up to `limit` queued recipients that are due, locked (another worker skips them),
    with what the email needs and whether they may still be mailed."""
    result = await session.execute(
        text(
            "SELECT r.id, r.contact_id, r.email, r.name, r.attempts, "  # noqa: S608
            "coalesce(c.attributes, '{}'::jsonb) AS attributes, "
            f"(c.id IS NULL OR c.erased_at IS NOT NULL) AS gone, {_CLAIM_SUPPRESSED} "
            "AS suppressed FROM hd_campaign_recipient r "
            "LEFT JOIN hd_contact c ON c.id = r.contact_id "
            "WHERE r.campaign_id = :campaign AND r.status = 'queued' "
            "AND r.next_attempt_at <= :now ORDER BY r.next_attempt_at, r.id "
            "LIMIT :limit FOR UPDATE OF r SKIP LOCKED"
        ),
        {"ws": str(workspace_id), "campaign": campaign_id, "limit": limit, "now": now},
    )
    return result.mappings().all()


async def skip(session: AsyncSession, recipient_id: UUID, reason: str) -> None:
    await session.execute(
        text("UPDATE hd_campaign_recipient SET status = 'skipped', error = :reason WHERE id = :id"),
        {"id": recipient_id, "reason": reason},
    )


async def mark_sending(
    session: AsyncSession,
    recipient_id: UUID,
    *,
    until: datetime,
    unsubscribe_hash: bytes,
    open_hash: bytes | None,
    header_message_id: str,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'sending', attempts = attempts + 1, "
            "claimed_until = :until, unsubscribe_hash = :unsub, open_hash = :open, "
            "header_message_id = :mid WHERE id = :id"
        ),
        {
            "id": recipient_id,
            "until": until,
            "unsub": unsubscribe_hash,
            "open": open_hash,
            "mid": header_message_id,
        },
    )


async def mark_sent(session: AsyncSession, recipient_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'sent', sent_at = now(), error = NULL, "
            "claimed_until = NULL WHERE id = :id AND status = 'sending'"
        ),
        {"id": recipient_id},
    )


async def mark_retry(session: AsyncSession, recipient_id: UUID, error: str, at: datetime) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'queued', error = :error, "
            "next_attempt_at = :at, claimed_until = NULL WHERE id = :id AND status = 'sending'"
        ),
        {"id": recipient_id, "error": error[:300], "at": at},
    )


async def mark_failed(session: AsyncSession, recipient_id: UUID, error: str) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'failed', failed_at = now(), "
            "error = :error, claimed_until = NULL WHERE id = :id AND status = 'sending'"
        ),
        {"id": recipient_id, "error": error[:300]},
    )


async def left_to_send(session: AsyncSession, campaign_id: UUID) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_campaign_recipient WHERE campaign_id = :id "
            "AND status IN ('queued', 'sending')"
        ),
        {"id": campaign_id},
    )
    return int(result.scalar_one())


async def cancel_queued(session: AsyncSession, campaign_id: UUID) -> int:
    result = await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'cancelled' "
            "WHERE campaign_id = :id AND status = 'queued'"
        ),
        {"id": campaign_id},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


# Results coming back: bounces, unsubscribes, opens


async def by_message_ids(session: AsyncSession, message_ids: Sequence[str]) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, campaign_id, email, status FROM hd_campaign_recipient "
            "WHERE header_message_id = ANY(:ids) LIMIT 1"
        ),
        {"ids": list(message_ids)},
    )
    return result.mappings().first()


async def mark_bounced(session: AsyncSession, recipient_id: UUID) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET status = 'bounced', "
            "bounced_at = coalesce(bounced_at, now()) "
            "WHERE id = :id AND status IN ('sent', 'sending', 'bounced') RETURNING id"
        ),
        {"id": recipient_id},
    )
    return result.first() is not None


async def mark_complained(session: AsyncSession, recipient_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET complained_at = coalesce(complained_at, now()) "
            "WHERE id = :id"
        ),
        {"id": recipient_id},
    )


async def by_unsubscribe(session: AsyncSession, token_hash: bytes) -> Row | None:
    result = await session.execute(
        text(
            "SELECT id, campaign_id, email, unsubscribed_at FROM hd_campaign_recipient "
            "WHERE unsubscribe_hash = :h"
        ),
        {"h": token_hash},
    )
    return result.mappings().first()


async def mark_unsubscribed(session: AsyncSession, recipient_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET "
            "unsubscribed_at = coalesce(unsubscribed_at, now()) WHERE id = :id"
        ),
        {"id": recipient_id},
    )


async def record_open(session: AsyncSession, token_hash: bytes) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET first_opened_at = coalesce(first_opened_at, "
            "now()), open_count = open_count + 1 WHERE open_hash = :h "
            "AND status IN ('sent', 'bounced') RETURNING id"
        ),
        {"h": token_hash},
    )
    return result.first() is not None


# The results pages


_RECIPIENT = (
    "SELECT r.id, r.contact_id, r.email, r.name, r.status, r.attempts, r.error, "
    "r.created_at AS queued_at, r.sent_at, r.failed_at, r.bounced_at, r.first_opened_at, "
    "r.open_count, r.unsubscribed_at, r.complained_at FROM hd_campaign_recipient r "
)
_RESULT_FILTERS = {
    "opened": "r.first_opened_at IS NOT NULL",
    "unsubscribed": "r.unsubscribed_at IS NOT NULL",
    "complained": "r.complained_at IS NOT NULL",
}


def _filter_sql(status: str | None) -> str:
    """Fixed strings only: a status is a bound parameter; the rest are constants."""
    if status is None:
        return "true"
    return _RESULT_FILTERS.get(status, "r.status = :status")


async def recipient_page(
    session: AsyncSession,
    campaign_id: UUID,
    *,
    status: str | None,
    after: tuple[str, UUID] | None,
    limit: int | None,
) -> Sequence[Row]:
    """By address (an erased one sorts first, as ""), then id. `limit` None: all (CSV)."""
    key, row_id = after if after else (None, None)
    sql = (
        _RECIPIENT + f"WHERE r.campaign_id = :campaign AND {_filter_sql(status)} "
        "AND (CAST(:key AS text) IS NULL OR (coalesce(r.email, ''), r.id) > "
        "(CAST(:key AS text), CAST(:row AS uuid))) ORDER BY coalesce(r.email, ''), r.id"
        + (" LIMIT :limit" if limit is not None else "")
    )
    params: dict[str, Any] = {"campaign": campaign_id, "status": status, "key": key,
                              "row": row_id, "limit": limit}  # fmt: skip
    result = await session.execute(text(sql), params)
    return result.mappings().all()


# Privacy


async def recipients_for_export(
    session: AsyncSession, contact_id: UUID, emails: Sequence[str]
) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT k.name AS campaign, k.subject, r.email, r.status, r.created_at AS queued_at, "
            "r.sent_at, r.bounced_at, r.first_opened_at, r.open_count, r.unsubscribed_at, "
            "r.complained_at FROM hd_campaign_recipient r "
            "JOIN hd_campaign k ON k.id = r.campaign_id "
            "WHERE r.contact_id = :c OR r.email = ANY(:emails) ORDER BY r.created_at"
        ),
        {"c": contact_id, "emails": list(emails)},
    )
    return result.mappings().all()


async def erase_recipients(
    session: AsyncSession, contact_id: UUID, emails: Sequence[str], name: str
) -> int:
    """Their rows stay (the campaign's totals don't change) without the address, name or
    link tokens; a row not sent yet is skipped."""
    result = await session.execute(
        text(
            "UPDATE hd_campaign_recipient SET email = NULL, name = :name, "
            "unsubscribe_hash = NULL, open_hash = NULL, header_message_id = NULL, "
            "error = NULL, status = CASE WHEN status = 'queued' THEN 'skipped' ELSE status END "
            "WHERE contact_id = :c OR email = ANY(:emails)"
        ),
        {"c": contact_id, "emails": list(emails), "name": name},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
