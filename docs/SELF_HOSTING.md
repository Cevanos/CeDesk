# Hosting CeDesk yourself

CeDesk runs on one Linux server with Docker: Postgres, the API, a worker and Caddy (which
gets the HTTPS certificate by itself). Nothing else is needed; Cevanos is optional. Every
setting is listed in [SETTINGS.md](SETTINGS.md).

A self-hosted CeDesk has every feature and no plan limits: limits only apply to a
workspace our operators give a cloud plan (`cedesk set-plan`), which you never do.

## What you need

- A Linux server with Docker and the compose plugin. A small one is enough to start (for
  example 2 CPUs and 4 GB of memory, plus 1.5 GB for the virus scan). Add more as the
  number of agents and tickets grows.
- A domain name pointing at the server (for HTTPS), e.g. `help.example.com`.
- An SMTP account to send email (Amazon SES, Gmail or Zoho with an app password, ...), and
  a way for customers' emails to reach CeDesk: a mailbox it reads, or a mail service that
  posts each email to it (see Email).
- The CeDesk frontend, served from its own address (e.g. `app.example.com`).

## Install

1. Firewall: allow only SSH, 80 and 443.

   ```sh
   ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
   ```

   Only Caddy publishes ports; the database and the API are not reachable from outside.

2. Get the code and the settings file:

   ```sh
   git clone https://github.com/Cogninest-AI/CDESK.git && cd CDESK/deploy
   cp env.example .env
   ```

