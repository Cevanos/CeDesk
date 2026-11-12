"""Sign-in rules that need no database: token format, lockout, input normalisation."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from cedesk.helpdesk.domain.permissions import Access, Action, Role, built_in

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
    # What they may do and on which tickets; a built-in role's when not given.
    access: Access = None  # type: ignore[assignment]
    # Past the workspace's password expiry (S3): only changing the password is allowed.
    password_expired: bool = False
    # Password checked, authenticator code not yet (S2): only the code can be sent.
    second_factor_pending: bool = False
    # The workspace requires 2FA and this member hasn't set it up: only setting it up works.
    two_factor_setup_required: bool = False

    def __post_init__(self) -> None:
        if self.access is None:
            object.__setattr__(self, "access", built_in(self.role))

    def can(self, action: Action) -> bool:
        return self.access.can(action)


# Forgot password (md A3): a link lasts 30 minutes; at most 3 are sent per hour per member.
RESET_LIFETIME = timedelta(minutes=30)
RESETS_PER_HOUR = 3


def reset_link(app_url: str, token: str) -> str:
    """The token goes after `#`, so browsers never send it to a server or in a Referer;
    the reset page reads it and posts it to POST /auth/reset-password."""
    return f"{app_url.rstrip('/')}/reset-password#token={token}"


def reset_email(name: str, workspace_name: str, link: str) -> str:
    return (
        f"Hi {name},\n\n"
        f"Someone asked to reset your CeDesk password for {workspace_name}. "
        f"To choose a new one, open this link within 30 minutes:\n\n{link}\n\n"
        "If it wasn't you, ignore this email: your password stays the same.\n"
    )


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


def password_problem(password: str, minimum: int = PASSWORD_MIN) -> str | None:
    """`minimum`: the workspace's setting (S3, 8-64); 10 when it has none."""
    if len(password) < minimum:
        return f"Use at least {minimum} characters."
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
