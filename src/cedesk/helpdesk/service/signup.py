"""Self-service workspace sign-up (sign-in page -> form -> email code -> sign in).

1. `start`: checks what was typed, the per-address limits and the CAPTCHA, hashes the
   password, then in one transaction re-counts the limits under their locks, saves the
   sign-up and queues its code email. The API sends no email itself: the worker's
   sign-up lane does, within about a second.
2. `verify`: the right code creates the workspace, its first Admin, the defaults, the cloud
   plan and the first audit entry, and closes the sign-up, all in one transaction. The
   sign-up row is locked first, so a double-click or a second tab waits and then gets the
   same answer, and every try (right or wrong) is counted before the code is compared.
   The per-address daily cap on workspaces is checked here, where a workspace is made.
   A workspace address taken meanwhile rolls everything back; the person picks another
   and sends the same code again, without a new email.
3. `resend`: a new code for a waiting sign-up (60 s apart); the old code's email, if not
   sent yet, is cancelled.

Locks, the same order everywhere so two requests never wait on each other in a circle:
the requester's IP, then the email, then the sign-up row.

Nothing here logs an email address, a code or a password.
"""

import asyncio
import hmac
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.captcha import Captcha
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as auth_rules
from cedesk.helpdesk.domain import signup as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import signup as repo
from cedesk.helpdesk.service import audit, limits, passwords
from cedesk.helpdesk.service.auth import Sessionmaker, insert_workspace
from cedesk.helpdesk.service.errors import (
    BadCode,
    CaptchaFailed,
    CodeExpired,
    Conflict,
    Invalid,
    SignupExpired,
    SlugTaken,
    TooManyAttempts,
)

logger = logging.getLogger("cedesk.signup")

# The unique key a taken workspace address trips (migration 0003).
SLUG_KEY = "workspace_directory_pkey"


@dataclass(frozen=True)
class Started:
    signup_id: UUID
    code_expires_at: datetime
    resend_after: datetime


@dataclass(frozen=True)
class Done:
    workspace: str  # the address to sign in with


@dataclass(frozen=True)
class Limits:
    """Per-address limits from the settings; the per-email ones are in the domain."""

    per_ip_hour: int = 5
    workspaces_per_ip_day: int = 3


def _now() -> datetime:
    return datetime.now(UTC)


async def _lock_ip(s: AsyncSession, ip_hash: bytes | None) -> None:
    if ip_hash is not None:
        await repo.lock(s, f"signup-ip:{ip_hash.hex()}")


async def _lock_email(s: AsyncSession, email: str) -> None:
    await repo.lock(s, f"signup:{email}")


async def _check_started(
    s: AsyncSession, ip_hash: bytes | None, now: datetime, cap: Limits
) -> None:
    """Sign-ups started from this address in the last hour, and workspaces it made today."""
    if ip_hash is None:
        return
    if await repo.started_from_ip(s, ip_hash, now - timedelta(hours=1)) >= cap.per_ip_hour:
        raise TooManyAttempts("Too many sign-ups from this address. Try again in an hour.", 3600)
    await _check_created(s, ip_hash, now, cap)


async def _check_created(
    s: AsyncSession, ip_hash: bytes | None, now: datetime, cap: Limits
) -> None:
    if ip_hash is None:
        return
    if await repo.created_from_ip(s, ip_hash, now - timedelta(days=1)) >= cap.workspaces_per_ip_day:
        raise TooManyAttempts(
            "Too many workspaces created from this address today. Try again tomorrow.", 86400
        )


async def _check_codes_left(s: AsyncSession, email: str, ip_hash: bytes | None) -> None:
    """3 code emails an hour per email and requester; 10 per email from anyone."""
    total, mine = await repo.codes_last_hour(s, email, ip_hash)
    if mine >= rules.CODES_PER_HOUR or total >= rules.EMAIL_CODES_PER_HOUR:
        raise TooManyAttempts("Too many codes for this email. Wait an hour and try again.", 3600)


def _slug_clash(exc: IntegrityError) -> bool:
    """Whether the database refused the workspace because its address is taken (and not
    for some other reason, which must not be shown as "address taken")."""
    orig = exc.orig
    name = getattr(getattr(orig, "__cause__", None), "constraint_name", None) or getattr(
        orig, "constraint_name", None
    )
    return name == SLUG_KEY or SLUG_KEY in str(orig)


