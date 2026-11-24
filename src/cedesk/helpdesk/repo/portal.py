"""SQL for the customer portal: settings, sign-in codes and portal sessions. RLS applies."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.reports import IMPORT_SOURCES
from cedesk.helpdesk.repo.attachments import MESSAGE_FILES


async def allowed_domains(session: AsyncSession) -> list[str]:
    result = await session.execute(text("SELECT allowed_domains FROM hd_portal_settings"))
    value = result.scalar_one_or_none()
    return list(value) if value else []


async def settings_row(session: AsyncSession) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT s.allowed_domains, s.csat_survey, s.web_form, s.show_owner, s.updated_at, "
            "m.name AS updated_by_name "
            "FROM hd_portal_settings s LEFT JOIN member m ON m.id = s.updated_by"
        )
    )
    return result.mappings().first()


async def save_settings(
    session: AsyncSession,
    workspace_id: UUID,
    domains: list[str],
    csat_survey: bool,
    web_form: bool,
    member_id: UUID,
    show_owner: bool = True,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_portal_settings (workspace_id, allowed_domains, csat_survey, "
            "web_form, show_owner, updated_by) VALUES (:ws, :domains, :csat, :form, :owner, :by) "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "allowed_domains = EXCLUDED.allowed_domains, csat_survey = EXCLUDED.csat_survey, "
            "web_form = EXCLUDED.web_form, show_owner = EXCLUDED.show_owner, "
            "updated_by = EXCLUDED.updated_by, "
            "updated_at = now()"
        ),
        {
            "ws": workspace_id,
            "domains": domains,
            "csat": csat_survey,
            "form": web_form,
            "owner": show_owner,
            "by": member_id,
        },
    )


async def web_form_on(session: AsyncSession) -> bool:
    result = await session.execute(text("SELECT web_form FROM hd_portal_settings"))
    return bool(result.scalar_one_or_none())


async def web_forms_since(session: AsyncSession, email: str, since: datetime) -> int:
    """Web form tickets from this email since a time (spam limit per email)."""
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id "
            "WHERE c.email = :email AND t.channel = 'web' AND t.created_at > :since"
        ),
        {"email": email, "since": since},
    )
    return int(result.scalar_one())


async def lock_email(session: AsyncSession, workspace_id: UUID, email: str) -> None:
    """Serialises code requests and checks for one email, so parallel requests can't
    send more codes than allowed or each spend the same try."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"portal-code:{workspace_id}:{email}"},
    )


async def codes_since(session: AsyncSession, email: str, since: datetime) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_portal_code WHERE email = :email AND created_at > :since"),
        {"email": email, "since": since},
    )
    return int(result.scalar_one())


async def insert_code(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    email: str,
    code_hash: bytes,
    expires_at: datetime,
) -> None:
    # A new code replaces the earlier ones: only the latest works.
    await session.execute(
        text("UPDATE hd_portal_code SET used_at = now() WHERE email = :email AND used_at IS NULL"),
        {"email": email},
    )
    await session.execute(
        text(
            "INSERT INTO hd_portal_code (workspace_id, email, code_hash, expires_at) "
            "VALUES (:ws, :email, :hash, :expires)"
        ),
        {"ws": workspace_id, "email": email, "hash": code_hash, "expires": expires_at},
    )


async def live_code(session: AsyncSession, email: str, now: datetime) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT id, code_hash, tries FROM hd_portal_code "
            "WHERE email = :email AND used_at IS NULL AND expires_at > :now "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"email": email, "now": now},
    )
    return result.mappings().first()


async def wrong_try(session: AsyncSession, code_id: UUID, burn: bool) -> None:
    await session.execute(
        text(
            "UPDATE hd_portal_code SET tries = tries + 1, "
            "used_at = CASE WHEN :burn THEN now() ELSE used_at END WHERE id = :id"
        ),
        {"id": code_id, "burn": burn},
    )


async def use_code(session: AsyncSession, code_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_portal_code SET used_at = now() WHERE id = :id"), {"id": code_id}
    )


async def prune_codes(session: AsyncSession, before: datetime) -> None:
    await session.execute(
        text("DELETE FROM hd_portal_code WHERE created_at < :before"), {"before": before}
    )


async def insert_session(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    contact_id: UUID,
    token_hash: bytes,
    expires_at: datetime,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_portal_session (workspace_id, contact_id, token_hash, expires_at) "
            "VALUES (:ws, :contact, :hash, :expires) RETURNING id"
        ),
        {"ws": workspace_id, "contact": contact_id, "hash": token_hash, "expires": expires_at},
    )
    return UUID(str(result.scalar_one()))


async def live_session(session: AsyncSession, token_hash: bytes) -> RowMapping | None:
    """The session and its customer, with the portal's email domains (in the same query,
    so every portal request checks them at no extra cost)."""
    result = await session.execute(
        text(
            "SELECT s.id AS session_id, s.contact_id, c.email, c.name, "
            "(SELECT allowed_domains FROM hd_portal_settings) AS allowed_domains "
            "FROM hd_portal_session s JOIN hd_contact c ON c.id = s.contact_id "
            "WHERE s.token_hash = :hash AND s.revoked_at IS NULL AND s.expires_at > now()"
        ),
        {"hash": token_hash},
    )
    return result.mappings().first()


async def live_sessions(session: AsyncSession) -> Sequence[RowMapping]:
    """Every portal session still signed in (`id`, `email`), locked: the caller ends the
    ones whose email the portal no longer allows."""
    result = await session.execute(
        text(
            "SELECT s.id, c.email FROM hd_portal_session s "
            "JOIN hd_contact c ON c.id = s.contact_id "
            "WHERE s.revoked_at IS NULL AND s.expires_at > now() FOR UPDATE OF s"
        )
    )
    return result.mappings().all()


