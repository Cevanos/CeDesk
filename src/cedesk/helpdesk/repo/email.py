"""SQL for support email addresses and the outgoing email queue. Always run inside a
`workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_ADDRESS = (
    "SELECT id, address, name, signature, is_default, created_at, updated_at, "
    "forwarding_verified_at, verify_sent_at, dkim_selector, dns_checked_at, dns_result, "
    "(SELECT o.status FROM hd_email_out o WHERE o.id = a.verify_email_id) "
    "    AS verify_email_status, "
    "(SELECT o.last_error FROM hd_email_out o WHERE o.id = a.verify_email_id) "
    "    AS verify_email_error "
    "FROM hd_email_address a"
)


# Support addresses


async def list_addresses(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_ADDRESS + " ORDER BY is_default DESC, address"))
    return result.mappings().all()


async def get_address(session: AsyncSession, address_id: UUID) -> Row | None:
    result = await session.execute(text(_ADDRESS + " WHERE id = :id"), {"id": address_id})
    return result.mappings().first()


async def count_addresses(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_email_address"))
    return int(result.scalar_one())


async def lock_addresses(session: AsyncSession) -> None:
    """Locks the workspace's addresses, so two changes to the default take turns."""
    await session.execute(text("SELECT id FROM hd_email_address ORDER BY id FOR UPDATE"))


async def insert_address(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    address: str,
    name: str,
    signature: str,
    is_default: bool,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_email_address (workspace_id, address, name, signature, is_default) "
            "VALUES (:ws, :address, :name, :signature, :is_default) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "address": address,
            "name": name,
            "signature": signature,
            "is_default": is_default,
        },
    )
    return UUID(str(result.scalar_one()))


async def update_address(
    session: AsyncSession, address_id: UUID, *, name: str | None, signature: str | None
) -> None:
    await session.execute(
        text(
            "UPDATE hd_email_address SET name = coalesce(:name, name), "
            "signature = coalesce(:signature, signature), updated_at = now() WHERE id = :id"
        ),
        {"id": address_id, "name": name, "signature": signature},
    )


async def set_dkim_selector(session: AsyncSession, address_id: UUID, selector: str | None) -> None:
    await session.execute(
        text("UPDATE hd_email_address SET dkim_selector = :sel, updated_at = now() WHERE id = :id"),
        {"id": address_id, "sel": selector},
    )


async def set_verify_code(
    session: AsyncSession, address_id: UUID, code_hash: str, email_id: UUID
) -> None:
    """A new forwarding check: its code (and its queued email) replace the last one's."""
    await session.execute(
        text(
            "UPDATE hd_email_address SET verify_code_hash = :h, verify_sent_at = now(), "
            "verify_email_id = :email WHERE id = :id"
        ),
        {"id": address_id, "h": code_hash, "email": email_id},
    )


async def verify_by_code(session: AsyncSession, code_hash: str, since: datetime) -> Row | None:
    """Marks the address whose check carried this code (sent after `since`) as verified;
    the code is used up."""
    result = await session.execute(
        text(
            "UPDATE hd_email_address SET forwarding_verified_at = now(), verify_code_hash = NULL "
            "WHERE verify_code_hash = :h AND verify_sent_at > :since RETURNING id, address"
        ),
        {"h": code_hash, "since": since},
    )
    return result.mappings().first()


async def save_dns(session: AsyncSession, address_id: UUID, found: Mapping[str, Any]) -> None:
    await session.execute(
        text(
            "UPDATE hd_email_address SET dns_checked_at = now(), "
            "dns_result = CAST(:found AS jsonb) WHERE id = :id"
        ),
        {"id": address_id, "found": json.dumps(found)},
    )


async def clear_default(session: AsyncSession) -> None:
    await session.execute(
        text("UPDATE hd_email_address SET is_default = false, updated_at = now() WHERE is_default")
    )


async def make_default(session: AsyncSession, address_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_email_address SET is_default = true, updated_at = now() WHERE id = :id"),
        {"id": address_id},
    )


