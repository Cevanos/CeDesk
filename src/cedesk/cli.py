"""`uv run cedesk <command>`: serve the API, run migrations, or create a workspace."""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _migrate() -> int:
    from alembic import command
    from alembic.config import Config

    from cedesk.settings import get_settings

    url = get_settings().migration_database_url
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
    create = sub.add_parser("create-workspace", help="A new workspace and its first Admin")
    create.add_argument("--name", required=True, help="Shown name, e.g. 'Acme Support'")
    create.add_argument("--slug", required=True, help="Sign-in address, e.g. 'acme'")
    create.add_argument("--admin-email", required=True)
    create.add_argument("--admin-name", required=True)
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("cedesk.main:app", host="127.0.0.1", port=args.port, reload=args.reload)
        return 0
    if args.command == "migrate":
        return _migrate()
    if args.command == "prune-sessions":
        return _prune_sessions()
    return _create_workspace(args)


if __name__ == "__main__":
    raise SystemExit(main())
