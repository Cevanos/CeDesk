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
    outbox events (Cevanos take-overs and replies), alerts SLA breaches and overdue tasks
    (once a minute), and drops expired idempotency keys. Run one next to the API (sending
    and delivering are safe with several; reading a mailbox is meant for one)."""
    import logging

    from cedesk import logs
    from cedesk.cevanos_client import make_cevanos
    from cedesk.db import make_engine, make_sessionmaker
    from cedesk.helpdesk.service.cevanos import CevanosHandler, ChatPoller
    from cedesk.helpdesk.service.csat import CsatHandler
    from cedesk.helpdesk.service.delivery import Handler, deliver_due
    from cedesk.helpdesk.service.email import send_due
    from cedesk.helpdesk.service.email_in import poll
    from cedesk.helpdesk.service.idempotency import sweep_everywhere
    from cedesk.helpdesk.service.password_reset import Sender
    from cedesk.helpdesk.service.portal_notice import PortalNoticeHandler
    from cedesk.helpdesk.service.scanning import scan_due
    from cedesk.helpdesk.service.sla import refresh_everywhere
    from cedesk.helpdesk.service.sla_alerts import alert_everywhere
    from cedesk.helpdesk.service.task_alerts import alert_everywhere as alert_tasks
    from cedesk.mail import MailboxError, make_mailbox, make_mailer
    from cedesk.scanning import make_scanner
    from cedesk.settings import get_settings
    from cedesk.storage import make_store

    settings = get_settings()
    logs.configure(settings.log_level)
    problems = settings.unsafe() + settings.mail_problems()
    if problems:
        print("Refusing to start:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    logger = logging.getLogger("cedesk.worker")

    async def run() -> None:
        engine = make_engine(settings.database_url)
        sm = make_sessionmaker(engine)
        mailer = make_mailer(settings)
        mailbox = make_mailbox(settings)
        store = make_store(settings)  # files on replies out, files in customer emails
        scanner = make_scanner(settings)
        # Outbox handlers (webhooks join in Phase 3). The Cevanos one does nothing for a
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
        ]
        chats = ChatPoller(sm, cevanos, batch=settings.cevanos_poll_batch)
        swept_at = 0.0
        alerted_at = 0.0
        logger.info("worker started", extra={"event": settings.mail_backend.value})
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
                    alerted_at = time.monotonic()
                if time.monotonic() - swept_at > SWEEP_EVERY_SECONDS:
                    await sweep_everywhere(sm)
                    swept_at = time.monotonic()
                if once:
                    return
                await asyncio.sleep(settings.worker_interval_seconds)
        finally:
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
    sub.add_parser(
        "sla-refresh",
        help="Work out the SLA of every open ticket again (once, after migration 0019)",
    )
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
    if args.command == "sla-refresh":
        return _sla_refresh()
    if args.command == "openapi":
        return _openapi(args.out, args.check)
    return _create_workspace(args)


if __name__ == "__main__":
    raise SystemExit(main())