async def oldest_address(session: AsyncSession) -> UUID | None:
    result = await session.execute(
        text("SELECT id FROM hd_email_address ORDER BY created_at, id LIMIT 1")
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def delete_address(session: AsyncSession, address_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_email_address WHERE id = :id RETURNING id"), {"id": address_id}
    )
    return result.first() is not None


# What a reply needs from the ticket's emails


async def customer_emails(session: AsyncSession, ticket_id: UUID) -> tuple[Any, Any, Any]:
    """The `email` of the customer's first message, of the first one with a CC, and of the
    latest one (each None when there is none)."""
    result = await session.execute(
        text(
            "SELECT "
            "(SELECT email FROM hd_message WHERE ticket_id = :t AND kind = 'customer' "
            " AND email IS NOT NULL ORDER BY created_at, id LIMIT 1), "
            "(SELECT email FROM hd_message WHERE ticket_id = :t AND kind = 'customer' "
            " AND jsonb_array_length(CASE jsonb_typeof(email->'cc') WHEN 'array' "
            " THEN email->'cc' ELSE '[]' END) > 0 ORDER BY created_at, id LIMIT 1), "
            "(SELECT email FROM hd_message WHERE ticket_id = :t AND kind = 'customer' "
            " AND email IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1)"
        ),
        {"t": ticket_id},
    )
    row = result.one()
    return row[0], row[1], row[2]


async def set_message_email(
    session: AsyncSession, message_id: UUID, email: Mapping[str, Any]
) -> None:
    await session.execute(
        text("UPDATE hd_message SET email = CAST(:email AS jsonb) WHERE id = :id"),
        {"id": message_id, "email": json.dumps(dict(email))},
    )


# The queue


async def enqueue(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    ticket_id: UUID | None,
    message_id: UUID | None,
    from_address: str,
    from_name: str,
    to: Sequence[str],
    cc: Sequence[str],
    subject: str,
    body: str,
    header_message_id: str | None,
    in_reply_to: str | None,
    references: Sequence[str],
    sensitive: bool = False,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_email_out (workspace_id, ticket_id, message_id, from_address, "
            "from_name, to_addresses, cc_addresses, subject, body_text, header_message_id, "
            "in_reply_to, references_ids, sensitive) VALUES (:ws, :ticket, :message, "
            ":from_address, :from_name, :to, :cc, :subject, :body, :mid, :in_reply_to, :refs, "
            ":sensitive) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "ticket": ticket_id,
            "message": message_id,
            "from_address": from_address,
            "from_name": from_name,
            "to": list(to),
            "cc": list(cc),
            "subject": subject,
            "body": body,
            "mid": header_message_id,
            "in_reply_to": in_reply_to,
            "refs": list(references),
            "sensitive": sensitive,
        },
    )
    return UUID(str(result.scalar_one()))


async def claim_due(session: AsyncSession, limit: int, lease_seconds: int) -> Sequence[Row]:
    """Takes up to `limit` emails that are due, counts the attempt, and pushes their next
    try `lease_seconds` ahead, so another worker (or this one after a crash) only picks them
    up again if this attempt never reports back."""
    result = await session.execute(
        text(
            "UPDATE hd_email_out o SET attempts = attempts + 1, "
            "next_attempt_at = now() + make_interval(secs => :lease) "
            "FROM (SELECT id FROM hd_email_out WHERE status = 'pending' "
            "      AND next_attempt_at <= now() ORDER BY next_attempt_at, id "
            "      LIMIT :limit FOR UPDATE SKIP LOCKED) due "
            "WHERE o.id = due.id RETURNING o.*"
        ),
        {"limit": limit, "lease": lease_seconds},
    )
    return result.mappings().all()


async def mark_sent(session: AsyncSession, email_id: UUID) -> None:
    await session.execute(
        text(
            # A sensitive email (a reset link) has its text wiped once it is sent.
            "UPDATE hd_email_out SET status = 'sent', sent_at = now(), last_error = NULL, "
            "body_text = CASE WHEN sensitive THEN '(removed after sending)' ELSE body_text END "
            "WHERE id = :id"
        ),
        {"id": email_id},
    )


async def mark_retry(
    session: AsyncSession, email_id: UUID, error: str, next_attempt_at: datetime
) -> None:
    await session.execute(
        text("UPDATE hd_email_out SET last_error = :error, next_attempt_at = :at WHERE id = :id"),
        {"id": email_id, "error": error[:500], "at": next_attempt_at},
    )


async def mark_failed(session: AsyncSession, email_id: UUID, error: str) -> None:
    await session.execute(
        text(
            "UPDATE hd_email_out SET status = 'failed', last_error = :error, "
            "body_text = CASE WHEN sensitive THEN '(removed after sending)' ELSE body_text END "
            "WHERE id = :id"
        ),
        {"id": email_id, "error": error[:500]},
    )


async def has_due(session: AsyncSession) -> bool:
    result = await session.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM hd_email_out WHERE status = 'pending' "
            "AND next_attempt_at <= now())"
        )
    )
    return bool(result.scalar_one())


