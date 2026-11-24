"""Privacy export and erase for one customer (md S11): the SQL.

Erasing keeps every row (tickets, messages, ratings, time logs) so counts and reports
stay; only the words that could identify the customer are replaced.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

ERASED = "(erased)"
ERASED_NAME = "Erased customer"
STOPPED = "Stopped: the customer was erased."


def erased_email(contact_id: UUID) -> str:
    """Unique per contact (the address is unique per workspace) and never deliverable."""
    return f"erased-{contact_id}@erased.invalid"


async def _changed(session: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    result = await session.execute(text(sql), params)
    return int(cast("CursorResult[Any]", result).rowcount)


async def contact(
    session: AsyncSession, contact_id: UUID, *, lock: bool = False
) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT id, email, name, vip, phone, organization, customer_since, created_at, "
            "erased_at, attributes "
            "FROM hd_contact WHERE id = :id" + (" FOR UPDATE" if lock else "")
        ),
        {"id": contact_id},
    )
    return result.mappings().first()


async def contact_by_email(session: AsyncSession, email: str) -> RowMapping | None:
    """By the contact's own address, or another one merged into it (plan 2.4)."""
    result = await session.execute(
        text(
            "SELECT id FROM hd_contact WHERE email = :email "
            "UNION ALL SELECT contact_id AS id FROM hd_contact_alias WHERE email = :email LIMIT 1"
        ),
        {"email": email},
    )
    return result.mappings().first()


async def workspace_name(session: AsyncSession) -> str:
    result = await session.execute(text("SELECT name FROM workspace"))
    return str(result.scalar_one())


async def ticket_ids(session: AsyncSession, contact_id: UUID) -> list[UUID]:
    result = await session.execute(
        text("SELECT id FROM hd_ticket WHERE contact_id = :c ORDER BY number"), {"c": contact_id}
    )
    return [r[0] for r in result.all()]


# ---- export -------------------------------------------------------------------------


async def tickets_of(session: AsyncSession, contact_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT t.id, t.number, t.subject, t.status, t.priority, t.channel, t.fields, "
            "t.tags, t.created_at, t.resolved_at, ty.name AS type_name "
            "FROM hd_ticket t LEFT JOIN hd_ticket_type ty ON ty.id = t.type_id "
            "WHERE t.contact_id = :c ORDER BY t.number"
        ),
        {"c": contact_id},
    )
    return result.mappings().all()


async def messages_of(session: AsyncSession, ids: list[UUID]) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT m.id, m.ticket_id, m.kind, m.author_name, m.body, m.created_at, "
            "mem.name AS member_name "
            "FROM hd_message m LEFT JOIN member mem ON mem.id = m.author_member_id "
            "WHERE m.ticket_id = ANY(:ids) ORDER BY m.created_at, m.id"
        ),
        {"ids": ids},
    )
    return result.mappings().all()


async def files_of(session: AsyncSession, ids: list[UUID]) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT ticket_id, message_id, name, size, mime, scan_status, created_at "
            "FROM hd_attachment WHERE ticket_id = ANY(:ids) ORDER BY created_at, id"
        ),
        {"ids": ids},
    )
    return result.mappings().all()


async def ratings_of(session: AsyncSession, contact_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT c.ticket_id, c.score, c.comment, c.source, c.rated_at "
            "FROM hd_csat c JOIN hd_ticket t ON t.id = c.ticket_id "
            "WHERE t.contact_id = :c OR c.contact_id = :c ORDER BY c.rated_at"
        ),
        {"c": contact_id},
    )
    return result.mappings().all()


async def receipts_of(session: AsyncSession, ids: Sequence[UUID]) -> Sequence[RowMapping]:
    """The tickets' resolution receipts (differentiator plan #5)."""
    result = await session.execute(
        text(
            "SELECT ticket_id, request, action_taken, result, verified, verified_at, "
            "customer_notified, pending_item, created_at "
            "FROM hd_resolution WHERE ticket_id = ANY(:ids)"
        ),
        {"ids": list(ids)},
    )
    return result.mappings().all()


# ---- requests -----------------------------------------------------------------------


async def insert_request(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    request_id: UUID,
    kind: str,
    contact_id: UUID,
    requested_by: UUID | None,
    object_key: str | None = None,
    size: int | None = None,
    expires_at: datetime | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_privacy_request (id, workspace_id, kind, contact_id, requested_by, "
            "object_key, size, expires_at, counts) VALUES (:id, :ws, :kind, :contact, :by, "
            ":key, :size, :expires, CAST(:counts AS jsonb))"
        ),
        {
            "id": request_id,
            "ws": workspace_id,
            "kind": kind,
            "contact": contact_id,
            "by": requested_by,
            "key": object_key,
            "size": size,
            "expires": expires_at,
            "counts": json.dumps(counts or {}),
        },
    )


