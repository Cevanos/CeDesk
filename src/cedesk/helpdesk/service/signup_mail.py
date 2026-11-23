"""The worker's sign-up lane: sends sign-up code emails, and nothing else.

It runs beside the worker's main loop (mailbox, imports, outbox, workspace emails,
webhooks, sweeps...), never inside it, so no other job can hold a code back. It wakes as
soon as the API queues a code (`pg_notify` on commit) and at least every second.

- Claiming: `FOR UPDATE SKIP LOCKED` with a lease, so several workers can run, never send
  one email twice, and an email a crashed worker held is taken again after the lease.
- Sending: up to `concurrency` at once, to stay under the mail provider's rate.
- Failing: a temporary error is tried again after 5, 20 and 60 s (all within the code's
  10 minutes); a permanent one (address refused) or the last try marks it failed. The
  person can ask for a new code from the page.
- Health: a warning when a code has waited over 30 s, so a stuck lane is seen.
- Housekeeping: sign-ups over two days old and old code emails are deleted hourly.

No log line holds an address, a subject or a code.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cedesk.helpdesk.domain import signup as rules
from cedesk.helpdesk.domain.email import new_message_id
from cedesk.helpdesk.repo import signup as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.mail import Mailer, OutgoingEmail, SendError

logger = logging.getLogger("cedesk.signup_mail")

POLL_SECONDS = 1.0
HEALTH_EVERY_SECONDS = 60.0
SLOW_AFTER_SECONDS = 30.0
PRUNE_EVERY_SECONDS = 3600.0
ERROR_PAUSE_SECONDS = 5.0


@dataclass
class LaneResult:
    claimed: int = 0
    sent: int = 0
    retrying: int = 0
    failed: int = 0


async def send_due(
    sm: Sessionmaker, mailer: Mailer, sender: Sender, *, concurrency: int
) -> LaneResult:
    """One pass: claims up to a batch of due code emails and sends them."""
    async with sm() as s, s.begin():
        rows = await repo.claim_due(s, rules.MAIL_BATCH, rules.MAIL_LEASE_SECONDS)
    result = LaneResult(claimed=len(rows))
    if not rows:
        return result
    slots = asyncio.Semaphore(concurrency)

    async def send_one(row: repo.Row) -> SendError | None:
        email = OutgoingEmail(
            from_address=sender.address,
            from_name=sender.name,
            to=[str(row["to_address"])],
            subject=str(row["subject"]),
            text=str(row["body_text"]),
            message_id=new_message_id(sender.address),
        )
        async with slots:
            try:
                await mailer.send(email)
            except SendError as exc:
                return exc
            except Exception as exc:  # a bug or an odd failure: tried again like a 4xx
                return SendError(f"Could not send ({type(exc).__name__}).")
        return None

    outcomes = await asyncio.gather(*(send_one(row) for row in rows))
    now = datetime.now(UTC)
    for row, error in zip(rows, outcomes, strict=True):
        async with sm() as s, s.begin():
            if error is None:
                await repo.mark_sent(s, row["id"])
                result.sent += 1
                continue
            wait = None if error.permanent else rules.mail_retry_wait(int(row["attempts"]))
            if wait is None:
                await repo.mark_failed(s, row["id"], str(error))
                result.failed += 1
            else:
                await repo.mark_retry(s, row["id"], str(error), now + timedelta(seconds=wait))
                result.retrying += 1
    return result


async def housekeeping(sm: Sessionmaker) -> None:
    now = datetime.now(UTC)
    async with sm() as s, s.begin():
        await repo.prune_mail(s, now - rules.MAIL_KEPT)
        await repo.prune(s, now - rules.SIGNUP_KEPT)


async def check_health(sm: Sessionmaker) -> None:
    async with sm() as s, s.begin():
        waited = await repo.oldest_waiting_seconds(s)
    if waited is not None and waited > SLOW_AFTER_SECONDS:
        logger.warning(
            "sign-up codes are waiting to be sent",
            extra={"event": "signup_mail_slow", "seconds": round(waited)},
        )


async def run_lane(
    sm: Sessionmaker,
    mailer: Mailer,
    sender: Sender,
    *,
    concurrency: int,
    wake: asyncio.Event | None = None,
    once: bool = False,
) -> None:
    """Runs until cancelled (or one pass with `once`). Any error is logged and the lane
    carries on: it must never stop, and never stop the worker's main loop."""
    checked_at = pruned_at = -float("inf")
    logger.info("sign-up lane started", extra={"event": "signup_mail"})
    while True:
        if wake is not None:
            wake.clear()  # before the pass: a code queued during it wakes the next one
        result = LaneResult()
        pause = POLL_SECONDS
        try:
            result = await send_due(sm, mailer, sender, concurrency=concurrency)
            if result.sent or result.retrying or result.failed:
                logger.info(
                    f"sign-up codes: {result.sent} sent, {result.retrying} retrying, "
                    f"{result.failed} failed",
                    extra={"event": "signup_mail"},
                )
            if time.monotonic() - checked_at > HEALTH_EVERY_SECONDS:
                checked_at = time.monotonic()
                await check_health(sm)
            if time.monotonic() - pruned_at > PRUNE_EVERY_SECONDS:
                pruned_at = time.monotonic()
                await housekeeping(sm)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sign-up lane pass failed")
            pause = ERROR_PAUSE_SECONDS  # the database is down, say: no busy loop of errors
        if once:
            return
        if result.claimed >= rules.MAIL_BATCH:
            continue  # more waiting: no pause
        if wake is None or pause != POLL_SECONDS:
            await asyncio.sleep(pause)
        else:
            try:
                await asyncio.wait_for(wake.wait(), pause)
            except TimeoutError:
                pass
