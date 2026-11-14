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
| `CEDESK_ENV` | `dev` | `dev` (a developer's machine), `staging` (a shared test server) or `production`. Anything but `dev` refuses to start with the dev secret key or dev database password, `*` in CORS, or Cevanos over plain http; `production` also refuses `MAIL_BACKEND=log` and turns off test-only routes (simulated customer replies; demo data, even with `CEDESK_DEMO_ENABLED`). | all | migrate, api, worker |
| `CEDESK_SECRET_KEY` | a dev-only value | Encrypts stored secrets (two-step sign-in, app credentials, the Cevanos key, webhook secrets, import credentials). Random, 32+ characters (`openssl rand -hex 32`). **Back it up; never change it**: a new key locks out everyone using two-step sign-in. | api, worker | api, worker |
| `CEDESK_EDITION` | `cloud` | `cloud` or `self_hosted`. Only switches the cloud plan limits on: they apply just to a workspace given a plan with `cedesk set-plan`. `/readyz` shows it. | api, worker, cli | no |
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
| `CEDESK_API_URL` | (empty: the address requests come in on) | This API's public address, for single sign-on: the identity provider sends people back to `<this>/api/v1/sso/<workspace>/acs`. | api | api |
| `CEDESK_CORS_ORIGINS` | (empty: none) | Sites whose pages may call the API from a browser, comma-separated, e.g. `https://app.example.com`. Not `*`. | api | api |

## Email out

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_MAIL_BACKEND` | `log` | `smtp` (send), `log` (write emails to the worker's log, send nothing; refused in production) or `none` (drop them). | worker | worker |
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
| `CEDESK_SCAN_BACKEND` | `none` | `clamd`: the worker checks every new file with ClamAV; a file can be downloaded or emailed only once found clean, and infected ones are removed. `none`: files aren't checked. | api, worker | api, worker |
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

## Cloud plans and the worker

| Setting | Default | What it does | Read by | In compose |
|---|---|---|---|---|
| `CEDESK_PLANS` | (empty: the built-in free, growth and enterprise plans) | Plan limits as JSON, e.g. `{"free": {"agents": 3, "tickets_month": 1000, "storage_gb": 1, "history_days": 365}}`. Cloud edition only, for workspaces given a plan. | api, worker, cli | no |
| `CEDESK_WORKER_INTERVAL_SECONDS` | `10` | How often the worker looks for work (up to 3600). | worker | no |
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
| `BACKUP_DIR` | Where `backup.sh` writes (default `~/cedesk-backups`). |

The three passwords are set on the database's **first** start only; changing them later in
`.env` doesn't change the database.
