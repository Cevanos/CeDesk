"""Self-service workspace sign-up rules that need no database.

Anyone may start a sign-up from the sign-in page: a workspace address, a workspace name,
their name, email and password. The workspace is only created once they type the 6-digit
code emailed to them. Until then the sign-up is a row in the global `workspace_signup`
table (there is no workspace yet to hold it), and its code email waits in the global
`auth_email_out` queue, which the worker sends on its own lane, apart from every other job.

Locks, always taken in this order so two requests never wait on each other in a circle:
the requester's IP (`signup-ip:`), then the email (`signup:`), then the sign-up row. Every
limit is counted under its lock, in the transaction that writes, so parallel requests
can't all slip under it.

The code is stored only as an HMAC bound to the sign-up and the email (never in plain
text, never logged), lasts 10 minutes, and allows 5 tries. The client's IP address is
kept only as an HMAC too: enough to count sign-ups per address, not to read it back.
"""

import hashlib
import hmac
import re
import unicodedata
from datetime import timedelta
from uuid import UUID

from cedesk.helpdesk.domain import portal

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")

CODE_LIFETIME = timedelta(minutes=10)
CODE_TRIES = 5  # tries of one code (right or wrong) before it stops working
# Code emails per hour (sign-ups and resends). Counted per email *and* requester (the
# client IP), so a stranger can't use up someone else's codes; the total per email is
# capped higher, so nobody can flood an inbox from many addresses.
CODES_PER_HOUR = 3
EMAIL_CODES_PER_HOUR = 10
RESEND_AFTER = timedelta(seconds=60)  # between two code emails of one sign-up
SIGNUP_LIFETIME = timedelta(hours=24)  # after this, start again (resend no longer works)
SIGNUP_KEPT = timedelta(days=2)  # rows are deleted after this (the per-day counts need 1)

# The code email: tries and the waits between them. A code lives 10 minutes, so every
# retry is over well within that; after the last one the email is marked failed.
MAIL_RETRY_SECONDS = (5, 20, 60)
MAIL_LEASE_SECONDS = 120  # a claimed email is free again if its sender never reports back
MAIL_BATCH = 50
MAIL_KEPT = timedelta(days=1)  # sent and failed code emails are deleted after this
NOTIFY_CHANNEL = "cedesk_signup_mail"  # the API's pg_notify wakes the worker's lane

# First path segments of the agent app (the frontend's AGENT_AREAS) and names that would
# let a workspace pass for CeDesk itself. A new workspace can't take one; workspaces made
# from the command line before this list existed keep theirs.
RESERVED = frozenset(
    {
        # the agent app's own pages (frontend src/lib/public-routes.ts)
        "account", "agents", "api", "archive", "customers", "dev", "forgot-password",
        "login", "rate", "reports", "reset-password", "settings", "sso", "tickets",
        # sign-up and sign-in pages
        "signup", "sign-up", "register", "signin", "sign-in", "logout", "verify",
        # CeDesk itself and common service names
        "cedesk", "admin", "administrator", "app", "www", "mail", "email", "smtp",
        "support", "help", "helpdesk", "status", "docs", "blog", "billing", "static",
        "assets", "cdn", "portal", "security", "root", "system", "test", "demo",
    }
)  # fmt: skip

BAD_CODE = "That code didn't work. Check the email and try again."
CODE_EXPIRED = "That code has expired or was tried too often. Ask for a new one."
SIGNUP_GONE = "This sign-up has expired. Start again."
SLUG_TAKEN = "That workspace address is taken. Choose another."
SUBJECT = "Your CeDesk sign-up code"


def clean_slug(slug: str) -> str:
    return slug.strip().lower()


def slug_problem(slug: str) -> str | None:
    """Why a new workspace can't have this address, or None. `slug` is cleaned already."""
    if not SLUG.match(slug):
        return "The workspace address uses 3-40 lowercase letters, digits or dashes."
    if "--" in slug:
        return "The workspace address can't have two dashes in a row."
    if slug in RESERVED:
        return "That workspace address is reserved. Choose another."
    return None


# A stranger types these, and the workspace name goes into emails CeDesk sends from its own
# address (every portal sign-in code says "Your sign-in code for <name> support"), so they
# can't carry a message of their own: no line breaks or other control or invisible
# characters, and nothing that reads as a link or an email address.
_LINK = re.compile(
    r"://|\bwww\.|@|[a-z0-9-]{2,}\.(?:[a-z]{2,24})(?=$|[\s/:?#.,;!)\]])", re.IGNORECASE
)


def name_problem(value: str, what: str) -> str | None:
    """Why a typed name (`what`: "Your name", "The workspace name") can't be used, or None.
    `value` is stripped already."""
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in value):
        return f"{what} can't contain line breaks or hidden characters."
    if _LINK.search(value):
        return f"{what} can't contain a web or email address."
    return None


def normalise_email(email: str) -> str | None:
    """Lower-case and trimmed, or None when it isn't an email address."""
    return portal.normalise_email(email)


def new_code() -> str:
    return portal.new_code()


def clean_code(code: str) -> str | None:
    return portal.clean_code(code)


def code_hash(secret_key: str, signup_id: UUID, email: str, code: str) -> bytes:
    """Keyed and bound to this sign-up and email: a copied row can't be tried offline,
    nor its code used for another sign-up."""
    message = f"signup-code:{signup_id}:{email}:{code}".encode()
    return hmac.new(secret_key.encode(), message, hashlib.sha256).digest()


def ip_hash(secret_key: str, ip: str | None) -> bytes | None:
    """The client's address, kept only to count sign-ups per address."""
    if not ip:
        return None
    return hmac.new(secret_key.encode(), f"signup-ip:{ip}".encode(), hashlib.sha256).digest()


def code_email(code: str) -> str:
    """Only our own words and the code: nothing typed on the form goes in, since the form
    can name any email address (a stranger's) and CeDesk sends this from its own."""
    return (
        "Hello,\n\n"
        "Your code to finish creating your CeDesk workspace is:\n\n"
        f"    {code}\n\n"
        "It works for 10 minutes. If you didn't ask for it, ignore this email: nothing is "
        "created without the code.\n"
    )


def mail_retry_wait(attempts: int) -> int | None:
    """Seconds before the next try after `attempts` failed tries, or None: give up."""
    if attempts <= 0:
        return MAIL_RETRY_SECONDS[0]
    if attempts > len(MAIL_RETRY_SECONDS):
        return None
    return MAIL_RETRY_SECONDS[attempts - 1]
