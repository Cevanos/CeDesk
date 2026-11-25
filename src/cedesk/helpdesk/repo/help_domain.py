"""SQL for the help center's custom domain (feature-gap plan 4.2). RLS applies to
`hd_help_domain`; `help_domain_directory` is global and read-only for the app role (a trigger
keeps it in step with the verified domains)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = (
    "workspace_id, domain, status, verify_token, verified_at, last_checked_at, "
    "failed_checks, last_error, created_at, updated_at"
)


async def get(session: AsyncSession, *, lock: bool = False) -> RowMapping | None:
    result = await session.execute(
        text(f"SELECT {_COLUMNS} FROM hd_help_domain" + (" FOR UPDATE" if lock else ""))  # noqa: S608
    )
    return result.mappings().first()


async def put(
    session: AsyncSession, *, workspace_id: UUID, domain: str, token: str, member_id: UUID
) -> None:
    """Sets the workspace's domain: pending, with a new token and nothing checked yet. (The
    caller leaves an unchanged domain alone.)"""
    await session.execute(
        text(
            "INSERT INTO hd_help_domain (workspace_id, domain, verify_token, created_by) "
            "VALUES (:ws, :domain, :token, :by) "
            "ON CONFLICT (workspace_id) DO UPDATE SET domain = EXCLUDED.domain, "
            "status = 'pending', verify_token = EXCLUDED.verify_token, verified_at = NULL, "
            "last_checked_at = NULL, failed_checks = 0, last_error = NULL, updated_at = now()"
        ),
        {"ws": workspace_id, "domain": domain, "token": token, "by": member_id},
    )


async def delete(session: AsyncSession) -> bool:
    result = await session.execute(text("DELETE FROM hd_help_domain RETURNING workspace_id"))
    return result.first() is not None


async def mark_verified(session: AsyncSession) -> None:
    """The proof was found. A domain another workspace has already verified raises an
    IntegrityError (the unique index on verified domains): the first to prove control wins."""
    await session.execute(
        text(
            "UPDATE hd_help_domain SET status = 'verified', "
            "verified_at = coalesce(verified_at, now()), last_checked_at = now(), "
            "failed_checks = 0, last_error = NULL, updated_at = now()"
        )
    )


async def mark_missing(session: AsyncSession, error: str, *, demote: bool) -> None:
    """The proof was not found: counted against the domain. `demote` sends a verified domain
    back to pending (its directory entry goes with it); a pending one just keeps the error."""
    await session.execute(
        text(
            "UPDATE hd_help_domain SET last_checked_at = now(), last_error = :error, "
            "failed_checks = failed_checks + 1, updated_at = now(), "
            "status = CASE WHEN :demote THEN 'pending' ELSE status END, "
            "verified_at = CASE WHEN :demote THEN NULL ELSE verified_at END"
        ),
        {"error": error[:300], "demote": demote},
    )


async def note_error(session: AsyncSession, error: str) -> None:
    """Said, but not counted and not a check: the domain's clock (`last_checked_at`) and its
    count of failures stay as they were."""
    await session.execute(
        text("UPDATE hd_help_domain SET last_error = :error, updated_at = now()"),
        {"error": error[:300]},
    )


async def mark_unknown(session: AsyncSession, error: str) -> None:
    """DNS didn't answer: said, but not counted against the domain."""
    await session.execute(
        text(
            "UPDATE hd_help_domain SET last_checked_at = now(), last_error = :error, "
            "updated_at = now()"
        ),
        {"error": error[:300]},
    )


async def due_for_check(session: AsyncSession, older_than: datetime) -> RowMapping | None:
    """This workspace's verified domain, when it has not been checked since `older_than`."""
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM hd_help_domain WHERE status = 'verified' "  # noqa: S608
            "AND (last_checked_at IS NULL OR last_checked_at < :before)"
        ),
        {"before": older_than},
    )
    return result.mappings().first()


# The global directory: verified domains, readable before the workspace is known


async def workspace_for_domain(session: AsyncSession, domain: str) -> UUID | None:
    result = await session.execute(
        text("SELECT workspace_id FROM help_domain_directory WHERE domain = :domain"),
        {"domain": domain},
    )
    found = result.scalar_one_or_none()
    return UUID(str(found)) if found else None


async def directory_workspaces(session: AsyncSession) -> Sequence[UUID]:
    """Every workspace with a verified domain."""
    result = await session.execute(
        text("SELECT workspace_id FROM help_domain_directory ORDER BY workspace_id")
    )
    return [UUID(str(r)) for r in result.scalars().all()]