async def revoke_sessions(session: AsyncSession, session_ids: Sequence[UUID]) -> None:
    await session.execute(
        text(
            "UPDATE hd_portal_session SET revoked_at = now() WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": list(session_ids)},
    )


async def revoke_session(session: AsyncSession, session_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_portal_session SET revoked_at = now() WHERE id = :id"),
        {"id": session_id},
    )


async def prune_sessions(session: AsyncSession, before: datetime) -> None:
    await session.execute(
        text("DELETE FROM hd_portal_session WHERE expires_at < :before OR revoked_at < :before"),
        {"before": before},
    )


# Portal tickets: always filtered by the signed-in customer's contact.

# An imported ticket carries what the old help desk had: its custom fields (agents' own,
# e.g. a fraud score), extra tags, who was copied, the link to its agent view. None of that
# was ever meant for the customer, so on an imported ticket the portal shows only the
# fields of the ticket's type (those an agent set later). Every other ticket shows its
# fields as before: CeDesk only ever stores a type's own fields on them.
_TICKET = (
    "SELECT t.id, t.number, t.subject, t.status, t.channel, "
    "CASE WHEN t.source = ANY(:imported) THEN COALESCE((SELECT jsonb_object_agg(f.key, "
    "f.value) FROM jsonb_each(t.fields) f WHERE EXISTS (SELECT 1 FROM "
    "jsonb_array_elements(y.fields) d WHERE d->>'key' = f.key)), '{}'::jsonb) "
    "ELSE t.fields END AS fields, t.created_at, "
    "t.updated_at, t.resolved_at, y.name AS type_name, "
    "(SELECT to_jsonb(r) FROM (SELECT score, comment, source, rated_at FROM hd_csat "
    "WHERE ticket_id = t.id) r) AS csat, "
    # Where it stands (plan #9): worked out in domain.portal, these columns never go out.
    "am.name AS assignee_name, tm.name AS team_name, rc.pending_item AS receipt_pending, "
    "rc.result AS receipt_result, rc.verified AS receipt_verified, "
    "COALESCE((SELECT show_owner FROM hd_portal_settings), true) AS show_owner "
    "FROM hd_ticket t LEFT JOIN hd_ticket_type y ON y.id = t.type_id "
    "LEFT JOIN member am ON am.id = t.assignee_id "
    "LEFT JOIN hd_team tm ON tm.id = t.team_id "
    "LEFT JOIN hd_resolution rc ON rc.ticket_id = t.id "
)


_IMPORTED = list(IMPORT_SOURCES)


async def my_tickets(
    session: AsyncSession,
    contact_id: UUID,
    *,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Newest first."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            _TICKET + "WHERE t.contact_id = :contact "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (t.created_at, t.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY t.created_at DESC, t.id DESC LIMIT :limit"
        ),
        {"contact": contact_id, "at": at, "row": row_id, "limit": limit, "imported": _IMPORTED},
    )
    return result.mappings().all()


async def my_ticket(session: AsyncSession, contact_id: UUID, ticket_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(_TICKET + "WHERE t.id = :id AND t.contact_id = :contact"),
        {"id": ticket_id, "contact": contact_id, "imported": _IMPORTED},
    )
    return result.mappings().first()


async def tickets_since(session: AsyncSession, contact_id: UUID, since: datetime) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM hd_ticket WHERE contact_id = :contact "
            "AND channel = 'portal' AND created_at > :since"
        ),
        {"contact": contact_id, "since": since},
    )
    return int(result.scalar_one())


async def visible_messages(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Customer, agent and AI messages only (never notes or system lines), newest first."""
    at, row_id = before if before else (None, None)
    result = await session.execute(
        text(
            # Fixed strings only: MESSAGE_FILES is a constant.
            "SELECT m.id, m.kind, m.body, m.author_name, m.created_at, "  # noqa: S608
            + MESSAGE_FILES
            + " FROM hd_message m WHERE m.ticket_id = :id "
            "AND m.kind IN ('customer', 'agent', 'ai') "
            "AND (CAST(:at AS timestamptz) IS NULL "
            "     OR (m.created_at, m.id) < (CAST(:at AS timestamptz), CAST(:row AS uuid))) "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
        ),
        {"id": ticket_id, "at": at, "row": row_id, "limit": limit},
    )
    return result.mappings().all()


async def visible_attachment(
    session: AsyncSession, contact_id: UUID, attachment_id: UUID
) -> RowMapping | None:
    """A file on a message the customer can see, on one of their tickets."""
    result = await session.execute(
        text(
            "SELECT f.id, f.name, f.size, f.mime, f.object_key, f.scan_status "
            "FROM hd_attachment f JOIN hd_message m ON m.id = f.message_id "
            "JOIN hd_ticket t ON t.id = m.ticket_id "
            "WHERE f.id = :id AND t.contact_id = :contact "
            "AND m.kind IN ('customer', 'agent', 'ai')"
        ),
        {"id": attachment_id, "contact": contact_id},
    )
    return result.mappings().first()


async def notice_queued(session: AsyncSession, header_id: str) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM hd_email_out WHERE header_message_id = :id"), {"id": header_id}
    )
    return result.first() is not None


async def recent_notice(session: AsyncSession, ticket_id: UUID, since: datetime) -> bool:
    """A "you have a reply" email for this ticket since a time."""
    result = await session.execute(
        text(
            "SELECT 1 FROM hd_email_out WHERE ticket_id = :ticket AND created_at > :since "
            "AND header_message_id LIKE '<portal-reply-%' LIMIT 1"
        ),
        {"ticket": ticket_id, "since": since},
    )
    return result.first() is not None
