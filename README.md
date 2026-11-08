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
uv run cedesk prune-sessions # delete sign-in sessions that ended over 7 days ago (run daily)
uv run cedesk create-workspace --name "Acme Support" --slug acme \
    --admin-email you@example.com --admin-name "Your Name"   # asks for the password
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
5. **Lock order.** Member changes lock every active Admin and the target member in one
   id-ordered statement.
   Routing takes the workspace's routing lock, then the team row. New code that locks
   several of these takes them in the same order.

## Errors

Every error, whatever its status, has one shape:

```json
{"error": {"code": "Invalid", "message": "Some fields are not valid.",
           "details": [{"field": "contact.email", "location": "body",
                        "message": "String should match pattern ..."}]}}
```

| Status | `code` | When |
|---|---|---|
| 401 | `NotSignedIn` | No token, or the session ended (also sends `WWW-Authenticate: Bearer`) |
| 403 | `Forbidden` | The role can't do this |
| 404 | `NotFound` | Missing, or in another workspace (never revealed) |
| 405 | `MethodNotAllowed` | Wrong HTTP method |
| 409 | `Conflict` | Clashes with the current state, or lost a race with another request; retry |
| 422 | `Invalid` | Bad input. `details` lists each field for request-shape errors; never the submitted values |
| 500 | `Internal` | Unexpected; details are logged, not returned |

`details` appears only on request validation errors.

## Standalone, and how Cevanos connects

CeDesk is its own service with its own database, sign-in and deployment. It never imports
Cevanos code, and it runs fully without Cevanos.

When a workspace connects Cevanos (not built yet), the link is one interface,
`helpdesk/service/cevanos.py` (`CevanosConnector`), over Cevanos's public API only:

- **In:** Cevanos's signed webhooks (for example `conversation.escalated` for an AI handover)
  arrive at a CeDesk endpoint; the signature is checked before anything acts.
- **Out:** CeDesk calls Cevanos's `/v1` API with the workspace's Cevanos API key
  (read the chat, take over, reply). The key is stored encrypted, never in config or logs.
- **Off:** with no key configured the connector does nothing; Cevanos features are hidden.

AI (summaries, writing help, translation, the AI agent) lives only in Cevanos.

## Local development passwords

The passwords in `docker-compose.yml`, `.env.example` and `infra/postgres/init/` are for
local development and CI only. Production roles and passwords are created separately and
supplied through `CEDESK_*` environment variables.

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
