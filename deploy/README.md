# Running CeDesk on a server

One server with Docker. Caddy answers on 80/443 and passes requests to the API; the
database and the API are not reachable from outside.

These are the notes for our own servers. For a customer hosting CeDesk themselves, see
[../docs/SELF_HOSTING.md](../docs/SELF_HOSTING.md); every setting is in
[../docs/SETTINGS.md](../docs/SETTINGS.md).

## First time

1. Server: Docker with the compose plugin, and a firewall that allows only SSH, 80 and 443:

   ```sh
   ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
   ```

   (Docker opens published ports itself, past ufw. Only Caddy publishes any here.)
2. Get the code: `git clone https://github.com/Cogninest-AI/CDESK.git && cd CDESK/deploy`
3. `cp env.example .env`, then fill in `.env`: four secrets from `openssl rand -hex 32`,
   `CEDESK_SITE` (a domain pointing at the server for HTTPS, or `:80`) and
   `CEDESK_CORS_ORIGINS` (the frontend's addresses).
4. Start: `docker compose up -d --build`. It creates the database, runs the migrations,
   then starts the API and Caddy. Check: `curl http://<server>/readyz` gives `{"status":"ok",...}`.
5. Demo workspace (an Admin, the agents and tickets from the mock):

   ```sh
   docker compose exec api uv run --no-sync cedesk seed-demo
   ```

   It asks for one password for all demo accounts and prints the sign-in emails
   (workspace `acme-store`).
6. Backups: see [Backups](#backups) below. Do it before real customer data arrives.

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

## Backups

`backup.sh` saves the database and the uploaded files every night, **encrypted for a key
that is not on the server**, so anyone who can read the backup folder (another user, a
leaked disk image) still can't read a customer's data. `restore.sh` checks a backup and
loads it into a **new** database next to the live one.

Set up once (as root on the server):

1. On a laptop (not the server), make the key pair and export the public half:
   `gpg --quick-gen-key "CeDesk backups" rsa4096 encrypt never`, then
   `gpg --export --armor <fingerprint> > cedesk-backup.pub`. Keep the private key
   (`gpg --export-secret-keys --armor <fingerprint>`) offline, in two places (e.g. the
   company password manager and an encrypted USB stick). Never copy it to the server.
2. On the server: `gpg --import cedesk-backup.pub`.
3. `cp backup.env.example backup.env && chmod 600 backup.env`; set
   `BACKUP_GPG_RECIPIENT` to the fingerprint (and `BACKUP_COMPOSE` if the server uses an
   extra compose file). Optional: `BACKUP_REQUIRE_ENCRYPTION=1`, so a missing key stops
   the backup instead of writing an unencrypted one.
4. Cron: `30 2 * * * /path/to/CDESK/deploy/backup.sh >> /var/log/cedesk-backup.log 2>&1`.
5. Run it once by hand and check `ls -l` on the folder: `drwx------` and every file
   `-rw-------`, names ending `.gpg`.

Each night writes one set: `cedesk-<time>.manifest` plus `-db.sql.gz.gpg` and
`-files.tar.gz.gpg`. The manifest holds each file's checksum. Sets older than
`BACKUP_KEEP_DAYS` (14) are deleted, but the newest `BACKUP_KEEP_MIN` (7) are always kept.
Backups made by the old script (`cedesk-*.sql.gz`, unencrypted) are never touched: delete
them by hand once a new encrypted backup has been restored successfully (below).

Exit codes: 0 done; 3 done but **not encrypted** (no key set; files are 0600, the alert
fires); anything else failed and wrote nothing. Set `BACKUP_ALERT_URL` (a Slack or other
webhook) to be told; the message names the step that failed, never any data.

Restore (test it monthly, and before deleting old backups):

```sh
# on a machine that has the private key (or import it for the restore, then remove it)
./restore.sh /root/cedesk-backups/cedesk-<time>.manifest --files-to /tmp/cedesk-files-check
```

It checks the checksums (a changed or broken file is refused), decrypts, and loads the
database into `cedesk_restore_<time>`, leaving `cedesk` alone. Look at it
(`docker compose exec db psql -U postgres -d cedesk_restore_<time>`), then drop it.
Replacing the live database needs `--db cedesk --force`, with the API and worker stopped,
right after a fresh backup.

## Email

The `worker` service sends the emails the API queues (replies on email tickets, test
emails). Choose how in `deploy/.env`:

- `CEDESK_MAIL_BACKEND=none` (the default here): emails are dropped. The log says how many,
  never to whom.
- `CEDESK_MAIL_BACKEND=smtp` with `CEDESK_SMTP_HOST`, `CEDESK_SMTP_PORT` (587),
  `CEDESK_SMTP_SECURITY` (`starttls`, or `ssl` for port 465), `CEDESK_SMTP_USERNAME`
  and `CEDESK_SMTP_PASSWORD`: any SMTP server, e.g. Amazon SES
  (`email-smtp.<region>.amazonaws.com`), or Gmail / Zoho with an app password.
- `CEDESK_MAIL_BACKEND=log`: a developer's machine only (`CEDESK_ENV=dev`). Each email is
  written as a private .eml file to `CEDESK_MAIL_OUTBOX_DIR`; the log gets only the file name.
  On a server the worker refuses it: emails carry sign-in codes and reset links.

The worker also delivers outbox events (what happened on tickets) to the handlers that
need them, retrying for about 15 hours; Admins see the ones that still failed at
`GET /api/v1/outbox/failed`. And it removes `Idempotency-Key`s older than a day.

Virus scan: on by default (`CEDESK_SCAN_BACKEND=clamd`; the `clamav` service starts
with the others). ClamAV needs about 1.5 GB of memory (3 GB for a minute while it loads
new definitions), and its first start downloads the virus definitions (a few minutes;
until then files wait as "being checked"). The worker checks each new file; infected
ones are removed. Files uploaded before scanning was on are checked too, so after
switching it on old files wait until the worker reaches them; nothing is deleted unless
ClamAV finds a virus. `docker compose ps clamav` shows `healthy` once it answers.

Switching it on, on a server that ran without it (in this order, so downloads never
wait on a scanner that is not there yet):

1. `docker compose up -d clamav`, then wait for `docker compose ps clamav` to say
   `healthy` (up to 10 minutes the first time).
2. Set `CEDESK_SCAN_BACKEND=clamd` in `deploy/.env` (or remove a `none` line).
3. `docker compose up -d api worker`.
4. Check: upload the EICAR test file (a harmless file every scanner reports, from
   https://www.eicar.org) to a test ticket; within a minute it shows as blocked, and a
   normal file shows as clean.

To undo: set `CEDESK_SCAN_BACKEND=none` and `docker compose up -d api worker`; files
already marked infected stay blocked. A server too small for ClamAV keeps `none` and
runs `docker compose stop clamav`; the worker then logs a warning at every start.

Cevanos: `CEDESK_CEVANOS_URL` is Cevanos's API address (default
`https://api.cevanos.ai`), and `CEDESK_CEVANOS_APP_URL` its web app (default
`https://cevanos.ai`), which Reports links to for the Governance report. Each workspace
connects itself: an Admin saves the Cevanos API
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

The CSAT survey (emailed by the worker when a ticket is resolved) links to
`CEDESK_PORTAL_URL/rate`, or `CEDESK_APP_URL/rate` when that is empty, and comes from the
same sender as password resets. Admins turn it off, and turn the public web form on, in
Settings › Portal (`/portal-settings`).

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

Checking a support address (Settings › Email): "Check forwarding" emails the address a
one-time code; when it comes back in (by the mailbox or the webhook above) the address
shows as verified, with no ticket made. Set `CEDESK_MAIL_FORWARD_TO` to the address
Admins should forward to, so the page can say it. "Check DNS" reads the domain's SPF,
DKIM (at the selector the Admin enters) and DMARC records; set `CEDESK_MAIL_SPF_INCLUDE`
to your mail service's SPF name so it checks SPF names it.

SLA breach alerts: the worker checks once a minute and notifies the assignee and the
Supervisors of the ticket's team when a ticket's SLA runs out. Nothing to set.

Then an Admin adds the support address (Settings › Email, or `POST /api/v1/email-addresses`)
and uses "Send test" to check it arrives. For real sending the address's domain needs
SPF and DKIM set up for the mail service, or emails land in spam.

## Custom help-center domains

Off by default. An Admin can point a domain they own (say `help.example.com`) at their help
center: Settings registers it, shows a TXT record to publish, and "Check" proves control.
Only a **verified** domain is served. Nothing here changes unless you turn it on.

What it needs, because the pages are the frontend's, not the API's:

1. **A key.** Set `CEDESK_TLS_ASK_KEY` (24+ random characters, `openssl rand -hex 24`) in
   `deploy/.env`. With it empty the `tls-ask` route does not exist and no certificate is ever
   requested. Optionally set `CEDESK_HELP_CNAME_TARGET`: the host name customers point their
   domain at, shown to the Admin.
2. **Caddy in front of the frontend, with on-demand TLS.** In `deploy/Caddyfile` there are two
   commented-out blocks: the global `on_demand_tls` block (it has to be the first block of the
   file, so it is at the top) and an `https://` site block at the bottom. Un-comment both, put
   the same key in the `ask` URL, and set `CEDESK_FRONTEND_UPSTREAM` (for example
   `frontend:3000`). Caddy then asks the API
   `GET /api/v1/internal/tls-ask?domain=<host>&key=<key>` the first time a host connects, and
   gets a certificate only when the API says 200: the domain is verified, one per workspace.
3. **Block `/api/v1/internal/` at any public proxy.** The `ask` call is meant for the network
   between Caddy and the API. The key is in the URL, so it can show in proxy access logs;
   treat it as defence in depth, not the only barrier. (The staging server's nginx is set up
   outside this repo: add `location /api/v1/internal/ { return 404; }` there.)
4. **The frontend maps the host to a workspace.** It calls `GET /api/v1/public-hosts/<host>`
   (server side, cached) to find the workspace slug, then serves `/<slug>/help/...`.

DNS records the Admin adds: the TXT record shown on the domain screen (proves control), and a
CNAME from the domain to your platform host. The worker re-reads every verified domain's TXT
record daily and turns a domain back to pending after three failed daily checks in a row, so
a domain the customer let go stops being served. (The Admin pressing Check never counts toward
that.)

Test it once on a real domain and a real server before telling customers: the API's own
tests check the rules and the routes, not a certificate being issued.

## What the API refuses

With `CEDESK_ENV` other than `dev`, the API does not start with the dev secret key or the
dev database password, and `cedesk migrate` refuses the dev owner password. The worker
also refuses `CEDESK_MAIL_BACKEND=log` anywhere but `dev` (emails carry sign-in codes and
reset links), and `smtp` without a host. No log line holds an email address, subject or body.

## For the frontend team

- API docs to try every route: `https://<server>/docs`; the file: `docs/api/openapi.json`;
  changes: `docs/api/CHANGELOG.md`.
- Sign-in and password change allow 20 attempts per 5 minutes per address, then 429 with
  `Retry-After`.
