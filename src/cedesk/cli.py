"""`uv run cedesk <command>`: serve the API, run migrations, create a workspace or a demo
workspace, prune sessions, or write the OpenAPI file."""

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
    if args.command == "prune-sessions":
        return _prune_sessions()
    if args.command == "seed-demo":
        return _seed_demo(args)
    if args.command == "openapi":
        return _openapi(args.out, args.check)
    return _create_workspace(args)


if __name__ == "__main__":
    raise SystemExit(main())
