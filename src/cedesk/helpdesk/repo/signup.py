"""SQL for self-service sign-up: the global `workspace_signup` and `auth_email_out` tables.

Neither table has row-level security (no workspace exists yet), so these run in a plain
session; `verify` runs them inside the new workspace's `workspace_session`, so the sign-up
is closed in the same transaction that creates the workspace.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping


# Sign-ups

_GET = "SELECT * FROM workspace_signup WHERE id = :id"
_GET_LOCKED = _GET + " FOR UPDATE"


async def started_from_ip(session: AsyncSession, ip_hash: bytes, since: datetime) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM workspace_signup WHERE ip_hash = :ip AND created_at > :since"),
        {"ip": ip_hash, "since": since},
    )
    return int(result.scalar_one())


async def created_from_ip(session: AsyncSession, ip_hash: bytes, since: datetime) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM workspace_signup WHERE ip_hash = :ip "
            "AND status = 'completed' AND completed_at > :since"
        ),
        {"ip": ip_hash, "since": since},
    )
    return int(result.scalar_one())


async def supersede_pending(session: AsyncSession, email: str) -> None:
    """An email's waiting sign-up, replaced by a new one: its code stops working and its
    password hash is dropped at once."""
    await session.execute(
        text(
            "UPDATE workspace_signup SET status = 'superseded', password_hash = NULL "
            "WHERE email = :email AND status = 'pending'"
        ),
        {"email": email},
    )


async def insert_signup(
    session: AsyncSession,
    *,
    signup_id: UUID,
    slug: str,
    workspace_name: str,
    email: str,
    admin_name: str,
    password_hash: str,
    code_hash: bytes,
    code_expires_at: datetime,
    ip_hash: bytes | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO workspace_signup (id, slug, workspace_name, email, admin_name, "
            "password_hash, code_hash, code_expires_at, ip_hash) VALUES (:id, :slug, "
            ":workspace_name, :email, :admin_name, :password_hash, :code_hash, :expires, :ip)"
        ),
        {
            "id": signup_id,
            "slug": slug,
            "workspace_name": workspace_name,
            "email": email,
            "admin_name": admin_name,
            "password_hash": password_hash,
            "code_hash": code_hash,
            "expires": code_expires_at,
            "ip": ip_hash,
        },
    )


async def get_signup(session: AsyncSession, signup_id: UUID, *, lock: bool = False) -> Row | None:
    """`lock`: held until the transaction ends, so a double-click or a second tab waits
    for the first verify instead of racing it."""
    result = await session.execute(text(_GET_LOCKED if lock else _GET), {"id": signup_id})
    return result.mappings().first()


async def count_try(session: AsyncSession, signup_id: UUID) -> None:
    await session.execute(
        text("UPDATE workspace_signup SET code_tries = code_tries + 1 WHERE id = :id"),
        {"id": signup_id},
    )


async def new_code(
    session: AsyncSession, signup_id: UUID, code_hash: bytes, expires_at: datetime
) -> None:
    await session.execute(
        text(
            "UPDATE workspace_signup SET code_hash = :hash, code_expires_at = :expires, "
            "code_tries = 0 WHERE id = :id"
        ),
        {"id": signup_id, "hash": code_hash, "expires": expires_at},
    )


async def complete(
    session: AsyncSession, signup_id: UUID, workspace_id: UUID, slug: str, now: datetime
) -> None:
    """Done: the password hash is dropped (the member row has its own copy now). `slug`:
    the address the workspace got, which may differ from the first one chosen."""
    await session.execute(
        text(
            "UPDATE workspace_signup SET status = 'completed', password_hash = NULL, "
            "workspace_id = :ws, slug = :slug, completed_at = :now WHERE id = :id"
        ),
        {"id": signup_id, "ws": workspace_id, "slug": slug, "now": now},
    )


async def prune(session: AsyncSession, before: datetime) -> int:
    result = await session.execute(
        text("DELETE FROM workspace_signup WHERE created_at < :before"), {"before": before}
    )
    return int(getattr(result, "rowcount", 0) or 0)


# Code emails (the worker's own lane)


async def codes_to_address(session: AsyncSession, address: str, since: datetime) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM auth_email_out WHERE to_address = :to AND created_at > :since"),
        {"to": address, "since": since},
    )
    return int(result.scalar_one())


async def last_code_at(session: AsyncSession, signup_id: UUID) -> datetime | None:
    result = await session.execute(
        text("SELECT max(created_at) FROM auth_email_out WHERE signup_id = :id"),
        {"id": signup_id},
    )
    value: datetime | None = result.scalar_one()
    return value


async def enqueue(
    session: AsyncSession, *, signup_id: UUID, to: str, subject: str, body: str, channel: str
) -> None:
    """Queues the code email and wakes the worker's lane (`pg_notify` is delivered only
    when this transaction commits, so the lane never looks before the row is there)."""
    await session.execute(
        text(
            "INSERT INTO auth_email_out (signup_id, to_address, subject, body_text) "
            "VALUES (:id, :to, :subject, :body)"
        ),
        {"id": signup_id, "to": to, "subject": subject, "body": body},
    )
    await session.execute(text("SELECT pg_notify(:channel, '')"), {"channel": channel})


async def claim_due(session: AsyncSession, limit: int, lease_seconds: int) -> Sequence[Row]:
    """Takes up to `limit` code emails that are due, counts the attempt, and pushes their
    next try `lease_seconds` ahead. SKIP LOCKED: two workers never take the same email,
    and one that crashed mid-send leaves it to be taken again when the lease runs out."""
    result = await session.execute(
        text(
            "UPDATE auth_email_out o SET attempts = attempts + 1, "
            "next_attempt_at = now() + make_interval(secs => :lease) "
            "FROM (SELECT id FROM auth_email_out WHERE status = 'pending' "
            "      AND next_attempt_at <= now() ORDER BY next_attempt_at, id "
            "      LIMIT :limit FOR UPDATE SKIP LOCKED) due "
            "WHERE o.id = due.id "
            "RETURNING o.id, o.to_address, o.subject, o.body_text, o.attempts"
        ),
        {"limit": limit, "lease": lease_seconds},
    )
    return result.mappings().all()


async def mark_sent(session: AsyncSession, email_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE auth_email_out SET status = 'sent', sent_at = now(), last_error = NULL, "
            "body_text = '(removed after sending)' WHERE id = :id"
        ),
        {"id": email_id},
    )


async def mark_retry(
    session: AsyncSession, email_id: UUID, error: str, next_attempt_at: datetime
) -> None:
    await session.execute(
        text("UPDATE auth_email_out SET last_error = :error, next_attempt_at = :at WHERE id = :id"),
        {"id": email_id, "error": error[:500], "at": next_attempt_at},
    )


async def mark_failed(session: AsyncSession, email_id: UUID, error: str) -> None:
    await session.execute(
        text(
            "UPDATE auth_email_out SET status = 'failed', last_error = :error, "
            "body_text = '(removed after sending)' WHERE id = :id"
        ),
        {"id": email_id, "error": error[:500]},
    )


async def oldest_waiting_seconds(session: AsyncSession) -> float | None:
    """How long the oldest due code email has waited: the lane's health number."""
    result = await session.execute(
        text(
            "SELECT extract(epoch FROM now() - min(created_at)) FROM auth_email_out "
            "WHERE status = 'pending' AND next_attempt_at <= now()"
        )
    )
    value = result.scalar_one()
    return None if value is None else float(value)


async def prune_mail(session: AsyncSession, before: datetime) -> None:
    await session.execute(
        text("DELETE FROM auth_email_out WHERE status <> 'pending' AND created_at < :before"),
        {"before": before},
    )