async def start(
    sm: Sessionmaker,
    *,
    slug: str,
    workspace_name: str,
    email: str,
    admin_name: str,
    password: str,
    ip: str | None,
    secret_key: str,
    captcha: Captcha | None,
    captcha_token: str | None,
    hash_slots: asyncio.Semaphore,
    ip_limits: Limits,
) -> Started:
    # What was typed: cheap checks first, so bad input costs nothing.
    slug = rules.clean_slug(slug)
    problem = rules.slug_problem(slug)
    if problem:
        raise Invalid(problem)
    address = rules.normalise_email(email)
    if address is None:
        raise Invalid("Enter a valid email address.")
    workspace_name, admin_name = workspace_name.strip(), admin_name.strip()
    if not workspace_name or not admin_name:
        raise Invalid("Enter the workspace name and your name.")
    problem = auth_rules.password_problem(password)
    if problem:
        raise Invalid(problem)

    now = _now()
    ip_key = rules.ip_hash(secret_key, ip)
    # A quick look first, so an address over its limit is refused before any slow work.
    # Not the final word: counted again below, under the lock, where the row is written.
    async with sm() as s, s.begin():
        await _check_started(s, ip_key, now, ip_limits)

    # The CAPTCHA before anything that answers about workspaces (so a script can't use
    # this to list which addresses exist) and before the slow work (hashing, the email).
    if captcha is not None and not await captcha.passed(captcha_token or "", ip):
        raise CaptchaFailed("The check that you're not a robot didn't pass. Try again.")
    async with sm() as s, s.begin():
        if await auth_repo.workspace_for_slug(s, slug) is not None:
            raise SlugTaken(rules.SLUG_TAKEN)

    # Argon2 takes ~64 MB per hash: only a few at once, the rest wait their turn.
    async with hash_slots:
        password_hash = await asyncio.to_thread(passwords.hash_password, password)

    signup_id = uuid4()
    code = rules.new_code()
    expires_at = now + rules.CODE_LIFETIME
    try:
        async with sm() as s, s.begin():
            # Parallel sign-ups from one address wait here one at a time, so each sees
            # the rows the ones before it wrote, and the limits hold.
            await _lock_ip(s, ip_key)
            await _check_started(s, ip_key, now, ip_limits)
            await _lock_email(s, address)
            await _check_codes_left(s, address, ip_key)
            await repo.supersede_pending(s, address, ip_key)
            await repo.insert_signup(
                s,
                signup_id=signup_id,
                slug=slug,
                workspace_name=workspace_name,
                email=address,
                admin_name=admin_name,
                password_hash=password_hash,
                code_hash=rules.code_hash(secret_key, signup_id, address, code),
                code_expires_at=expires_at,
                ip_hash=ip_key,
                now=now,
            )
            await repo.enqueue(
                s,
                signup_id=signup_id,
                to=address,
                subject=rules.SUBJECT,
                body=rules.code_email(admin_name, code),
                channel=rules.NOTIFY_CHANNEL,
            )
    except IntegrityError as exc:  # only if the locks were bypassed; never expected
        raise Conflict("A sign-up for this email just started. Check your inbox.") from exc
    logger.info("sign-up started", extra={"event": "signup_started"})
    return Started(signup_id, expires_at, now + rules.RESEND_AFTER)


