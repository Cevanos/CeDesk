"""Reporting logins for BI tools (differentiator plan #7). Pure rules: the login's name,
its password, and the password's SCRAM-SHA-256 verifier.

The verifier is worked out here, so `CREATE ROLE ... PASSWORD` carries a hash: the plain
password never reaches the database, its logs or `pg_stat_activity`. PostgreSQL stores a
verifier in this format as it is (the format of `pg_authid.rolpassword`).
"""

import base64
import hashlib
import hmac
import re
import secrets
import unicodedata

NAME = re.compile(r"^report_[a-z0-9_]{2,40}$")
NAME_RULE = "A reporting login is report_ then 2-40 lowercase letters, digits or _."
PASSWORD_MIN = 16
GROUP = "cedesk_reporting"  # every reporting login is in it; it holds the SELECT rights
CONNECTION_LIMIT = 5
STATEMENT_TIMEOUT = "60s"
SCRAM_ITERATIONS = 4096  # PostgreSQL's default


class ReportingError(ValueError):
    pass


def check_name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ReportingError(NAME_RULE)
    return name


def check_password(password: str) -> str:
    if len(password) < PASSWORD_MIN:
        raise ReportingError(f"Use a password of {PASSWORD_MIN} characters or more.")
    if "\x00" in password:
        raise ReportingError("The password can't hold a NUL character.")
    return password


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def scram_verifier(password: str, *, salt: bytes | None = None,
                   iterations: int = SCRAM_ITERATIONS) -> str:  # fmt: skip
    """`SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>` (RFC 5802, 7677).
    The password is normalised (NFKC) the way SASLprep does for ordinary text."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    normal = unicodedata.normalize("NFKC", password).encode("utf-8")
    salted = hashlib.pbkdf2_hmac("sha256", normal, salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"