_REQUEST = (
    "SELECT r.id, r.kind, r.contact_id, r.requested_by, mem.name AS requested_by_name, "
    "r.requested_at, r.object_key, r.size, r.expires_at, r.counts, c.erased_at "
    "FROM hd_privacy_request r JOIN hd_contact c ON c.id = r.contact_id "
    "LEFT JOIN member mem ON mem.id = r.requested_by "
)


async def request(session: AsyncSession, request_id: UUID) -> RowMapping | None:
    result = await session.execute(text(_REQUEST + "WHERE r.id = :id"), {"id": request_id})
    return result.mappings().first()


async def requests(session: AsyncSession, *, limit: int) -> Sequence[RowMapping]:
    result = await session.execute(
        text(_REQUEST + "ORDER BY r.requested_at DESC, r.id LIMIT :limit"), {"limit": limit}
    )
    return result.mappings().all()


async def export_keys(session: AsyncSession, contact_id: UUID) -> list[str]:
    """The stored export files of this customer, to delete when they're erased."""
    result = await session.execute(
        text(
            "SELECT object_key FROM hd_privacy_request "
            "WHERE contact_id = :c AND object_key IS NOT NULL"
        ),
        {"c": contact_id},
    )
    return [str(r[0]) for r in result.all()]


async def forget_exports(session: AsyncSession, contact_id: UUID) -> int:
    return await _changed(
        session,
        "UPDATE hd_privacy_request SET object_key = NULL, size = NULL "
        "WHERE contact_id = :c AND object_key IS NOT NULL",
        {"c": contact_id},
    )


async def expired_exports(session: AsyncSession, now: datetime) -> Sequence[RowMapping]:
    result = await session.execute(
        text(
            "SELECT id, object_key FROM hd_privacy_request "
            "WHERE object_key IS NOT NULL AND expires_at <= :now"
        ),
        {"now": now},
    )
    return result.mappings().all()


async def forget_export(session: AsyncSession, request_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_privacy_request SET object_key = NULL, size = NULL WHERE id = :id"),
        {"id": request_id},
    )


# ---- erase --------------------------------------------------------------------------


async def file_keys(session: AsyncSession, ids: list[UUID]) -> list[str]:
    result = await session.execute(
        text(
            "SELECT object_key FROM hd_attachment "
            "WHERE ticket_id = ANY(:ids) AND scan_status <> 'erased'"
        ),
        {"ids": ids},
    )
    return [str(r[0]) for r in result.all()]


async def merged_copies(session: AsyncSession, contact_id: UUID) -> list[UUID]:
    """The copies a merge made of this customer's messages on another ticket (X1 copies
    the messages, and moves their files, into the ticket merged into, which may be another
    customer's). Found by being the same message: same kind, time and words, so this runs
    before the customer's own messages are erased."""
    result = await session.execute(
        text(
            "SELECT DISTINCT c.id FROM hd_ticket src "
            "JOIN hd_message s ON s.ticket_id = src.id AND s.kind <> 'system' "
            "JOIN hd_message c ON c.ticket_id = src.merged_into_id AND c.kind = s.kind "
            "AND c.created_at = s.created_at AND c.body = s.body "
            "WHERE src.contact_id = :c AND src.merged_into_id IS NOT NULL"
        ),
        {"c": contact_id},
    )
    return [r[0] for r in result.all()]


async def message_file_keys(session: AsyncSession, message_ids: list[UUID]) -> list[str]:
    result = await session.execute(
        text(
            "SELECT object_key FROM hd_attachment "
            "WHERE message_id = ANY(:ids) AND scan_status <> 'erased'"
        ),
        {"ids": message_ids},
    )
    return [str(r[0]) for r in result.all()]


