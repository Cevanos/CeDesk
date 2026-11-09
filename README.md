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
uv run cedesk openapi        # rewrite docs/api/openapi.json after an API change
uv run cedesk create-workspace --name "Acme Support" --slug acme \
    --admin-email you@example.com --admin-name "Your Name"   # asks for the password;
    # starts with the mock's teams (Billing, Shipping, Tech) and 5 ticket types; --no-seed for none
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

## Lists and concurrent edits

- **Cursor pages.** Tickets, a ticket's messages, notifications and members return
  `next_cursor`; send it back as `cursor`, with the same filters and `sort`, for the next
  page (`null` on the last page). `limit` is 1–200, default 50. A row added while paging
  never makes a row repeat.
- **Long tickets.** `GET /tickets/{id}` shows the latest 50 messages, oldest to newest,
  with `older_messages_cursor`; `GET /tickets/{id}/messages?cursor=…` loads older ones
  (newest first).
- **Ticket sort.** `sort=priority` (default: Open and Pending most urgent first, then
  oldest; Resolved last, newest first), `newest`, `oldest`, or `updated`.
- **If-Match.** Ticket responses carry an `ETag` (the ticket's `updated_at`). Send it as
  `If-Match` on `PATCH /tickets/{id}` or `POST /tickets/{id}/resolve`; if anyone changed
  the ticket since, the request gets 409 and nothing is saved. Without the header the
  last save wins.
- **Search.** `q` matches the ticket number (`T-1042` or `1042`), subject, field values,
  tags, and the customer's name and email, ignoring case. Subject, fields and tags are
  kept in one `search_text` column by a trigger.
- **Speed.** Each sort has its own index (migration 0009), so a page reads only the rows
  it returns, however deep. `uv run python scripts/bench_ticket_list.py` fills a new
  workspace in the dev database with 100k tickets and prints p50/p95 per list and search
  (target p95 under 300 ms; measured about 10–60 ms for lists, 120 ms at most for search).
  It leaves the `bench-…` workspace behind.

## Agents

- A new agent starts **Offline** and gets no tickets until they set themselves Available
  (`PUT /api/v1/me/status`) or an Admin does (`PATCH /api/v1/agents/{id}/routing`).
  Light agents have no status and are never routed.

## Events, logs and the API file

- **Outbox.** Every ticket change writes its events to `hd_outbox` in the same
  transaction: `ticket.created`, `ticket.updated` (with the changed fields),
  `ticket.resolved`, `message.created`, `assignment.changed` (`by` is null when routing
  chose). Payload: `{id, occurred_at, event, ticket: {id, number, subject, status,
  priority, team, topic}, ...}`, with ids only, never message text. A refused or failed
  change leaves no event. The worker that delivers them (webhooks, emails) comes with
  the job queue.
- **Logs.** `cedesk serve` writes one JSON line per request with `request_id` (yours, from
  `X-Request-ID`, if it looks like an id), `workspace_id`, `member_id`, `ticket_id`, the
  route template, status and ms. Never bodies, query strings, tokens or customer data.
- **OpenAPI.** `docs/api/openapi.json` is the API as served; `docs/api/CHANGELOG.md`
  lists what changed. After changing a route or schema run `uv run cedesk openapi` and
  add a changelog line; a test fails while the file is out of date.

## Files

One setting picks where uploaded files live, `CEDESK_STORAGE_DRIVER`:

- `local` (default): a folder, `CEDESK_STORAGE_LOCAL_PATH` (`storage/`, ignored by git).
  Back it up with the database.
- `s3`: AWS S3 or any S3-compatible service (MinIO, Cloudflare R2, Spaces, Wasabi, B2):
  `CEDESK_STORAGE_S3_BUCKET`, plus `..._ENDPOINT_URL` and `..._PATH_STYLE=true` for MinIO.
  Without keys it uses the server's AWS role. See `.env.example`.

A file is stored under `<workspace>/<ticket>/<file id>`, never under its own name. Downloads
use a link that works without signing in for 5 minutes (`CEDESK_DOWNLOAD_LINK_SECONDS`):
S3 presigns it, local disk signs it with `CEDESK_SECRET_KEY`. Files are capped at 25 MB
(`CEDESK_ATTACHMENT_MAX_BYTES`). `scan_status` is where a virus scanner plugs in; an
`infected` file is never handed out.

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
  storage/       where uploaded files live: local disk or S3 (one setting)
  helpdesk/
    api/         routers
    service/     use cases
    repo/        SQL
    domain/      pure logic (SLA, routing, rules, permissions)
migrations/      Alembic (owner role)
infra/postgres/  database roles for local and CI
tests/           unit/ (no DB) and db/ (cedesk_test)
```
