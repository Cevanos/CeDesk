# CeDesk

Help desk backend: tickets, routing, SLA, macros, rules, customer portal and imports.
CeDesk works on its own; connecting a Cevanos workspace adds the AI agent, the
AI-to-human handover and the Cevanos chat channels.

## Run it locally

Needs Python 3.12 via [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                      # install
docker compose up -d db      # Postgres 17 on localhost:5447 (roles from infra/postgres/init)
cp .env.example .env
uv run cedesk migrate        # apply migrations as cedesk_owner
uv run cedesk serve --reload # API on http://127.0.0.1:8100, docs at /docs
```

Or everything in containers: `docker compose --profile api up --build`.

## Test

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest                # DB tests use the cedesk_test database, never cedesk
```

## Rules every change keeps

1. **Tenancy.** Every table holding workspace data has `workspace_id`, and the migration
   that creates it calls `tenant_rls_sql(...)` (enabled and forced RLS).
   `tests/db/test_tenancy.py` fails otherwise.
2. **Roles.** The API connects as `cedesk_app`, which owns nothing and cannot bypass RLS.
   Migrations run as `cedesk_owner`. `tests/db/test_roles.py` checks both.
3. **Layers.** `api` → `service` → `repo` / `domain`; `domain` has no database or web code
   (`uv run lint-imports`).
4. **One migration head.** Only one person adds migrations at a time;
   `tests/db/test_migrations.py` fails on two heads.

## Layout

```
src/cedesk/
  main.py        app factory, health routes
  settings.py    CEDESK_* environment settings
  db.py          engine and workspace_session (sets the RLS workspace)
  tenancy.py     RLS statements for tenant tables
  helpdesk/
    api/         routers
    service/     use cases
    repo/        SQL
    domain/      pure logic (SLA, routing, rules, permissions)
migrations/      Alembic (owner role)
infra/postgres/  database roles for local and CI
tests/           unit/ (no DB) and db/ (cedesk_test)
```