3. Fill in `deploy/.env` (it holds secrets: never commit it, keep it readable by you only,
   `chmod 600 .env`):

   - `POSTGRES_PASSWORD`, `CEDESK_OWNER_PASSWORD`, `CEDESK_APP_PASSWORD`,
     `CEDESK_SECRET_KEY`: four different values from `openssl rand -hex 32`.
   - `CEDESK_ENV=production`.
   - `CEDESK_SITE=help.example.com` (your domain; Caddy gets its certificate).
   - `CEDESK_APP_URL=https://app.example.com` and
     `CEDESK_CORS_ORIGINS=https://app.example.com` (the frontend's address).
   - Email: see [Email](#email). Production refuses `CEDESK_MAIL_BACKEND=log`.

   **Keep a copy of `CEDESK_SECRET_KEY` somewhere safe and never change it.** It encrypts
   every member's two-step sign-in secret, app credentials, the Cevanos key, webhook
   secrets and import credentials. A lost or changed key locks out everyone who uses
   two-step sign-in and means entering every credential again.

4. Start:

   ```sh
   docker compose up -d --build
   ```

   This creates the database, runs the migrations (the `migrate` service), then starts the
   API, the worker and Caddy. Check it:

   ```sh
   curl https://help.example.com/readyz     # {"status":"ok","edition":"cloud"}
   ```

   (`edition` says `cloud` unless you add `CEDESK_EDITION` to the compose file; on your
   own server it changes nothing, see above.)

5. Your first workspace and its Admin (asks for the Admin's password twice):

   ```sh
   docker compose exec api uv run --no-sync cedesk create-workspace \
       --name "Acme Support" --slug acme \
       --admin-email you@example.com --admin-name "Your Name"
   ```

   `--slug` is the workspace address people type when they sign in. The workspace starts
   with three teams, five ticket types, four saved replies, six rules and three macros
   (from the mock); add `--no-seed` to start empty. Sign in on the frontend and add the
   rest in Settings.

6. Daily jobs (cron, as the user that runs Docker):

   ```sh
   30 2 * * * /path/to/CDESK/deploy/backup.sh >> /var/log/cedesk-backup.log 2>&1
   45 2 * * * cd /path/to/CDESK/deploy && docker compose exec -T api uv run --no-sync cedesk prune-sessions
   ```

   The worker does everything else by itself: sending email, reading the mailbox, SLA
   alerts, webhooks, imports and retention.

The API's own documentation, to try any route: `https://help.example.com/docs`.

## Settings the compose file doesn't pass

`deploy/docker-compose.yml` hands each service only the settings it lists (see the
"In compose" column of [SETTINGS.md](SETTINGS.md)). To use another one (S3 storage, a
longer download link, `CEDESK_EDITION`...), put it in `.env` **and** add it to the
`environment:` list of both `api` and `worker`, the same way as the others:

```yaml
      CEDESK_STORAGE_DRIVER: ${CEDESK_STORAGE_DRIVER:-local}
```

Then `docker compose up -d` again.

## Email

The worker sends every email (the API only queues them).

**Sending.** `CEDESK_MAIL_BACKEND=smtp` with `CEDESK_SMTP_HOST`, `CEDESK_SMTP_PORT` (587),
`CEDESK_SMTP_SECURITY` (`starttls`, or `ssl` for port 465), `CEDESK_SMTP_USERNAME` and
`CEDESK_SMTP_PASSWORD`. Emails go out from each workspace's support addresses (Settings >
Email); `CEDESK_MAIL_FROM_ADDRESS` is the sender of account emails (password reset, the
CSAT survey) when a workspace has none yet. The domain of every sending address needs SPF
and DKIM for your mail service, or emails land in spam: Settings > Email > Check DNS
reads them (set `CEDESK_MAIL_SPF_INCLUDE` to your service's SPF name, e.g.
`amazonses.com`, so it checks that one).

**Receiving.** Customers' emails become tickets (or join the ticket they answer) in the
workspace that owns the address they wrote to. Choose one:

- A mailbox: `CEDESK_IMAP_HOST`, `CEDESK_IMAP_USERNAME`, `CEDESK_IMAP_PASSWORD` (an app
  password), `CEDESK_IMAP_FOLDER`. Support addresses forward to it. Run one worker.
- A mail service webhook (Mailgun, SendGrid Inbound Parse): set
  `CEDESK_INBOUND_EMAIL_TOKEN` (random, 32+ characters) and point the service at
  `https://help.example.com/api/v1/inbound/email`. Mailgun: forward to the URL with the
  token as the password (`https://api:<token>@help.example.com/api/v1/inbound/email`);
  SendGrid: tick "POST the raw, full MIME message".

Set `CEDESK_MAIL_FORWARD_TO` to the address support addresses should forward to, so
Settings > Email can tell Admins. "Check forwarding" there proves an address arrives.
Auto-replies, bounces and mailing lists are ignored, so CeDesk never answers an
out-of-office forever.

## Files

Uploaded files live in one place for the API and the worker (they must share it).

| `CEDESK_STORAGE_DRIVER` | Where | Also set |
|---|---|---|
| `local` (default) | The `files` Docker volume | Nothing (back it up, see below) |
| `s3` | AWS S3, or MinIO, R2 and other S3-compatible services | `CEDESK_STORAGE_S3_BUCKET`, `_REGION`, keys (or the server's IAM role); `_ENDPOINT_URL` and `_PATH_STYLE=true` for MinIO |
| `gcs` | Google Cloud Storage | `CEDESK_STORAGE_GCS_BUCKET`, an HMAC key (`_HMAC_ACCESS_ID`, `_HMAC_SECRET`) |
| `azure` | Azure Blob Storage | `CEDESK_STORAGE_AZURE_ACCOUNT_NAME`, `_ACCOUNT_KEY`, `_CONTAINER` (create it first) |

Add these to both `api` and `worker` in the compose file (see above). A storage setting
that is missing stops the API at start-up, with the reason in `docker compose logs api`.

**Virus scan (optional).** `docker compose --profile scan up -d` starts ClamAV; set
`CEDESK_SCAN_BACKEND=clamd` and `docker compose up -d api worker`. Files can then be
downloaded or emailed only once found clean; infected ones are removed. Its first start
downloads the virus definitions (a few minutes).

## Cevanos (optional)

Without Cevanos, CeDesk has its own channels: email, the customer portal and web form,
and the inbound API. With it, a workspace also gets the Cevanos AI agent's handovers and
its chat channels. Each workspace connects itself: an Admin enters the Cevanos API key and
webhook secret in Settings > Cevanos, and in Cevanos adds a webhook to the address shown
there. `CEDESK_CEVANOS_URL` and `CEDESK_CEVANOS_APP_URL` only change if Cevanos gives you
other addresses.

## Single sign-on

An Admin turns it on in the single sign-on settings (SAML, `PUT /api/v1/sso-settings`;
the identity provider's setup reads `/api/v1/sso/<workspace>/metadata`). Set `CEDESK_API_URL`
to `https://help.example.com` so the identity provider sends people back to the right
address.

## Updating

```sh
cd /path/to/CDESK && git pull && cd deploy && docker compose up -d --build
```

Migrations run before the new API starts. The database (`pgdata`), files (`files`) and
certificates (`caddy_data`) survive. When an update brings new default saved replies,
rules or macros, add them to an existing workspace (safe to run again; nothing you deleted
comes back):

```sh
docker compose exec api uv run --no-sync cedesk seed-workflow --workspace acme
```

Read `docs/api/CHANGELOG.md` before updating: it lists what changed.

## Backups

Three things, and you need all three to restore:

1. **The database**: `deploy/backup.sh` (the cron line above) writes
   `~/cedesk-backups/cedesk-<date>-<HHMM>.sql.gz` every night and keeps 14 days
   (`BACKUP_DIR` changes the folder).
2. **The files**, with local storage (with S3, GCS or Azure, use the bucket's own
   versioning or backups):

   ```sh
   docker run --rm -v cedesk_files:/data:ro -v ~/cedesk-backups:/backup alpine \
       tar czf /backup/cedesk-files-$(date +%Y%m%d).tar.gz -C /data .
   ```

3. **`deploy/.env`**, above all `CEDESK_SECRET_KEY` (see Install). Keep it apart from the
   other two.

Copy the backups off the server.

**Restoring** on a new server: do Install steps 1 to 3 with the saved `.env`, then, from
`deploy/`:

```sh
docker compose up -d db
gunzip -c cedesk-<date>.sql.gz | docker compose exec -T db psql -U postgres -d cedesk -v ON_ERROR_STOP=1
docker run --rm -v cedesk_files:/data -v ~/cedesk-backups:/backup alpine \
    tar xzf /backup/cedesk-files-<date>.tar.gz -C /data
docker compose up -d --build
```

The database must be the new, empty one `docker compose up -d db` made (it creates the
roles first). The migrations then find it up to date.

## When something is wrong

| What | Do |
|---|---|
| The API doesn't start | `docker compose logs api`: it names the setting to fix (e.g. the dev secret key outside `dev`, `*` in CORS) |
| No emails go out | `docker compose logs worker`; Admins see the failed ones with Retry (`GET /api/v1/email/failed`) |
| An IP allowlist locked every Admin out | `docker compose exec api uv run --no-sync cedesk clear-ip-allowlist --workspace acme` |
| An Admin lost their 2FA phone | Another Admin resets it (`POST /api/v1/members/<id>/2fa/reset`) |
| Events (webhooks, Cevanos, alerts) failed | Admins see them with Retry (`GET /api/v1/outbox/failed`; webhooks also have their delivery log) |

## Licence

To be announced.
