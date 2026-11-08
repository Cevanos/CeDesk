"""Sign-in rules that need no database: token format, lockout, input normalisation."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from cedesk.helpdesk.domain.permissions import Role

SESSION_LIFETIME = timedelta(hours=12)
# Sessions that expired or were signed out are deleted once they ended this long ago.
ENDED_SESSION_KEPT = timedelta(days=7)
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)
PASSWORD_MIN, PASSWORD_MAX = 10, 128


@dataclass(frozen=True)
class Caller:
    """The signed-in member behind a request. The workspace comes only from here."""

    workspace_id: UUID
    member_id: UUID
    role: Role
    session_id: UUID


def new_token(workspace_id: UUID) -> str:
    # The workspace id in the token only says where to look; the random part must
    # still match a stored hash in that workspace, so editing it gains nothing.
    return f"{workspace_id}.{secrets.token_urlsafe(32)}"


def token_workspace(token: str) -> UUID | None:
    head, sep, rest = token.partition(".")
    if not sep or not rest or len(token) > 200:
        return None
    try:
        return UUID(head)
    except ValueError:
        return None


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def password_problem(password: str) -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Use at most {PASSWORD_MAX} characters."
    return None


def is_locked(locked_until: datetime | None, now: datetime) -> bool:
    return locked_until is not None and locked_until > now


def after_failure(failed_logins: int, now: datetime) -> tuple[int, datetime | None]:
    """The new (failed_logins, locked_until) after one more wrong password."""
    failed = failed_logins + 1
    if failed >= MAX_FAILED_LOGINS:
        return 0, now + LOCKOUT
    return failed, None
