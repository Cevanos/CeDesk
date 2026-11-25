# CeDesk settings

Every setting CeDesk reads, from `src/cedesk/settings.py`. Each is an environment
variable (or a line in `.env`) named `CEDESK_` plus the setting's name. An empty value
counts as not set. How to run CeDesk on a server: [SELF_HOSTING.md](SELF_HOSTING.md).

**Read by**: `api` (the web API), `worker` (`cedesk worker`: email, imports, alerts,
retention...), `migrate` (`cedesk migrate`), or `cli` (the other `cedesk` commands).
**In compose**: whether `deploy/docker-compose.yml` passes it from `deploy/.env` (and to
which service). A setting marked "no" needs adding to the `environment:` list of the
services that read it (see SELF_HOSTING.md, "Settings the compose file doesn't pass").
`tests/unit/test_settings_reference.py` fails when a setting is missing here, or when this
file or `deploy/env.example` names one that doesn't exist.

## Running mode and secrets

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_ENV` | `dev` | `dev` (a developer's machine), `staging` (a shared test server) or `production`. Anything but `dev` refuses to start with the dev secret key or dev database password, `*` in CORS, or Cevanos over plain http; anything but `dev` also refuses `MAIL_BACKEND=log`; `production` also turns off test-only routes (simulated customer replies; demo data, even with `CEDESK_DEMO_ENABLED`). | all | migrate, api, worker |
| `CEDESK_SECRET_KEY` | a dev-only value | Encrypts stored secrets (two-step sign-in, app credentials, the Cevanos key, webhook secrets, import credentials). Random, 32+ characters (`openssl rand -hex 32`). **Back it up; never change it**: a new key locks out everyone using two-step sign-in. | api, worker | api, worker |
| `CEDESK_EDITION` | `cloud` | `cloud` or `self_hosted`. Only switches the cloud plan limits on: they apply just to a workspace given a plan with `cedesk set-plan`. `/readyz` shows it. `deploy/` sets `self_hosted`; our own cloud servers set `cloud` in `deploy/.env`. | api, worker, cli | api, worker |
| `CEDESK_DEMO_ENABLED` | `false` | Turns on the demo data routes (`/demo/seed`, `/demo/samples`, `/demo/reset`) for Admins. Never on `production`, even when set. | api | api |
| `CEDESK_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. | api, worker, cli | no |

## Database

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_DATABASE_URL` | `postgresql+asyncpg://cedesk_app:cedesk_app_dev@localhost:5447/cedesk` | The app's connection, as `cedesk_app` (owns nothing, can't bypass row-level security). | api, worker, cli | set by compose from `CEDESK_APP_PASSWORD` |
| `CEDESK_MIGRATION_DATABASE_URL` | `postgresql+asyncpg://cedesk_owner:cedesk_owner_dev@localhost:5447/cedesk` | The migrations' connection, as `cedesk_owner` (owns the tables). | migrate | set by compose from `CEDESK_OWNER_PASSWORD` |

## Addresses

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_APP_URL` | `http://localhost:3000` | The agent app (frontend). Password reset links, the return from single sign-on (`<this>/sso/callback`), and Jira issues and Slack alerts linking to a ticket open it. | api, worker | api, worker |
| `CEDESK_PORTAL_URL` | (empty: `CEDESK_APP_URL`) | The customer portal. The CSAT survey email links to `<this>/rate`; the "an agent replied" email links to the ticket there. | worker | worker |
| `CEDESK_TLS_ASK_KEY` | (empty: custom help-center domains off) | Turns on custom help-center domains (plan 4.2). Caddy's on-demand TLS calls `/api/v1/internal/tls-ask?domain=...&key=<this>` before it gets a certificate; the route answers 200 only for a domain an Admin has verified. Empty: the route does not exist and no certificate is requested. 24+ random characters; never reuse another secret. See `deploy/README.md`. | api | api |
| `CEDESK_HELP_CNAME_TARGET` | (empty: none shown) | The host name customers point their help-center domain at (a CNAME), shown on the domain screen next to the TXT record. | api | api |
| `CEDESK_API_URL` | (empty: the address requests come in on) | This API's public address, for single sign-on: the identity provider sends people back to `<this>/api/v1/sso/<workspace>/acs`. | api | api |
| `CEDESK_CORS_ORIGINS` | (empty: none) | Sites whose pages may call the API from a browser, comma-separated, e.g. `https://app.example.com`. Not `*`. | api | api |

