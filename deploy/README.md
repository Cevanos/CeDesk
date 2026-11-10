# Running CeDesk on a server

One server with Docker. Caddy answers on 80/443 and passes requests to the API; the
database and the API are not reachable from outside.

## First time

1. Server: Docker with the compose plugin, and a firewall that allows only SSH, 80 and 443:

   ```sh
   ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
   ```

   (Docker opens published ports itself, past ufw. Only Caddy publishes any here.)
2. Get the code: `git clone git@github.com:Cogninest-AI/CDESK.git && cd CDESK/deploy`
3. `cp env.example .env`, then fill in `.env`: four secrets from `openssl rand -hex 32`,
   `CEDESK_SITE` (a domain pointing at the server for HTTPS, or `:80`) and
   `CEDESK_CORS_ORIGINS` (the frontend's addresses).
4. Start: `docker compose up -d --build`. It creates the database, runs the migrations,
   then starts the API and Caddy. Check: `curl http://<server>/readyz` gives `{"status":"ok",...}`.
5. Demo workspace (Ritesh as Admin, the agents and tickets from the mock):

   ```sh
   docker compose exec api uv run --no-sync cedesk seed-demo
   ```

   It asks for one password for all demo accounts and prints the sign-in emails
   (workspace `acme-store`).
6. Backups: add `deploy/backup.sh` to cron (the line is at the top of the script).

## Updating

```sh
cd CDESK && git pull && cd deploy && docker compose up -d --build
```

Migrations run before the new API starts. Uploaded files (`files` volume), the database
(`pgdata`) and the HTTPS certificates (`caddy_data`) survive updates.

When an update brings new defaults (saved replies, rules and macros), add them to a
workspace made before it. Safe to run again: anything already there is kept, a default an
Admin deleted never comes back, and a default rule or macro whose team or ticket type is
missing is left out until it exists:

```sh
docker compose exec api uv run --no-sync cedesk seed-workflow --workspace acme-store
```

The worker works out the SLA of tickets that have none stored yet (e.g. from before
migration 0019) each time it starts, so "sort by time left" is right after every deploy.
To do it by hand (safe to run again):

```sh
docker compose exec api uv run --no-sync cedesk sla-refresh
```

## Email

The `worker` service sends the emails the API queues (replies on email tickets, test
emails). Choose how in `deploy/.env`:

- `CEDESK_MAIL_BACKEND=log` (the default): nothing is sent; each email is written to the
  worker's log (`docker compose logs worker`). For the test server only.
- `CEDESK_MAIL_BACKEND=smtp` with `CEDESK_SMTP_HOST`, `CEDESK_SMTP_PORT` (587),
  `CEDESK_SMTP_SECURITY` (`starttls`, or `ssl` for port 465), `CEDESK_SMTP_USERNAME`
  and `CEDESK_SMTP_PASSWORD`: any SMTP server, e.g. Amazon SES
  (`email-smtp.<region>.amazonaws.com`), or Gmail / Zoho with an app password.
- `CEDESK_MAIL_BACKEND=none`: emails are dropped.

The worker also delivers outbox events (what happened on tickets) to the handlers that
need them, retrying for about 15 hours; Admins see the ones that still failed at
`GET /api/v1/outbox/failed`. And it removes `Idempotency-Key`s older than a day.

Virus scan: start ClamAV with `docker compose --profile scan up -d` and set
`CEDESK_SCAN_BACKEND=clamd` in `deploy/.env` (then `docker compose up -d api worker`).
ClamAV needs about 1.5 GB of memory, and its first start downloads the virus definitions
(a few minutes; until then files wait as "being checked"). The worker checks each new
file; infected ones are removed. Files uploaded before scanning was on are checked too.

Cevanos: `CEDESK_CEVANOS_URL` is Cevanos's API address (default
`https://api.cevanos.ai`). Each workspace connects itself: an Admin saves the Cevanos API
key (scopes `conversations:read` and `conversations:write`) and the webhook signing
secret at `PUT /api/v1/cevanos/connection`, and in Cevanos adds a webhook to
`https://<server>/api/v1/cevanos/events/<workspace id>` (the exact path is in the
connection's `webhook_path`) for `conversation.escalated`, `conversation.classified`,
`conversation.reopened` and `conversation.closed`. The worker then reads open chats every
few seconds (within the key's 60-a-minute limit) and sends take-overs and replies. The
secrets are encrypted with `CEDESK_SECRET_KEY`: if that key changes, enter them again.

The worker reads the files sent with replies, and keeps the files that come in customer
emails, so it uses the same file storage as the api (the `files` volume, already set in
`docker-compose.yml`). With S3, give the worker the same `CEDESK_STORAGE_*` settings.

Password reset emails ("Forgot password?") come from the workspace's default support
address, else from `CEDESK_MAIL_FROM_ADDRESS`. Set `CEDESK_APP_URL` to the agent app's
address, or the links in them won't open the app.

Receiving: customers' emails become tickets (or join the ticket they answer) in the
workspace that owns the address they wrote to. Choose one way:

- **A mailbox** (simplest): set `CEDESK_IMAP_HOST` (e.g. `imap.gmail.com`), `_USERNAME`,
  `_PASSWORD` (an app password) and `_FOLDER`. The worker reads unread emails every few
  seconds and marks them read once handled. Run one worker when a mailbox is set.
- **A mail service webhook**: set `CEDESK_INBOUND_EMAIL_TOKEN` (random, 32+ characters)
  and point the service at `https://<server>/api/v1/inbound/email`:
  Mailgun "forward" to the URL ending in `mime` (put the token as the password:
  `https://api:<token>@<server>/api/v1/inbound/email`), or SendGrid Inbound Parse with
  "POST the raw, full MIME message" ticked. Raw emails can also be posted as
  `message/rfc822` with `Authorization: Bearer <token>`.

Auto-replies, bounces, mailing-list emails and emails from our own support addresses are
ignored, so CeDesk never answers an out-of-office forever.

Then an Admin adds the support address (Settings › Email, or `POST /api/v1/email-addresses`)
and uses "Send test" to check it arrives. For real sending the address's domain needs
SPF and DKIM set up for the mail service, or emails land in spam.

## What the API refuses

With `CEDESK_ENV` other than `dev`, the API does not start with the dev secret key or the
dev database password, and `cedesk migrate` refuses the dev owner password. The worker
also refuses `CEDESK_MAIL_BACKEND=log` in production (emails will carry sign-in codes),
and `smtp` without a host.

## For the frontend team

- API docs to try every route: `https://<server>/docs`; the file: `docs/api/openapi.json`;
  changes: `docs/api/CHANGELOG.md`.
- Sign-in and password change allow 20 attempts per 5 minutes per address, then 429 with
  `Retry-After`.
