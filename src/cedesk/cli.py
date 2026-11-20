"""`uv run cedesk <command>`: serve the API, run migrations, run the background worker,
create a workspace or a demo workspace, prune sessions, or write the OpenAPI file."""

import argparse
import asyncio
import getpass
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _migrate() -> int:
    from alembic import command
    from alembic.config import Config

    from cedesk.settings import get_settings

    settings = get_settings()
    url = settings.migration_database_url
    if settings.env.value != "dev" and "_dev@" in url:
        print("CEDESK_MIGRATION_DATABASE_URL uses the dev password.", file=sys.stderr)
        return 1
    print(f"Migrating {url.rsplit('@', 1)[-1]}", file=sys.stderr)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return 0


def _create_workspace(args: argparse.Namespace) -> int:
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.auth import create_workspace
    from cedesk.helpdesk.service.errors import ServiceError
    from cedesk.settings import get_settings

    # Asked at a prompt, never taken from the command line, so it stays out of shell history.
    password = getpass.getpass("First Admin's password: ")
    if password != getpass.getpass("Again: "):
        print("The passwords differ.", file=sys.stderr)
        return 1

    async def run() -> None:
        engine = make_engine(get_settings().database_url)
        try:
            workspace_id = await create_workspace(
                make_sessionmaker(engine),
                name=args.name,
                slug=args.slug,
                admin_email=args.admin_email,
                admin_name=args.admin_name,
                admin_password=password,
                seed=not args.no_seed,
            )
        finally:
            await engine.dispose()
        print(f"Created workspace {args.slug} ({workspace_id}). Sign in as {args.admin_email}.")

    try:
        asyncio.run(run())
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0


SWEEP_EVERY_SECONDS = 600  # expired idempotency keys
SLA_ALERT_EVERY_SECONDS = 60  # SLA breach alerts (md W14)
RETENTION_EVERY_SECONDS = 24 * 3600  # deleting old resolved tickets (md O2): at start, then daily


def _seed_workflow(slug: str) -> int:
    """Safe to run again: defaults already there, and an Admin's own changes, are kept."""
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.errors import ServiceError
    from cedesk.helpdesk.service.seeds import add_workflow_defaults
    from cedesk.settings import get_settings

    async def run() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            return await add_workflow_defaults(make_sessionmaker(engine), slug)
        finally:
            await engine.dispose()

    try:
        added = asyncio.run(run())
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Added {added} default(s) to {slug}; anything already there was kept.")
    return 0


def _clear_ip_allowlist(slug: str) -> int:
    """When the IP allowlist locks every Admin out; the Admins are emailed."""
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.errors import ServiceError
    from cedesk.helpdesk.service.security import clear_ip_allowlist
    from cedesk.settings import get_settings

    async def run() -> bool:
        engine = make_engine(get_settings().database_url)
        try:
            return await clear_ip_allowlist(make_sessionmaker(engine), slug)
        finally:
            await engine.dispose()

    try:
        cleared = asyncio.run(run())
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(
        f"IP allowlist of {slug} cleared; every address can sign in again."
        if cleared
        else f"{slug} has no IP allowlist; nothing changed."
    )
    return 0


def _set_plan(slug: str, plan: str) -> int:
    """Our cloud's operators give a workspace its plan (md O4); `none` takes it off."""
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.domain.limits import parse_plans
    from cedesk.helpdesk.service import limits
    from cedesk.helpdesk.service.errors import ServiceError
    from cedesk.settings import Edition, get_settings

    settings = get_settings()
    limits.configure(cloud=settings.edition is Edition.cloud, plans=parse_plans(settings.plans))

    async def run() -> None:
        engine = make_engine(settings.database_url)
        try:
            await limits.set_plan(make_sessionmaker(engine), slug, None if plan == "none" else plan)
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    note = "" if settings.edition is Edition.cloud else " (self-hosted: limits stay off)"
    print(f"{slug}: plan {plan}{note}.")
    return 0