## Email out

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_MAIL_BACKEND` | `log` | `smtp` (send), `none` (drop them; the log says how many, never to whom) or `log` (a developer's machine only: each email is written as a private .eml file to `CEDESK_MAIL_OUTBOX_DIR`, the log gets the file name; refused unless `CEDESK_ENV=dev`). | worker | worker |
| `CEDESK_MAIL_OUTBOX_DIR` | `mail-outbox` | With `MAIL_BACKEND=log`: where the .eml files go (folder 0700, files 0600). | worker | no |
| `CEDESK_MAIL_FROM_ADDRESS` | (empty) | Sender of account emails (password reset, portal sign-in codes, the CSAT survey, portal reply notices) when a workspace has no support address yet. Empty: those can't be sent. | api, worker | api, worker |
| `CEDESK_MAIL_FROM_NAME` | `CeDesk` | The name shown with that sender. | api, worker | no |
| `CEDESK_SMTP_HOST` | (empty) | The SMTP server, e.g. `email-smtp.ap-south-1.amazonaws.com`. Needed with `smtp`. | worker | worker |
| `CEDESK_SMTP_PORT` | `587` | Its port. | worker | worker |
| `CEDESK_SMTP_SECURITY` | `starttls` | `starttls` (usually 587), `ssl` (usually 465) or `none` (a local test server only). | worker | worker |
| `CEDESK_SMTP_USERNAME` | (none) | SMTP user. | worker | worker |
| `CEDESK_SMTP_PASSWORD` | (none) | SMTP password (an app password for Gmail or Zoho). | worker | worker |
| `CEDESK_SMTP_TIMEOUT_SECONDS` | `30` | Give up on the SMTP server after this long (up to 300); the email is tried again later. | worker | no |

## Email in

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_IMAP_HOST` | (empty: no mailbox) | A mailbox the worker reads every few seconds; each email becomes a ticket or joins one. Run one worker when set. | worker | worker |
| `CEDESK_IMAP_PORT` | `993` | Its port (TLS). | worker | worker |
| `CEDESK_IMAP_USERNAME` | (none) | Mailbox user. Needed with a host. | worker | worker |
| `CEDESK_IMAP_PASSWORD` | (none) | Mailbox password (an app password). Needed with a host. | worker | worker |
| `CEDESK_IMAP_FOLDER` | `INBOX` | The folder read. | worker | worker |
| `CEDESK_INBOUND_EMAIL_TOKEN` | (none: the route answers 404) | Lets a mail service (Mailgun, SendGrid) post emails to `/api/v1/inbound/email`. Random, 32+ characters. | api | api |
| `CEDESK_INBOUND_EMAIL_MAX_BYTES` | `31457280` (30 MB) | The largest email accepted there. | api | no |
| `CEDESK_MAIL_FORWARD_TO` | (empty: not shown) | Where support addresses should forward to, shown to Admins in Settings > Email. | api | api |
| `CEDESK_MAIL_SPF_INCLUDE` | (empty: any SPF) | Your mail service's SPF name (e.g. `amazonses.com`), which "Check DNS" looks for. | api | api |