async def erase_messages(session: AsyncSession, message_ids: list[UUID]) -> dict[str, int]:
    """Single messages on someone else's ticket (merged copies) and their files."""
    p: dict[str, Any] = {"ids": message_ids, "erased": ERASED, "name": ERASED_NAME}
    return {
        "merged_messages": await _changed(
            session,
            "UPDATE hd_message SET body = :erased, email = NULL, recipients = NULL, "
            "author_name = CASE WHEN kind = 'customer' THEN :name ELSE author_name END "
            "WHERE id = ANY(:ids)",
            p,
        ),
        "merged_files": await _changed(
            session,
            "UPDATE hd_attachment SET name = 'erased', scan_status = 'erased', "
            "scan_result = NULL, updated_at = now() "
            "WHERE message_id = ANY(:ids) AND scan_status <> 'erased'",
            p,
        ),
    }


async def erase_tickets(session: AsyncSession, ids: list[UUID]) -> dict[str, int]:
    """Every customer-identifying word on these tickets. The ticket's own search text
    follows the subject and fields (trigger)."""
    p: dict[str, Any] = {"ids": ids, "erased": ERASED}
    counts = {
        "tickets": await _changed(
            session,
            "UPDATE hd_ticket SET subject = :erased, fields = '{}', handover = NULL "
            "WHERE id = ANY(:ids)",
            p,
        ),
        "resolution_receipts": await _changed(
            session,
            "UPDATE hd_resolution SET request = NULL, action_taken = :erased, result = NULL, "
            "pending_item = NULL WHERE ticket_id = ANY(:ids)",
            p,
        ),
        "messages": await _changed(
            session,
            "UPDATE hd_message SET body = :erased, email = NULL, recipients = NULL, "
            "author_name = CASE WHEN kind = 'customer' THEN :name ELSE author_name END "
            "WHERE ticket_id = ANY(:ids)",
            {**p, "name": ERASED_NAME},
        ),
        "files": await _changed(
            session,
            "UPDATE hd_attachment SET name = 'erased', scan_status = 'erased', "
            "scan_result = NULL, updated_at = now() "
            "WHERE ticket_id = ANY(:ids) AND scan_status <> 'erased'",
            p,
        ),
        "emails": await _changed(
            session,
            "UPDATE hd_email_out SET subject = :erased, body_text = :erased, "
            "to_addresses = ARRAY['erased@erased.invalid'], cc_addresses = '{}', "
            "status = CASE WHEN status = 'pending' THEN 'failed' ELSE status END, "
            "last_error = CASE WHEN status = 'pending' THEN :stopped ELSE last_error END "
            "WHERE ticket_id = ANY(:ids)",
            {**p, "stopped": STOPPED},
        ),
        "notifications": await _changed(
            session, "UPDATE hd_notification SET text = :erased WHERE ticket_id = ANY(:ids)", p
        ),
        "tasks": await _changed(
            session, "UPDATE hd_task SET text = :erased WHERE ticket_id = ANY(:ids)", p
        ),
        "approvals": await _changed(
            session,
            "UPDATE hd_approval SET what = :erased, decision_note = NULL "
            "WHERE ticket_id = ANY(:ids)",
            p,
        ),
        "time_notes": await _changed(
            session,
            "UPDATE hd_time_log SET note = NULL WHERE ticket_id = ANY(:ids) AND note IS NOT NULL",
            p,
        ),
        # Side conversations (W19) about their tickets: the subject quotes the ticket's and
        # the messages talk about the customer. Who they went to stays out too; the
        # workspace's own sending address and the message ids (threading) stay.
        "side_conversations": await _changed(
            session,
            "UPDATE hd_side_conversation SET subject = :erased, "
            "to_addresses = ARRAY['erased@erased.invalid'] WHERE ticket_id = ANY(:ids)",
            p,
        ),
        "side_messages": await _changed(
            session,
            "UPDATE hd_side_message SET body = :erased, "
            "author_name = CASE WHEN direction = 'in' THEN :name ELSE author_name END, "
            "from_address = CASE WHEN direction = 'in' THEN 'erased@erased.invalid' "
            "ELSE from_address END "
            "WHERE side_id IN (SELECT id FROM hd_side_conversation WHERE ticket_id = ANY(:ids))",
            {**p, "name": ERASED_NAME},
        ),
        # What an import last read from the source (P1-10): subject, fields, cc. Without
        # it a later sync takes only status, priority, owner and tags, never the words.
        "import_snapshots": await _changed(
            session, "DELETE FROM hd_import_seen WHERE ticket_id = ANY(:ids)", p
        ),
    }
    return counts


# The customer's tickets, live and archived (md O3): their events and webhook deliveries
# name them without a foreign key.
_THEIR_TICKETS = (
    "SELECT id FROM hd_ticket WHERE contact_id = :c "
    "UNION ALL SELECT id FROM hd_ticket_archive WHERE contact_id = :c"
)