async def verify(
    sm: Sessionmaker,
    *,
    signup_id: UUID,
    code: str,
    slug: str | None,
    ip: str | None,
    secret_key: str,
    default_plan: str,
    ip_limits: Limits,
) -> Done:
    """`slug`: a new address, when the first one was taken meanwhile (409 slug_taken)."""
    typed = rules.clean_code(code)
    if typed is None:
        raise BadCode(rules.BAD_CODE)
    if slug is not None:
        slug = rules.clean_slug(slug)
        problem = rules.slug_problem(slug)
        if problem:
            raise Invalid(problem)

    # Which address the sign-up came from, to take that address's lock before the row's.
    async with sm() as s, s.begin():
        peek = await repo.get_signup(s, signup_id)
    if peek is None:
        raise SignupExpired(rules.SIGNUP_GONE)
    ip_key = bytes(peek["ip_hash"]) if peek["ip_hash"] is not None else None

    workspace_id = uuid4()
    now = _now()
    wrong = False
    done: Done | None = None
    try:
        async with workspace_session(sm, workspace_id) as s:
            await _lock_ip(s, ip_key)
            row = await repo.get_signup(s, signup_id, lock=True)
            if row is None or row["status"] == "superseded":
                raise SignupExpired(rules.SIGNUP_GONE)
            right = hmac.compare_digest(
                bytes(row["code_hash"]),
                rules.code_hash(secret_key, signup_id, str(row["email"]), typed),
            )
            if row["status"] == "completed":
                # A second click, or a retry after a lost answer: same result, nothing new.
                if not right:
                    raise BadCode(rules.BAD_CODE)
                done = Done(str(row["slug"]))
            elif row["created_at"] <= now - rules.SIGNUP_LIFETIME:
                raise SignupExpired(rules.SIGNUP_GONE)
            elif row["code_tries"] >= rules.CODE_TRIES or row["code_expires_at"] <= now:
                raise CodeExpired(rules.CODE_EXPIRED)
            elif not right:
                # Counted under the row lock: parallel guesses each use up one of the tries.
                await repo.count_try(s, signup_id)
                wrong = True  # saved when this block commits; raised below
            else:
                # The daily cap, where a workspace is actually made (under the address's
                # lock, so parallel verifies from one address are counted one by one).
                await _check_created(s, ip_key, now, ip_limits)
                await repo.count_try(s, signup_id)
                final_slug = slug or str(row["slug"])
                await insert_workspace(
                    s,
                    workspace_id,
                    name=str(row["workspace_name"]),
                    slug=final_slug,
                    admin_email=str(row["email"]),
                    admin_name=str(row["admin_name"]),
                    password_hash=str(row["password_hash"]),
                )
                await audit.record_server(
                    s, workspace_id, Area.workspace, "created", str(row["workspace_name"]),
                    detail={"slug": final_slug}, actor_name=audit.SIGNUP, ip=ip,
                )  # fmt: skip
                await limits.start_plan(
                    s, workspace_id, default_plan, actor_name=audit.SIGNUP, ip=ip
                )
                await repo.complete(s, signup_id, workspace_id, final_slug, now)
                done = Done(final_slug)
    except IntegrityError as exc:
        if not _slug_clash(exc):
            # Not the address: a real fault. Logged (the type only) and a 500, never
            # shown as "address taken", which would send the person round in a loop.
            logger.error(
                "sign-up verify: the database refused the new workspace (%s)",
                type(exc.orig).__name__,
                extra={"event": "signup_verify_failed"},
            )
            raise
        # The address was taken between the form and the code: everything is rolled back
        # (the try too), and the same code works again with another address.
        raise SlugTaken(rules.SLUG_TAKEN) from exc
    if wrong:
        raise BadCode(rules.BAD_CODE)
    assert done is not None
    logger.info("sign-up verified", extra={"event": "signup_verified"})
    return done


async def resend(sm: Sessionmaker, *, signup_id: UUID, secret_key: str) -> Started:
    async with sm() as s, s.begin():
        peek = await repo.get_signup(s, signup_id)
    if peek is None:
        raise SignupExpired(rules.SIGNUP_GONE)
    address = str(peek["email"])
    ip_key = bytes(peek["ip_hash"]) if peek["ip_hash"] is not None else None

    now = _now()
    async with sm() as s, s.begin():
        # The email's lock before the row's, as `start` takes them.
        await _lock_email(s, address)
        row = await repo.get_signup(s, signup_id, lock=True)
        if (
            row is None
            or row["status"] != "pending"
            or row["created_at"] <= now - rules.SIGNUP_LIFETIME
        ):
            raise SignupExpired(rules.SIGNUP_GONE)
        since = await repo.seconds_since_last_code(s, signup_id)
        wait_for = rules.RESEND_AFTER.total_seconds()
        if since is not None and since < wait_for:
            wait = max(1, math.ceil(wait_for - since))
            raise TooManyAttempts(f"Wait {wait} seconds before asking for another code.", wait)
        await _check_codes_left(s, address, ip_key)
        code = rules.new_code()
        expires_at = now + rules.CODE_LIFETIME
        await repo.new_code(
            s, signup_id, rules.code_hash(secret_key, signup_id, address, code), expires_at
        )
        await repo.cancel_unsent(s, signup_id)
        await repo.enqueue(
            s,
            signup_id=signup_id,
            to=address,
            subject=rules.SUBJECT,
            body=rules.code_email(str(row["admin_name"]), code),
            channel=rules.NOTIFY_CHANNEL,
        )
    logger.info("sign-up code sent again", extra={"event": "signup_resent"})
    return Started(signup_id, expires_at, now + rules.RESEND_AFTER)