## Files

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_STORAGE_DRIVER` | `local` | Where uploaded files live: `local`, `s3`, `gcs` or `azure`. A missing setting for the chosen driver stops the API at start-up. | api, worker | no |
| `CEDESK_STORAGE_LOCAL_PATH` | `storage` | `local`: the folder. | api, worker | set by compose (`/data/storage`, the `files` volume) |
| `CEDESK_STORAGE_S3_BUCKET` | (empty) | `s3`: the bucket. | api, worker | no |
| `CEDESK_STORAGE_S3_REGION` | (none) | `s3`: its region. | api, worker | no |
| `CEDESK_STORAGE_S3_ENDPOINT_URL` | (none: AWS) | `s3`: another S3-compatible service, e.g. `http://localhost:9000` for MinIO. | api, worker | no |
| `CEDESK_STORAGE_S3_ACCESS_KEY_ID` | (none: the server's IAM role or `AWS_*` variables) | `s3`: access key. | api, worker | no |
| `CEDESK_STORAGE_S3_SECRET_ACCESS_KEY` | (none) | `s3`: its secret. | api, worker | no |
| `CEDESK_STORAGE_S3_PATH_STYLE` | `false` | `s3`: path-style addresses (MinIO and some others need `true`). | api, worker | no |
| `CEDESK_STORAGE_GCS_BUCKET` | (empty) | `gcs`: the bucket. | api, worker | no |
| `CEDESK_STORAGE_GCS_HMAC_ACCESS_ID` | (none) | `gcs`: an HMAC key's access id (Cloud Storage > Settings > Interoperability). | api, worker | no |
| `CEDESK_STORAGE_GCS_HMAC_SECRET` | (none) | `gcs`: its secret. | api, worker | no |
| `CEDESK_STORAGE_AZURE_ACCOUNT_NAME` | (empty) | `azure`: the storage account. | api, worker | no |
| `CEDESK_STORAGE_AZURE_ACCOUNT_KEY` | (none) | `azure`: its key. | api, worker | no |
| `CEDESK_STORAGE_AZURE_CONTAINER` | (empty) | `azure`: a container that already exists. | api, worker | no |
| `CEDESK_STORAGE_AZURE_ENDPOINT` | (none: Azure) | `azure`: another endpoint, e.g. Azurite's. | api, worker | no |
| `CEDESK_ATTACHMENT_MAX_BYTES` | `26214400` (25 MB) | The largest file an agent or customer can attach (and keep from an email). | api, worker | no |
| `CEDESK_DOWNLOAD_LINK_SECONDS` | `300` | How long a download link works (10 seconds to 7 days). | api | no |

## Virus scan

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_SCAN_BACKEND` | `none` | `clamd`: the worker checks every new file with ClamAV; a file can be downloaded or emailed only once found clean, and infected ones are removed. `none`: files aren't checked. The app's own default is `none` (a developer's machine); `deploy/docker-compose.yml` sets `clamd` and starts ClamAV. | api, worker | api, worker |
| `CEDESK_CLAMD_HOST` | (empty) | ClamAV's address. Needed with `clamd`. | worker | worker (`clamav`) |
| `CEDESK_CLAMD_PORT` | `3310` | Its port. | worker | no |
| `CEDESK_CLAMD_TIMEOUT_SECONDS` | `60` | Give up on one check after this long. | worker | no |
| `CEDESK_CLAMD_MAX_BYTES` | `104857600` (100 MB) | Keep equal to ClamAV's `StreamMaxLength`. Bigger files are marked "could not be checked" (blocked). | worker | worker |

## Cevanos

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_CEVANOS_URL` | `https://api.cevanos.ai` | Cevanos's API. Each workspace enters its own key in Settings > Cevanos; without one, Cevanos is off for it. | api, worker | api, worker |
| `CEDESK_CEVANOS_APP_URL` | `https://cevanos.ai` | Cevanos's web app, for links into it (the Governance report). | api | api |
| `CEDESK_CEVANOS_POLL_BATCH` | `20` | Chats read per worker pass, per workspace (1 to 60, within Cevanos's rate limit). | worker | no |

## Limits per address (abuse protection)

The address these limits (and each workspace's IP allowlist) count is the visitor's,
as the proxy in front reports it in `X-Forwarded-For`. `deploy/docker-compose.yml` trusts
that header only from `CEDESK_FORWARDED_ALLOW_IPS` (default: this machine and Docker's
private networks; never `*`, which lets a client choose its own address). It is a
uvicorn flag set by compose, not an app setting.

Over a limit: 429 with `Retry-After`. Counted in each API process.

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_SIGN_IN_LIMIT` | `20` | Sign-in and password-change attempts per IP per window. | api | no |
| `CEDESK_SIGN_IN_WINDOW_SECONDS` | `300` | That window. | api | no |
| `CEDESK_FORM_LIMIT` | `10` | Public web form posts per IP per window. | api | no |
| `CEDESK_FORM_WINDOW_SECONDS` | `600` | That window. | api | no |
| `CEDESK_VOTE_LIMIT` | `60` | Help article votes per IP per window. | api | no |
| `CEDESK_VOTE_WINDOW_SECONDS` | `600` | That window. | api | no |
| `CEDESK_PUBLIC_LIMIT` | `120` | Other public pages with no sign-in (help center, CSAT rating page) per IP per window. | api | no |
| `CEDESK_PUBLIC_WINDOW_SECONDS` | `600` | That window. | api | no |
| `CEDESK_API_KEY_LIMIT` | `700` | Requests a minute per workspace with API keys, all its keys together. | api | no |
| `CEDESK_UNSUBSCRIBE_LIMIT` | `600` | Public campaign unsubscribe and open-image requests per IP per window (high: mail providers fetch them from shared addresses). | api | no |
| `CEDESK_UNSUBSCRIBE_WINDOW_SECONDS` | `600` | That window. | api | no |

## Email campaigns

Marketing email to a segment (feature-gap plan phase 6). Only campaigns read the
suppression list; ticket replies and other transactional email are never blocked by it.
The unsubscribe link in each email's footer points at `CEDESK_PORTAL_URL` (else
`CEDESK_APP_URL`), the one-click and open-image links at `CEDESK_API_URL` (else the
same); outside `dev` they must be https or nothing can be sent. Bounces and spam reports
come back through email in (IMAP or the inbound webhook) to the sending support address.

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_CAMPAIGN_MAX_RECIPIENTS` | `5000` | The most recipients one campaign may have; a bigger one is refused before sending. | api, worker | no |
| `CEDESK_CAMPAIGN_DAILY_CAP` | `10000` | Campaign emails per workspace per 24 hours (sent plus still queued); a campaign that would go over is refused. | api, worker | no |
| `CEDESK_CAMPAIGN_PER_MINUTE` | `60` | Campaign emails the worker sends a minute per workspace. | worker | no |

## Self-service sign-up

Anyone can create a workspace from the sign-in page: a form, then a 6-digit code emailed
to them (10 minutes, 5 tries), then they sign in. Nothing is created before the code.
The worker sends the codes on a lane of their own, beside its other jobs, within about
a second. Off by default. On (outside `dev`), the API refuses to start without
`CEDESK_MAIL_FROM_ADDRESS` and, on the cloud edition, without a CAPTCHA; the worker
refuses without `CEDESK_MAIL_BACKEND=smtp`. Per email: 3 code emails an hour from one
address (10 from all addresses together), and one waiting sign-up per address. The per-address limits below are kept in the database, so they hold across API
processes.

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_SIGNUP_ENABLED` | `false` | Turns self-service sign-up on. Off: `GET /auth/signup-config` says so and the other sign-up routes answer 404. | api, worker | api, worker |
| `CEDESK_SIGNUP_DEFAULT_PLAN` | `free` | The cloud plan a new workspace starts on (one of `CEDESK_PLANS`; a name that isn't stops the API at start-up). Ignored on `self_hosted`. Empty: no plan. | api | api |
| `CEDESK_SIGNUP_CAPTCHA_SITE_KEY` | (empty) | Cloudflare Turnstile site key the sign-up page shows. Set it with the secret, or neither. | api | api |
| `CEDESK_SIGNUP_CAPTCHA_SECRET` | (empty: no CAPTCHA) | Cloudflare Turnstile secret each sign-up is checked with. Required on the cloud edition outside `dev`. Secret: never logged. | api | api |
| `CEDESK_SIGNUP_PER_IP_HOUR` | `5` | Sign-ups started per IP per hour. | api | api |
| `CEDESK_SIGNUP_WORKSPACES_PER_IP_DAY` | `3` | Workspaces created by sign-up per IP per day (checked when the code is verified). | api | api |
| `CEDESK_SIGNUP_MAIL_CONCURRENCY` | `10` | Code emails the worker sends at once (1 to 50); keep it under the mail provider's rate. | worker | worker |
| `CEDESK_SIGNUP_HASH_SLOTS` | `4` | Sign-up passwords hashed at once per API process (1 to 64). Each Argon2 hash uses about 64 MB, so this caps the memory a burst of sign-ups can take. | api | api |

## Reports

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_REPORT_TIMEOUT_SECONDS` | `10` | A report query (custom reports, built-in trends and breakdowns, the live view, dashboard cards) that runs longer than this many seconds (1 to 120) is cancelled and the answer asks to narrow the range or grouping. Scheduled reports use `CEDESK_REPORT_SCHEDULE_TIMEOUT_SECONDS`. | api | api |
| `CEDESK_REPORT_SCHEDULE_TIMEOUT_SECONDS` | `5` | A scheduled report's query that runs longer than this many seconds (1 to 60) is cancelled; the schedule shows why and goes out at its next time. Lower than the interactive limit because the worker's single loop (ticket email, SLA alerts, pushes) waits for it. | worker | worker |
| `CEDESK_REPORT_SCHEDULE_BUDGET_SECONDS` | `20` | The most time (1 to 300 seconds) one worker pass spends on scheduled reports. Once spent, no new schedule starts (one already running finishes, so a pass can run over by one report); the rest stay due and go out on the next pass. Workspaces take turns, one schedule each, so a busy one can't hold up the others. | worker | worker |
| `CEDESK_REPORT_CONCURRENCY` | `4` | Report queries one API process runs at once (1 to 50); more wait a few seconds, then answer 429. Keeps a burst of reports from taking the whole database. | api | api |

## Cloud plans and the worker

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_PLANS` | (empty: the built-in free, growth and enterprise plans) | Plan limits as JSON, e.g. `{"free": {"agents": 3, "tickets_month": 1000, "storage_gb": 1, "history_days": 365}}`. Cloud edition only, for workspaces given a plan. | api, worker, cli | no |
| `CEDESK_WORKER_INTERVAL_SECONDS` | `10` | How often the worker looks for work (up to 3600). | worker | no |
| `CEDESK_IMPORT_RETRY_ATTEMPTS` | `8` | Imports: failed tries of a page (a 5xx, a timeout at the source) before the record that keeps failing is set aside and listed in the run's failures (1 to 50). A 429 never counts. | api, worker | no |
| `CEDESK_IMPORT_RETRY_BASE_SECONDS` | `30` | Imports: the first wait after a failed page; it doubles each try, with jitter, never under the source's Retry-After. | api, worker | no |
| `CEDESK_IMPORT_RETRY_MAX_SECONDS` | `3600` | Imports: the longest wait between tries (at least the base). The defaults retry for about 1 to 2 hours. | api, worker | no |
| `CEDESK_PUSH_FCM_PROJECT_ID` | (empty: push off) | The Firebase project id for push notifications to the mobile app. Set it together with the next one, or the worker refuses to start. | api, worker | api, worker |
| `CEDESK_PUSH_FCM_SERVICE_ACCOUNT` | (empty: push off) | The Firebase service account key (its JSON) used to send push notifications. Secret: never logged. | worker | worker |

## Deploy only (not CeDesk settings)

Read by `deploy/docker-compose.yml`, the database's first start, or `deploy/backup.sh`.

| Variable | What it does |
|---|---|
| `POSTGRES_PASSWORD` | The database superuser's password. |
| `CEDESK_OWNER_PASSWORD` | `cedesk_owner`'s password (migrations); compose builds `CEDESK_MIGRATION_DATABASE_URL` from it. |
| `CEDESK_APP_PASSWORD` | `cedesk_app`'s password (the app); compose builds `CEDESK_DATABASE_URL` from it. |
| `CEDESK_SITE` | Caddy's site: your domain (HTTPS) or `:80` (plain HTTP, for a short test). |
| `BACKUP_DIR` | Where `backup.sh` writes (default `~/cedesk-backups`). Its other settings (`BACKUP_GPG_RECIPIENT`, retention, alerts) are in `deploy/backup.env.example`. |
| `CEDESK_FORWARDED_ALLOW_IPS` | The proxies the API takes `X-Forwarded-For` from (uvicorn's `--forwarded-allow-ips`). Default: this machine and Docker's private networks. Never `*`. |

The three passwords are set on the database's **first** start only; changing them later in
`.env` doesn't change the database.