# An address in an imported list: the address itself, or "Name <address>", in any case.
_STRIP_IMPORTED_FIELDS = (
    "UPDATE hd_ticket SET fields = jsonb_strip_nulls(fields || jsonb_build_object("
    "'source_cc', CASE WHEN fields ? 'source_cc' THEN NULLIF(array_to_string(ARRAY("
    "SELECT x FROM unnest(string_to_array(fields->>'source_cc', ', ')) x "
    "WHERE NOT (lower(trim(x)) = :email OR lower(trim(x)) LIKE '%<' || :email || '>')), "
    "', '), '') END, "
    "'source_followers', CASE WHEN fields ? 'source_followers' THEN NULLIF(array_to_string("
    "ARRAY(SELECT x FROM unnest(string_to_array(fields->>'source_followers', ', ')) x "
    "WHERE NOT (lower(trim(x)) = :email OR lower(trim(x)) LIKE '%<' || :email || '>')), "
    "', '), '') END)) "
    "WHERE lower(fields->>'source_cc') LIKE :like "
    "OR lower(fields->>'source_followers') LIKE :like"
)
# The same for a message's recipients (to, cc, bcc); a list left empty goes, and so does
# `recipients` when nothing is left.
_STRIP_RECIPIENTS = (
    "UPDATE hd_message SET recipients = (SELECT jsonb_object_agg(r.key, r.kept) FROM "
    "(SELECT key, (SELECT jsonb_agg(a) FROM jsonb_array_elements_text(value) a "
    "WHERE NOT (lower(trim(a)) = :email OR lower(trim(a)) LIKE '%<' || :email || '>')) "
    "AS kept FROM jsonb_each(recipients)) r WHERE r.kept IS NOT NULL) "
    "WHERE recipients IS NOT NULL AND lower(recipients::text) LIKE :like"
)


# The same for emails CeDesk sent on other tickets (the customer was copied in), and for
# emails with no ticket at all (portal sign-in codes, md D5): the address goes; an email
# left with nobody to send to keeps a placeholder (the column needs one).
_ADDRESS_IS = "(lower(trim(a)) = :email OR lower(trim(a)) LIKE '%<' || :email || '>')"
_NAMES_THEM = (  # constants only
    f"(EXISTS (SELECT 1 FROM unnest(to_addresses) a WHERE {_ADDRESS_IS}) "  # noqa: S608
    f"OR EXISTS (SELECT 1 FROM unnest(cc_addresses) a WHERE {_ADDRESS_IS}))"
)
_STRIP_SENT = (
    "UPDATE hd_email_out SET "  # noqa: S608 (constants only)
    "to_addresses = coalesce(NULLIF(ARRAY(SELECT a FROM unnest(to_addresses) a "
    f"WHERE NOT {_ADDRESS_IS}), '{{}}'), ARRAY['erased@erased.invalid']), "
    f"cc_addresses = ARRAY(SELECT a FROM unnest(cc_addresses) a WHERE NOT {_ADDRESS_IS}), "
    "last_error = CASE WHEN lower(last_error) LIKE :like THEN :erased ELSE last_error END "
    f"WHERE ticket_id IS NOT NULL AND {_NAMES_THEM}"
)
_ERASE_UNTICKETED = (
    "UPDATE hd_email_out SET subject = :erased, body_text = :erased, "  # noqa: S608
    "to_addresses = ARRAY['erased@erased.invalid'], cc_addresses = '{}', "
    "status = CASE WHEN status = 'pending' THEN 'failed' ELSE status END, "
    "last_error = CASE WHEN status = 'pending' THEN :stopped ELSE NULL END "
    f"WHERE ticket_id IS NULL AND {_NAMES_THEM}"
)
# Their address in an email header kept on someone else's message (they were in cc).
_STRIP_HEADERS = (
    "UPDATE hd_message SET email = CAST(regexp_replace(email::text, :pattern, "
    "'erased@erased.invalid', 'gi') AS jsonb) "
    "WHERE email IS NOT NULL AND lower(email::text) LIKE :like"
)


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _pattern(email: str) -> str:
    """A Postgres regular expression for `email` in a header, with the display name before
    it when there is one ("Ana <ana@x.com>"), so the name goes with the address."""
    literal = "".join(c if c.isalnum() else "\\" + c for c in email)
    return f'(?:[^"<>,]*<)?{literal}>?'