# Email in


async def workspace_for_addresses(
    session: AsyncSession, addresses: Sequence[str]
) -> tuple[UUID, str] | None:
    """The first of `addresses` that is a support address, and its workspace. Reads the
    global directory, so it works before any workspace is known."""
    if not addresses:
        return None
    result = await session.execute(
        text(
            "SELECT workspace_id, address FROM email_address_directory "
            "WHERE address = ANY(:addresses) ORDER BY array_position(:addresses, address) LIMIT 1"
        ),
        {"addresses": list(addresses)},
    )
    row = result.first()
    return None if row is None else (UUID(str(row[0])), str(row[1]))


async def is_support_address(session: AsyncSession, address: str) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM email_address_directory WHERE address = :a"), {"a": address}
    )
    return result.first() is not None


async def email_seen(session: AsyncSession, workspace_id: UUID, message_id: str) -> bool:
    """An email with this Message-ID is already on some ticket (in or out)."""
    result = await session.execute(
        text(
            "SELECT 1 FROM hd_message WHERE workspace_id = :ws AND email IS NOT NULL "
            "AND email->>'message_id' = :mid LIMIT 1"
        ),
        {"ws": workspace_id, "mid": message_id},
    )
    return result.first() is not None


async def ticket_for_email_ids(
    session: AsyncSession, workspace_id: UUID, ids: Sequence[str]
) -> UUID | None:
    """The ticket of the first message (in `ids` order) whose Message-ID is listed."""
    if not ids:
        return None
    result = await session.execute(
        text(
            "SELECT ticket_id FROM hd_message WHERE workspace_id = :ws AND email IS NOT NULL "
            "AND email->>'message_id' = ANY(:ids) "
            "ORDER BY array_position(:ids, email->>'message_id'), created_at LIMIT 1"
        ),
        {"ws": workspace_id, "ids": list(ids)},
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def ticket_for_contact_number(
    session: AsyncSession, number: int, contact_email: str
) -> UUID | None:
    """Ticket T-<number>, only if <contact_email> is its customer."""
    result = await session.execute(
        text(
            "SELECT t.id FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id "
            "WHERE t.number = :n AND c.email = :e"
        ),
        {"n": number, "e": contact_email},
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def merged_into(session: AsyncSession, ticket_id: UUID) -> UUID | None:
    result = await session.execute(
        text("SELECT merged_into_id FROM hd_ticket WHERE id = :id"), {"id": ticket_id}
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


# Failed emails (Admins)


async def failed(
    session: AsyncSession, *, before: tuple[datetime, UUID] | None, limit: int
) -> Sequence[Row]:
    """Newest first. An account email (password reset) has no ticket, and its text was
    removed when it failed."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            "SELECT e.id, e.ticket_id, t.number AS ticket_number, e.message_id, "
            "e.to_addresses, e.subject, e.attempts, e.last_error, e.sensitive, e.created_at "
            "FROM hd_email_out e LEFT JOIN hd_ticket t ON t.id = e.ticket_id "
            "WHERE e.status = 'failed' "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (e.created_at, e.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY e.created_at DESC, e.id DESC LIMIT :limit"
        ),
        {"at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def retry_failed(session: AsyncSession, email_id: UUID) -> Row | None:
    """Back in the queue, due now, tries from zero. Not an email whose text was removed."""
    result = await session.execute(
        text(
            "UPDATE hd_email_out SET status = 'pending', attempts = 0, "
            "next_attempt_at = now(), last_error = NULL "
            "WHERE id = :id AND status = 'failed' AND NOT sensitive "
            "RETURNING id, ticket_id, subject"
        ),
        {"id": email_id},
    )
    return result.mappings().first()