def _sla_refresh() -> int:
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.sla import refresh_everywhere
    from cedesk.settings import get_settings

    async def run() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            return await refresh_everywhere(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    print(f"SLA worked out again for the open tickets of {asyncio.run(run())} workspace(s).")
    return 0


def _articles_markdown() -> int:
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.articles import markdown_everywhere
    from cedesk.settings import get_settings

    async def run() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            return await markdown_everywhere(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    print(f"Imported articles written as Markdown: {asyncio.run(run())}.")
    return 0


def _seed_demo(args: argparse.Namespace) -> int:
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.demo import seed_demo
    from cedesk.helpdesk.service.errors import ServiceError
    from cedesk.settings import get_settings

    # One password for every demo account, asked at a prompt (never in the code or argv).
    password = getpass.getpass("Password for all demo accounts (10+ characters): ")
    if password != getpass.getpass("Again: "):
        print("The passwords differ.", file=sys.stderr)
        return 1

    async def run() -> list[tuple[str, str, str]]:
        engine = make_engine(get_settings().database_url)
        try:
            return await seed_demo(
                make_sessionmaker(engine), slug=args.slug, name=args.name, password=password
            )
        finally:
            await engine.dispose()

    try:
        accounts = asyncio.run(run())
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Created workspace {args.slug} with 6 tickets. Sign in with workspace '{args.slug}':")
    for name, email, role in accounts:
        print(f"  {role:<12} {name:<14} {email}")
    return 0


def _prune_sessions() -> int:
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.auth import prune_ended_sessions
    from cedesk.settings import get_settings

    async def run() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            return await prune_ended_sessions(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    print(f"Deleted {asyncio.run(run())} ended sign-in sessions.")
    return 0


def _worker(once: bool) -> int:
    """Every few seconds: reads new emails from the IMAP mailbox (when one is set), reads
    connected Cevanos chats for new customer messages, sends queued emails, delivers
    outbox events (Cevanos take-overs and replies), alerts SLA breaches and overdue tasks,
    sends email campaigns within each workspace's limits (after the ticket emails),
    wakes snoozed tickets and resumes waiting workflow runs (once a minute), auto-closes
    inactive tickets (daily, before the archive pass), and drops expired idempotency keys.
    Run one next to the API (sending and delivering are safe with several; reading a
    mailbox is meant for one)."""
    import contextlib
    import logging

    from cedesk import logs
    from cedesk.cevanos_client import make_cevanos
    from cedesk.crypto import Sealer
    from cedesk.db import make_engine, make_sessionmaker, notifications
    from cedesk.helpdesk.api.campaigns import config_of as campaign_config
    from cedesk.helpdesk.domain import signup as signup_rules
    from cedesk.helpdesk.domain.limits import parse_plans
    from cedesk.helpdesk.service import limits as plan_limits
    from cedesk.helpdesk.service.apps import slack_bound
    from cedesk.helpdesk.service.archive import run_archive
    from cedesk.helpdesk.service.auto_close import run_auto_close
    from cedesk.helpdesk.service.automation import prune_everywhere as prune_runs
    from cedesk.helpdesk.service.automation import resume_due
    from cedesk.helpdesk.service.campaigns import run_due as send_campaigns
    from cedesk.helpdesk.service.cevanos import CevanosHandler, ChatPoller
    from cedesk.helpdesk.service.csat import CsatHandler
    from cedesk.helpdesk.service.delivery import Handler, deliver_due
    from cedesk.helpdesk.service.email import send_due
    from cedesk.helpdesk.service.email_in import poll
    from cedesk.helpdesk.service.idempotency import sweep_everywhere
    from cedesk.helpdesk.service.imports.framework import make_importers, retry_policy
    from cedesk.helpdesk.service.imports.framework import run_due as run_imports
    from cedesk.helpdesk.service.jira import make_post
    from cedesk.helpdesk.service.limits import remove_expired_retention_exports, run_retention
    from cedesk.helpdesk.service.password_reset import Sender
    from cedesk.helpdesk.service.portal_notice import PortalNoticeHandler
    from cedesk.helpdesk.service.privacy import remove_expired_exports
    from cedesk.helpdesk.service.push import push_due
    from cedesk.helpdesk.service.scanning import scan_due
    from cedesk.helpdesk.service.security_alerts import SecurityAlertHandler
    from cedesk.helpdesk.service.signup_mail import run_lane
    from cedesk.helpdesk.service.sla import refresh_everywhere
    from cedesk.helpdesk.service.sla_alerts import alert_everywhere
    from cedesk.helpdesk.service.slack_alerts import SlackAlertHandler
    from cedesk.helpdesk.service.snooze import wake_everywhere
    from cedesk.helpdesk.service.stream import prune_everywhere as prune_stream
    from cedesk.helpdesk.service.task_alerts import alert_everywhere as alert_tasks
    from cedesk.helpdesk.service.webhooks import WebhookFanout
    from cedesk.helpdesk.service.webhooks import prune_everywhere as prune_webhooks
    from cedesk.helpdesk.service.webhooks import send_due as send_webhooks
    from cedesk.mail import MailboxError, make_mailbox, make_mailer
    from cedesk.push_client import FcmClient, PushConfigError
    from cedesk.scanning import make_scanner
    from cedesk.settings import Env, ScanBackend, get_settings
    from cedesk.storage import make_store
    from cedesk.webhook_client import WebhookClient

    settings = get_settings()
    logs.configure(settings.log_level)
    # The worker deletes per the plans too (retention capped by a plan's history, md O2).
    plan_limits.configure(
        cloud=settings.edition.value == "cloud", plans=parse_plans(settings.plans)
    )
    problems = settings.unsafe() + settings.mail_problems()
    # Push banners to the mobile app (md P8): off without the Firebase settings; a key
    # that can't be read stops the worker here, with the reason (never the key).
    pusher = None
    if not problems and settings.push_on():
        assert settings.push_fcm_service_account is not None
        try:
            pusher = FcmClient(
                settings.push_fcm_project_id, settings.push_fcm_service_account.get_secret_value()
            )
        except PushConfigError as exc:
            problems.append(str(exc))
    if problems:
        print("Refusing to start:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    logger = logging.getLogger("cedesk.worker")
    if settings.env is not Env.dev and settings.scan_backend is ScanBackend.none:
        logger.warning(
            "virus scan is off: files customers send are not checked "
            "(set CEDESK_SCAN_BACKEND=clamd, deploy/README.md)"
        )

    async def run() -> None:
        engine = make_engine(settings.database_url)
        sm = make_sessionmaker(engine)
        mailer = make_mailer(settings)
        mailbox = make_mailbox(settings)
        store = make_store(settings)  # files on replies out, files in customer emails
        scanner = make_scanner(settings)
        # Outbox handlers. The Cevanos one does nothing for a
        # workspace that has not connected Cevanos; the CSAT one emails the survey; the portal
        # one tells portal customers an agent replied.
        cevanos = make_cevanos(settings)
        fallback = (
            Sender(settings.mail_from_address, settings.mail_from_name)
            if settings.mail_from_address
            else None
        )
        portal_url = settings.portal_url or settings.app_url
        handlers: list[Handler] = [
            CevanosHandler(sm, cevanos),
            CsatHandler(sm, base_url=portal_url, fallback=fallback),
            PortalNoticeHandler(sm, base_url=portal_url, fallback=fallback),
            SecurityAlertHandler(sm, fallback=fallback),
            WebhookFanout(sm),  # queues one delivery per webhook; send_webhooks sends them
        ]
        sealer = Sealer(settings.secret_key.get_secret_value())
        # Slack alerts for Urgent and High tickets, when the workspace installed the app.
        handlers.append(
            SlackAlertHandler(sm, sealer, make_post, slack_bound, app_url=settings.app_url)
        )
        webhook_client = WebhookClient()
        chats = ChatPoller(sm, cevanos, batch=settings.cevanos_poll_batch)
        importers = make_importers(
            settings.secret_key.get_secret_value(), store, retry=retry_policy(settings)
        )
        swept_at = 0.0
        alerted_at = 0.0
        retained_at = -float(RETENTION_EVERY_SECONDS)  # due at the first pass
        logger.info("worker started", extra={"event": settings.mail_backend.value})
        # Self-service sign-up codes go out on their own lane, beside this loop, so no job
        # below can hold a code back. Only with sign-up on; off, nothing here changes.
        lane: asyncio.Task[None] | None = None
        listening = contextlib.AsyncExitStack()
        if settings.signup_enabled:
            wake = None
            try:
                wake = await listening.enter_async_context(
                    notifications(engine, signup_rules.NOTIFY_CHANNEL)
                )
            except Exception:
                logger.warning("sign-up lane: no LISTEN connection, checking every second")
            sign_up_codes = run_lane(
                sm,
                mailer,
                Sender(
                    settings.mail_from_address or "no-reply@cedesk.local",
                    settings.mail_from_name,
                ),
                concurrency=settings.signup_mail_concurrency,
                wake=wake,
                once=once,
            )
            if once:
                await sign_up_codes
            else:
                lane = asyncio.create_task(sign_up_codes, name="signup-mail")
        # Tickets made before SLA was wired (or while the SLA code had a bug) get their
        # stored due time, so `sort=sla` is right after every deploy without a manual step.
        try:
            await refresh_everywhere(sm)
        except Exception:
            logger.exception("SLA refresh at start failed")
        try:
            while True:
                if mailbox is not None:
                    try:
                        received = await poll(sm, mailbox, store, settings.attachment_max_bytes)
                    except MailboxError as exc:
                        received = []
                        logger.warning("mailbox not read", extra={"error": str(exc)})
                    if received:
                        outcomes = ", ".join(sorted(r.outcome.value for r in received))
                        logger.info(f"emails in: {outcomes}", extra={"event": "email_in"})
                if scanner is not None:  # before sending: emailed files must be clean
                    scanned = await scan_due(sm, store, scanner)
                    if scanned.infected or scanned.failed:
                        logger.warning(
                            f"virus scan: {scanned.infected} infected, {scanned.failed} "
                            "could not be checked",
                            extra={"event": "scan"},
                        )
                polled = await chats.poll()
                if polled.added or polled.failed:
                    logger.info(
                        f"cevanos chats: {polled.read} read, {polled.added} new customer "
                        f"messages, {polled.failed} not read",
                        extra={"event": "cevanos"},
                    )
                # Imports from other help desks work in slices of about 20 seconds, so the
                # email, outbox and SLA work below is never held up by a big import.
                try:
                    importing = await run_imports(sm, importers)
                except Exception:
                    importing = 0
                    logger.exception("import pass failed")
                if importing:
                    logger.info(f"imports worked on: {importing}", extra={"event": "import"})
                # Before sending: a handler may queue an email (the CSAT survey).
                outbox = await deliver_due(sm, handlers)
                if outbox.retrying or outbox.failed:
                    logger.info(
                        f"outbox: {outbox.delivered} delivered, {outbox.retrying} retrying, "
                        f"{outbox.failed} failed",
                        extra={"event": "outbox"},
                    )
                result = await send_due(sm, mailer, store=store, scanning=scanner is not None)
                if result.sent or result.retrying or result.failed:
                    logger.info(
                        f"emails: {result.sent} sent, {result.retrying} retrying, "
                        f"{result.failed} failed",
                        extra={"event": "email"},
                    )
                # Campaign emails (plan phase 6) after the ticket emails, so a reply never
                # waits behind a campaign; each workspace within its per-minute limit.
                try:
                    sent = await send_campaigns(sm, mailer, campaign_config(settings))
                except Exception:
                    logger.exception("campaign pass failed")
                else:
                    if sent.sent or sent.failed or sent.started or sent.finished:
                        logger.info(
                            f"campaigns: {sent.started} started, {sent.sent} sent, "
                            f"{sent.retrying} retrying, {sent.failed} failed, "
                            f"{sent.skipped} skipped, {sent.finished} finished",
                            extra={"event": "campaign"},
                        )
                # After the outbox (its handlers make notifications) and the emails: one
                # pass sends for at most 20 s, the rest waits for the next.
                try:
                    banners = await push_due(sm, pusher)
                except Exception:
                    banners = 0
                    logger.exception("push pass failed")
                if banners:
                    logger.info(f"push: {banners} banners", extra={"event": "push"})
                try:
                    hooks = await send_webhooks(sm, sealer, webhook_client)
                except Exception:
                    logger.exception("webhook pass failed")
                else:
                    if hooks.delivered or hooks.retrying or hooks.failed:
                        logger.info(
                            f"webhooks: {hooks.delivered} delivered, {hooks.retrying} "
                            f"retrying, {hooks.failed} failed",
                            extra={"event": "webhook"},
                        )
                if time.monotonic() - alerted_at > SLA_ALERT_EVERY_SECONDS:
                    try:
                        breached = await alert_everywhere(sm)
                    except Exception:
                        breached = 0
                        logger.exception("SLA breach alerts failed")
                    if breached:
                        logger.info(f"SLA breaches alerted: {breached}", extra={"event": "sla"})
                    try:
                        overdue = await alert_tasks(sm)
                    except Exception:
                        overdue = 0
                        logger.exception("Overdue task alerts failed")
                    if overdue:
                        logger.info(f"Overdue tasks alerted: {overdue}", extra={"event": "tasks"})
                    try:  # snoozed tickets whose time came (feature-gap plan 1.1)
                        woke = await wake_everywhere(sm)
                    except Exception:
                        woke = 0
                        logger.exception("Waking snoozed tickets failed")
                    if woke:
                        logger.info(f"Snoozed tickets woke: {woke}", extra={"event": "snooze"})
                    try:  # workflow runs whose wait is over (feature-gap plan 5.3)
                        resumed = await resume_due(sm)
                    except Exception:
                        resumed = 0
                        logger.exception("Resuming workflow runs failed")
                    if resumed:
                        logger.info(f"Workflow runs resumed: {resumed}",
                                    extra={"event": "automation"})  # fmt: skip
                    alerted_at = time.monotonic()
                if time.monotonic() - swept_at > SWEEP_EVERY_SECONDS:
                    await sweep_everywhere(sm)
                    try:  # customer exports past their 7 days (S11)
                        await remove_expired_exports(sm, store)
                    except Exception:
                        logger.exception("Removing old privacy exports failed")
                    try:  # retention exports past their 30 days (O2)
                        await remove_expired_retention_exports(sm, store)
                    except Exception:
                        logger.exception("Removing old retention exports failed")
                    try:  # live-update events past their 24 hours, and old stream tickets (C11)
                        await prune_stream(sm)
                    except Exception:
                        logger.exception("Pruning the live-update events failed")
                    try:  # webhook deliveries past their 30 days (md PL6)
                        await prune_webhooks(sm)
                    except Exception:
                        logger.exception("Pruning the webhook log failed")
                    try:  # automation runs past their 30 days (plan 5.5)
                        await prune_runs(sm)
                    except Exception:
                        logger.exception("Pruning the automation run log failed")
                    swept_at = time.monotonic()
                if time.monotonic() - retained_at > RETENTION_EVERY_SECONDS:
                    try:  # resolved tickets past the archive age to the archive (md O3)
                        moved = await run_archive(sm)
                        if moved:
                            logger.info(f"archive: {moved} tickets", extra={"event": "archive"})
                    except Exception:
                        logger.exception("Archiving failed")
                    try:  # Pending tickets with no customer reply for too long (plan 1.4)
                        closed = await run_auto_close(sm)
                        if closed:
                            logger.info(f"auto-close: {closed} tickets",
                                        extra={"event": "auto_close"})  # fmt: skip
                    except Exception:
                        logger.exception("Auto-close failed")
                    try:  # resolved tickets past the workspace's retention (md O2)
                        gone = await run_retention(sm, store)
                        if gone:
                            logger.info(f"retention: {gone} tickets", extra={"event": "retention"})
                    except Exception:
                        logger.exception("Retention failed")
                    retained_at = time.monotonic()
                if once:
                    return
                await asyncio.sleep(settings.worker_interval_seconds)
        finally:
            if lane is not None:
                lane.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await lane
            await listening.aclose()
            await engine.dispose()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


def _openapi(out: Path, check: bool) -> int:
    """Writes the API description to docs/api/ for the frontend and integrators. With
    --check, fails when the file is out of date (CI and the test suite run that)."""
    import json

    from cedesk.main import create_app

    text = json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"
    if check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            print(f"{out} is out of date: run `uv run cedesk openapi`.", file=sys.stderr)
            return 1
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cedesk")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the API on :8100")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--reload", action="store_true")
    sub.add_parser("migrate", help="Apply all migrations (as the owner role)")
    sub.add_parser(
        "prune-sessions", help="Delete sign-in sessions that ended over 7 days ago (run daily)"
    )
    worker = sub.add_parser(
        "worker", help="Read the support mailbox and send queued emails; runs until stopped"
    )
    worker.add_argument("--once", action="store_true", help="One pass, then stop")
    create = sub.add_parser("create-workspace", help="A new workspace and its first Admin")
    create.add_argument("--name", required=True, help="Shown name, e.g. 'Acme Support'")
    create.add_argument("--slug", required=True, help="Sign-in address, e.g. 'acme'")
    create.add_argument("--admin-email", required=True)
    create.add_argument("--admin-name", required=True)
    create.add_argument(
        "--no-seed",
        action="store_true",
        help="Start empty, without the default teams and ticket types",
    )
    demo = sub.add_parser(
        "seed-demo", help="A demo workspace like the mock: Ritesh (Admin), agents, tickets"
    )
    demo.add_argument("--slug", default="acme-store")
    demo.add_argument("--name", default="Acme Store")
    workflow = sub.add_parser(
        "seed-workflow",
        help="Add the default saved replies, rules and macros to an existing workspace",
    )
    workflow.add_argument("--workspace", required=True, help="Sign-in address, e.g. 'acme-store'")
    clear_ips = sub.add_parser(
        "clear-ip-allowlist",
        help="Empty a workspace's IP allowlist when it locks every Admin out",
    )
    clear_ips.add_argument("--workspace", required=True, help="Sign-in address, e.g. 'acme-store'")
    sub.add_parser(
        "sla-refresh",
        help="Work out the SLA of every open ticket again (once, after migration 0019)",
    )
    sub.add_parser(
        "articles-markdown",
        help="Write imported help center articles that still hold HTML as Markdown (once)",
    )
    plan = sub.add_parser("set-plan", help="Give a workspace its cloud plan (limits, md O4)")
    plan.add_argument("--workspace", required=True, help="Sign-in address, e.g. 'acme-store'")
    plan.add_argument("--plan", required=True, help="free, growth, enterprise, or none")
    spec = sub.add_parser("openapi", help="Write the API description to docs/api/openapi.json")
    spec.add_argument("--out", type=Path, default=ROOT / "docs" / "api" / "openapi.json")
    spec.add_argument("--check", action="store_true", help="Fail if the file is out of date")
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from cedesk import logs
        from cedesk.settings import get_settings

        logs.configure(get_settings().log_level)
        uvicorn.run(
            "cedesk.main:app",
            host="127.0.0.1",
            port=args.port,
            reload=args.reload,
            log_config=None,  # keep the JSON logging set up above
        )
        return 0
    if args.command == "migrate":
        return _migrate()
    if args.command == "worker":
        return _worker(args.once)
    if args.command == "prune-sessions":
        return _prune_sessions()
    if args.command == "seed-demo":
        return _seed_demo(args)
    if args.command == "seed-workflow":
        return _seed_workflow(args.workspace)
    if args.command == "clear-ip-allowlist":
        return _clear_ip_allowlist(args.workspace)
    if args.command == "sla-refresh":
        return _sla_refresh()
    if args.command == "articles-markdown":
        return _articles_markdown()
    if args.command == "set-plan":
        return _set_plan(args.workspace, args.plan)
    if args.command == "openapi":
        return _openapi(args.out, args.check)
    return _create_workspace(args)


if __name__ == "__main__":
    raise SystemExit(main())