async def erase_contact(session: AsyncSession, contact_id: UUID, email: str) -> dict[str, int]:
    """The rating comments, portal sign-ins, events and the contact itself."""
    p: dict[str, Any] = {"c": contact_id}
    found = {"email": email.lower(), "like": f"%{_like(email.lower())}%", "erased": ERASED}
    counts = {
        "rating_comments": await _changed(
            session,
            "UPDATE hd_csat SET comment = NULL WHERE comment IS NOT NULL AND (contact_id = :c "
            "OR ticket_id IN (SELECT id FROM hd_ticket WHERE contact_id = :c))",
            p,
        ),
        "portal_sessions": await _changed(
            session,
            "UPDATE hd_portal_session SET revoked_at = now() "
            "WHERE contact_id = :c AND revoked_at IS NULL",
            p,
        ),
        "portal_codes": await _changed(
            session, "DELETE FROM hd_portal_code WHERE email = :email", {"email": email}
        ),
        # A CSV import keys the customer by their address; other sources by their own id.
        # A later run of that import then makes a new contact, as a new customer would.
        "import_links": await _changed(
            session,
            "DELETE FROM hd_import_map WHERE kind = 'contact' AND local_id = :c",
            p,
        ),
        # Events already sent keep only that they happened; ones not sent yet are stopped
        # (they'd carry the customer's words to a webhook).
        "events": await _changed(
            session,
            "UPDATE hd_outbox SET payload = jsonb_build_object('erased', true), "  # noqa: S608
            "failed_at = CASE WHEN published_at IS NULL THEN coalesce(failed_at, now()) "
            "ELSE failed_at END, "
            "last_error = CASE WHEN published_at IS NULL THEN :stopped ELSE last_error END "
            f"WHERE ticket_id IN ({_THEIR_TICKETS}) "
            "OR payload->>'contact_id' = CAST(:c AS text) "
            # contact.updated nests the contact (events.py) and has no ticket
            "OR payload->'contact'->>'id' = CAST(:c AS text)",
            {**p, "stopped": STOPPED},
        ),
        # An import keeps who was copied on or followed other customers' tickets, and whom
        # their messages went to: the address goes from those lists; the others stay.
        "imported_addresses": await _changed(session, _STRIP_IMPORTED_FIELDS, found),
        "imported_recipients": await _changed(session, _STRIP_RECIPIENTS, found),
        "email_headers": await _changed(
            session, _STRIP_HEADERS, {**found, "pattern": _pattern(email.lower())}
        ),
        "sent_elsewhere": await _changed(session, _STRIP_SENT, found),
        "sign_in_emails": await _changed(session, _ERASE_UNTICKETED, {**found, "stopped": STOPPED}),
        # An import that could not bring them in kept their address (a CSV keys a contact
        # by it) and the error; the row stays, for the run's count.
        "import_failures": await _changed(
            session,
            "UPDATE hd_import_failure SET external_id = 'erased-' || CAST(id AS text), "
            "last_error = :erased "
            "WHERE lower(external_id) = :email OR lower(last_error) LIKE :like",
            found,
        ),
        # Webhook deliveries are copies of those events (md PL5: the body names the ticket,
        # and its subject is the customer's words): the same, and the receiver's answers go.
        "webhook_responses": await _changed(
            session,
            "UPDATE hd_webhook_attempt SET response = NULL WHERE response IS NOT NULL "  # noqa: S608
            "AND delivery_id IN (SELECT id FROM hd_webhook_delivery WHERE "
            f"body->'ticket'->>'id' IN (SELECT CAST(id AS text) FROM ({_THEIR_TICKETS}) t))",
            p,
        ),
        "webhook_deliveries": await _changed(
            session,
            "UPDATE hd_webhook_delivery SET body = jsonb_build_object('erased', true), "  # noqa: S608
            "state = CASE WHEN state = 'pending' THEN 'failed' ELSE state END, "
            "last_error = CASE WHEN state = 'pending' THEN :stopped ELSE last_error END, "
            "lease_until = NULL WHERE body->'ticket'->>'id' IN "
            f"(SELECT CAST(id AS text) FROM ({_THEIR_TICKETS}) t)",
            {**p, "stopped": STOPPED},
        ),
    }
    await session.execute(
        text(
            "UPDATE hd_contact SET email = :email, name = :name, vip = false, "
            "phone = NULL, organization = NULL, "
            "erased_at = now(), updated_at = now() WHERE id = :c"
        ),
        {**p, "email": erased_email(contact_id), "name": ERASED_NAME},
    )
    return counts
