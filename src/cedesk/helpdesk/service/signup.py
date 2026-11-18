"""Self-service workspace sign-up (sign-in page -> form -> email code -> sign in).

1. `start`: checks what was typed, the per-address limits and the CAPTCHA, hashes the
   password, then in one transaction saves the sign-up and queues its code email. The API
   sends no email itself: the worker's sign-up lane does, within about a second.
2. `verify`: the right code creates the workspace, its first Admin, the defaults, the cloud
   plan and the first audit entry, and closes the sign-up, all in one transaction. The
   sign-up row is locked first, so a double-click or a second tab waits and then gets the
   same answer, and every try (right or wrong) is counted before the code is compared.
   A workspace address taken meanwhile rolls everything back; the person picks another
   and sends the same code again, without a new email.
3. `resend`: a new code for a waiting sign-up (60 s apart, 3 code emails an hour).

Nothing here logs an email address, a code or a password.
"""

import asyncio
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
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


async def _lock_email(s: AsyncSession, email: str) -> None:
    """Sign-ups and resends for one email, one at a time (until this transaction ends),
    so its limits are counted exactly and two never both become the waiting one."""
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"signup:{email}"},
    )


async def _check_codes_left(s: AsyncSession, email: str, now: datetime) -> None:
    sent = await repo.codes_to_address(s, email, now - timedelta(hours=1))
    if sent >= rules.CODES_PER_HOUR:
        raise TooManyAttempts("Too many codes for this email. Wait an hour and try again.", 3600)


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
    async with sm() as s, s.begin():
        if ip_key is not None:
            if await repo.started_from_ip(s, ip_key, now - timedelta(hours=1)) >= (
                ip_limits.per_ip_hour
            ):
                raise TooManyAttempts(
                    "Too many sign-ups from this address. Try again in an hour.", 3600
                )
            if await repo.created_from_ip(s, ip_key, now - timedelta(days=1)) >= (
                ip_limits.workspaces_per_ip_day
            ):
                raise TooManyAttempts(
                    "Too many workspaces created from this address today. Try again tomorrow.",
                    86400,
                )
        if await auth_repo.workspace_for_slug(s, slug) is not None:
            raise SlugTaken(rules.SLUG_TAKEN)

    # The CAPTCHA before the slow work (hashing, the email): a bot spends nothing of ours.
    if captcha is not None and not await captcha.passed(captcha_token or "", ip):
        raise CaptchaFailed("The check that you're not a robot didn't pass. Try again.")

    # Argon2 takes ~64 MB per hash: only a few at once, the rest wait their turn.
    async with hash_slots:
        password_hash = await asyncio.to_thread(passwords.hash_password, password)

    signup_id = uuid4()
    code = rules.new_code()
    expires_at = now + rules.CODE_LIFETIME
    try:
        async with sm() as s, s.begin():
            await _lock_email(s, address)
            await _check_codes_left(s, address, now)
            await repo.supersede_pending(s, address)
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
            )
            await repo.enqueue(
                s,
                signup_id=signup_id,
                to=address,
                subject=rules.SUBJECT,
                body=rules.code_email(admin_name, code),
                channel=rules.NOTIFY_CHANNEL,
            )
    except IntegrityError as exc:  # only if the email lock was bypassed; never expected
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

    workspace_id = uuid4()
    now = _now()
    wrong = False
    done: Done | None = None
    try:
        async with workspace_session(sm, workspace_id) as s:
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
            else:
                # Counted before anything else, under the row lock: parallel guesses each
                # use up one of the 5 tries.
                await repo.count_try(s, signup_id)
                if not right:
                    wrong = True  # saved when this block commits; raised below
                else:
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
        # The address was taken between the form and the code: everything is rolled back
        # (the try too), and the same code works again with another address.
        raise SlugTaken(rules.SLUG_TAKEN) from exc
    if wrong:
        raise BadCode(rules.BAD_CODE)
    assert done is not None
    logger.info("sign-up verified", extra={"event": "signup_verified"})
    return done


async def resend(sm: Sessionmaker, *, signup_id: UUID, secret_key: str) -> Started:
    now = _now()
    async with sm() as s, s.begin():
        row = await repo.get_signup(s, signup_id, lock=True)
        if (
            row is None
            or row["status"] != "pending"
            or row["created_at"] <= now - rules.SIGNUP_LIFETIME
        ):
            raise SignupExpired(rules.SIGNUP_GONE)
        address = str(row["email"])
        await _lock_email(s, address)
        last = await repo.last_code_at(s, signup_id)
        if last is not None and now - last < rules.RESEND_AFTER:
            wait = int((rules.RESEND_AFTER - (now - last)).total_seconds()) + 1
            raise TooManyAttempts(f"Wait {wait} seconds before asking for another code.", wait)
        await _check_codes_left(s, address, now)
        code = rules.new_code()
        expires_at = now + rules.CODE_LIFETIME
        await repo.new_code(
            s, signup_id, rules.code_hash(secret_key, signup_id, address, code), expires_at
        )
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
