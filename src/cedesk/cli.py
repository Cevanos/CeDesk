"""`uv run cedesk <command>`: serve the API or run migrations."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cedesk")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the API on :8100")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--reload", action="store_true")
    sub.add_parser("migrate", help="Apply all migrations (as the owner role)")
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("cedesk.main:app", host="127.0.0.1", port=args.port, reload=args.reload)
        return 0

    from alembic import command
    from alembic.config import Config

    from cedesk.settings import get_settings

    url = get_settings().migration_database_url
    print(f"Migrating {url.rsplit('@', 1)[-1]}", file=sys.stderr)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
