# Reporting database: connect Metabase, Power BI or Looker

CeDesk keeps a `reporting` schema of read-only views (migration 0080). A BI tool reads
them with its own database login: numbers, states, times and ids, **no personal data**.
Logins are made by the server's operator with `cedesk reporting-user`.

## What a login can do

- Read the `reporting` views, and nothing else: no CeDesk table, no writing (its
  transactions are read-only).
- See one workspace (`--workspace acme`) or, on a self-hosted server, every workspace
  (`--all`).
- At most 5 connections; a query stops after 60 seconds.

## The views

| View | One row per | Columns |
|---|---|---|
| `tickets` | ticket | id, workspace_id, number, status, priority, channel, type_id, team_id, assignee_id, topic, sentiment, language, source, reopen_count, created_at, first_reply_at, resolved_at, updated_at, sla_due_at, sla_breach_alerted, snoozed, parent_id, merged_into_id |
| `messages` | message | id, workspace_id, ticket_id, kind (customer, agent, note, system, ...), author_member_id, created_at |
| `csat` | rating | id, workspace_id, ticket_id, score, source, rated_at |
| `members` | member | id, workspace_id, role, role_id, is_active, created_at |
| `teams` | team | id, workspace_id, name, created_at |
| `team_members` | team membership | workspace_id, team_id, member_id |
| `ticket_types` | ticket type | id, workspace_id, name |
| `workspaces` | workspace | workspace_id, name, time_zone, created_at |

Join on the ids: `tickets.team_id = teams.id`, `tickets.type_id = ticket_types.id`,
`tickets.assignee_id = members.id`, `messages.ticket_id = tickets.id`.

### Left out on purpose

| Left out | Why |
|---|---|
| Ticket subjects, message text, notes, CSAT comments | Customers write personal details in them. |
| Customer names, email addresses, phone numbers | Personal data. Count customers by `tickets` instead. |
| Member names and email addresses | Personal data. Show `members.id` or the role; map ids to names inside your BI tool if you need to. |
| Tags and custom fields | Free text an agent typed; it can hold personal data. |

## Make a login

The command needs the database superuser (the `postgres` user and `POSTGRES_PASSWORD`
from `deploy/.env`), for this command only. From `deploy/`:

```bash
read -rsp "postgres password: " PGPW; echo
export CEDESK_ADMIN_DATABASE_URL="postgresql://postgres:$PGPW@db:5432/cedesk"
docker compose exec -e CEDESK_ADMIN_DATABASE_URL api \
  uv run --no-sync cedesk reporting-user create report_bi --all
unset PGPW CEDESK_ADMIN_DATABASE_URL
```

- The name is `report_` and then 2-40 lowercase letters, digits or `_`.
- It asks for the login's password twice (16 characters or more). The password never
  reaches the database: only its SCRAM-SHA-256 hash is sent.
- `--workspace acme` instead of `--all` limits it to one workspace.
- `reporting-user list` shows every login; `reporting-user drop report_bi` removes one.

## After restoring a backup

Logins are server-wide roles, so a database backup doesn't hold them (`restore.sh`
makes the `cedesk_reporting` group first, so the restore works on a new server). On a
new server, `reporting-user list` still shows the old logins: `drop` each one and
`create` it again with a new password.

## Connect the BI tool

The database isn't reachable from outside the server (on purpose). Either:

1. **Run the BI tool next to CeDesk** (simplest), on the same Docker network. Example for
   Metabase, in a `docker-compose.override.yml` next to `docker-compose.yml`:

   ```yaml
   services:
     metabase:
       image: metabase/metabase:latest
       restart: unless-stopped
       ports: ["127.0.0.1:3000:3000"]
   ```

   Then in Metabase: *Add database* → PostgreSQL, host `db`, port `5432`, database
   `cedesk`, user `report_bi`, the password, schema `reporting`. Reach Metabase through
   an SSH tunnel (`ssh -L 3000:127.0.0.1:3000 you@server`) or put it behind Caddy with a
   sign-in.

2. **An SSH tunnel from your computer** (Power BI Desktop, Looker Studio connectors,
   DBeaver): publish the database on the server's loopback only
   (`ports: ["127.0.0.1:5432:5432"]` on `db` in an override file), then
   `ssh -L 5432:127.0.0.1:5432 you@server` and connect to `localhost:5432`, database
   `cedesk`, the `report_` login.

Never publish port 5432 to the internet.

## A first chart: tickets per day

```sql
SELECT date_trunc('day', created_at) AS day, count(*) AS tickets
FROM reporting.tickets
WHERE merged_into_id IS NULL
GROUP BY 1 ORDER BY 1;
```

## How it works

Every CeDesk table has row-level security bound to one workspace. Migration 0080 adds
one read-only policy per table the views read: a row is visible to the views when the
login is listed in `hd_reporting_access` for that row's workspace. The login itself has
no rights on any table, only on the views. The app's own connection is unchanged.
