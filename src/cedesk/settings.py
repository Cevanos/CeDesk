"""Configuration, read once from the environment (prefix `CEDESK_`)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Edition(StrEnum):
    cloud = "cloud"
    self_hosted = "self_hosted"


class Env(StrEnum):
    dev = "dev"  # a developer's machine: the dev passwords and secret key are fine
    staging = "staging"  # a shared test server, e.g. for the frontend team
    production = "production"


DEV_SECRET_KEY = "dev-only-change-me"  # noqa: S105  (the value refused outside dev)


class StorageDriver(StrEnum):
    local = "local"  # a folder on this server's disk
    s3 = "s3"  # AWS S3, or any S3-compatible service (MinIO, R2, ...) through an endpoint URL
    gcs = "gcs"  # Google Cloud Storage, through its S3-compatible API with HMAC keys
    azure = "azure"  # Azure Blob Storage, with the storage account's name and key


class ScanBackend(StrEnum):
    none = "none"  # files are not checked (downloads work at once)
    clamd = "clamd"  # ClamAV's daemon over TCP; the worker checks every new file


class MailBackend(StrEnum):
    # Send nothing; each email is written as a .eml file to `mail_outbox_dir` (a developer's
    # machine only: the files hold sign-in codes and reset links). The log gets the file
    # name, never the email. Refused anywhere but CEDESK_ENV=dev.
    log = "log"
    smtp = "smtp"  # any SMTP server: Amazon SES, Gmail, Zoho, Mailgun, ...
    none = "none"  # drop every email


class SmtpSecurity(StrEnum):
    starttls = "starttls"  # usually port 587
    ssl = "ssl"  # usually port 465
    none = "none"  # only for a local test server


class Settings(BaseSettings):
    # An empty variable counts as not set: docker-compose passes `${CEDESK_X:-}` as "".
    model_config = SettingsConfigDict(
        env_prefix="CEDESK_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    # The app connects as `cedesk_app`: it owns nothing and cannot bypass row-level security.
    database_url: str = "postgresql+asyncpg://cedesk_app:cedesk_app_dev@localhost:5447/cedesk"
    # Migrations run as `cedesk_owner`, which owns the tables.
    migration_database_url: str = (
        "postgresql+asyncpg://cedesk_owner:cedesk_owner_dev@localhost:5447/cedesk"
    )
    edition: Edition = Edition.cloud
    env: Env = Field(
        default=Env.dev,
        description="Anything but dev refuses to start with the dev passwords or secret key.",
    )
    secret_key: SecretStr = Field(default=SecretStr(DEV_SECRET_KEY))
    cors_origins: str = Field(
        default="",
        description="Comma-separated sites whose pages may call the API from a browser, "
        "e.g. 'https://app.cedesk.in,http://localhost:3000'. Empty: none.",
    )
    sign_in_limit: int = Field(
        default=20, ge=1, description="Sign-in or password-change attempts per IP per window."
    )
    sign_in_window_seconds: int = Field(default=300, ge=1)
    form_limit: int = Field(
        default=10, ge=1, description="Public web form posts per IP per window (md C2)."
    )
    form_window_seconds: int = Field(default=600, ge=1)
    vote_limit: int = Field(
        default=60, ge=1, description="Help article votes per IP per window (md P6)."
    )
    vote_window_seconds: int = Field(default=600, ge=1)
    public_limit: int = Field(
        default=120,
        ge=1,
        description="Requests per IP per window to public pages with no sign-in: the CSAT "
        "rating page and the help center (votes and the web form have their own).",
    )
    public_window_seconds: int = Field(default=600, ge=1)
    # Email campaigns (feature-gap plan phase 6). The caps are the same for every workspace.
    campaign_max_recipients: int = Field(
        default=5000, ge=1, le=1_000_000, description="The most recipients one campaign has."
    )
    campaign_daily_cap: int = Field(
        default=10_000,
        ge=1,
        description="Campaign emails one workspace may send in 24 hours, all campaigns "
        "together (test emails and ticket replies are not counted).",
    )
    campaign_per_minute: int = Field(
        default=60,
        ge=1,
        le=10_000,
        description="Campaign emails one workspace sends a minute at most (stay under the "
        "mail provider's rate; ticket replies go first).",
    )
    unsubscribe_limit: int = Field(
        default=600,
        ge=1,
        description="Unsubscribe and open-image requests per IP per window. High on "
        "purpose: mail providers send many people's one-click unsubscribes and image "
        "fetches from a few addresses.",
    )
    unsubscribe_window_seconds: int = Field(default=600, ge=1)
    plans: str = Field(
        default="",
        description="Cloud plan limits as JSON (md O4), e.g. "
        '{"free": {"agents": 3, "tickets_month": 1000, "storage_gb": 1, "history_days": 365}}. '
        "Empty: the md D6 proposal. Only on the cloud edition, and only for a workspace "
        "given a plan (cedesk set-plan).",
    )
    api_key_limit: int = Field(
        default=700,
        ge=1,
        description="Requests a minute per workspace with API keys, all its keys together "
        "(md PL2). Over it: 429 with Retry-After. Counted in each API process.",
    )
    demo_enabled: bool = Field(
        default=False,
        description="Turns on the demo data routes (`/demo/seed`, `/demo/samples`, "
        "`/demo/reset`, md D1-D3) for this server. Never on a production server, even "
        "when set.",
    )
    log_level: str = "INFO"

    # Uploaded files. One setting picks where they live; the rest belongs to that driver.
    storage_driver: StorageDriver = StorageDriver.local
    storage_local_path: str = "storage"
    storage_s3_bucket: str = ""
    storage_s3_region: str | None = None
    storage_s3_endpoint_url: str | None = Field(
        default=None, description="Leave empty for AWS; e.g. http://localhost:9000 for MinIO."
    )
    # Unset keys fall back to boto3's own chain (instance role, AWS_* variables).
    storage_s3_access_key_id: str | None = None
    storage_s3_secret_access_key: SecretStr | None = None
    storage_s3_path_style: bool = Field(
        default=False, description="MinIO and some other services need path-style URLs."
    )
    # GCS: a bucket and an HMAC key (Cloud Storage > Settings > Interoperability), signed
    # like S3 against storage.googleapis.com.
    storage_gcs_bucket: str = ""
    storage_gcs_hmac_access_id: str | None = None
    storage_gcs_hmac_secret: SecretStr | None = None
    # Azure: the storage account, its key and a container that already exists.
    storage_azure_account_name: str = ""
    storage_azure_account_key: SecretStr | None = None
    storage_azure_container: str = ""
    storage_azure_endpoint: str | None = Field(
        default=None,
        description="Leave empty for Azure; e.g. http://127.0.0.1:10000/devstoreaccount1 for "
        "Azurite.",
    )
    attachment_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    # S3 caps a presigned link at 7 days.
    download_link_seconds: int = Field(default=300, ge=10, le=7 * 24 * 3600)

    app_url: str = Field(
        default="http://localhost:3000",
        description="The agent app's address, for links in emails (password reset).",
    )
    api_url: str = Field(
        default="",
        description="This API's public address (https://...), for single sign-on: the "
        "identity provider sends people back to `<api_url>/api/v1/sso/<workspace>/acs`. "
        "Empty: the address the request came in on.",
    )
    portal_url: str = Field(
        default="",
        description="The customer portal's address, for links in emails (the CSAT survey "
        "opens `<portal_url>/rate`). Empty: the same as app_url.",
    )
    # Sender of account emails (password reset) when the workspace has no support
    # address yet, e.g. no-reply@cedesk.in. Empty: such emails can't be sent.
    mail_from_address: str = ""
    mail_from_name: str = "CeDesk"
    mail_backend: MailBackend = MailBackend.log
    mail_outbox_dir: str = Field(
        default="mail-outbox",
        description="With CEDESK_MAIL_BACKEND=log (dev only): the folder emails are written to.",
    )
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: SmtpSecurity = SmtpSecurity.starttls
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: float = Field(default=30, gt=0, le=300)
    # Email in. Webhook: mail services (Mailgun, SendGrid, ...) post raw emails to
    # POST /api/v1/inbound/email with this token; unset, the route answers 404.
    inbound_email_token: SecretStr | None = None
    inbound_email_max_bytes: int = Field(default=30 * 1024 * 1024, ge=1024)
    # Or a mailbox the worker reads (Gmail / Zoho with an app password, any IMAP server).
    imap_host: str = ""
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username: str | None = None
    imap_password: SecretStr | None = None
    imap_folder: str = "INBOX"
    # Checking support addresses (md C10), shown in Settings > Email. Where each support
    # address should forward to (the mail service's inbound address, e.g. the Mailgun
    # route's); and the mail service's SPF name (e.g. amazonses.com, mailgun.org,
    # _spf.google.com), which the DNS check looks for. Empty: not shown / not checked.
    mail_forward_to: str = ""
    mail_spf_include: str = ""
    # Virus scan (md T18): with clamd, a file can be downloaded or emailed only after the
    # worker found it clean.
    scan_backend: ScanBackend = ScanBackend.none
    clamd_host: str = ""
    clamd_port: int = Field(default=3310, ge=1, le=65535)
    clamd_timeout_seconds: float = Field(default=60, gt=0, le=600)
    # clamd's StreamMaxLength (100 MB unless changed in clamd.conf). A bigger file is marked
    # scan_failed without sending it: clamd would cut the connection mid-file, which looks
    # like "scanner down" and would be retried forever.
    clamd_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    # Cevanos connector (md 7.1). One Cevanos per deployment; each workspace enters its own
    # API key and webhook secret (Settings, stored encrypted). Without a key it is off.
    cevanos_url: str = Field(
        default="https://api.cevanos.ai",
        description="Cevanos's API address. https, except on a developer's machine.",
    )
    cevanos_app_url: str = Field(
        default="https://cevanos.ai",
        description="Cevanos's web app, for links into it (the Governance report, md R5). "
        "https, except on a developer's machine.",
    )
    cevanos_poll_batch: int = Field(
        default=20, ge=1, le=60, description="Chats read per worker pass, per workspace."
    )
    worker_interval_seconds: float = Field(
        default=10, gt=0, le=3600, description="How often `cedesk worker` looks for work."
    )
    # Imports (audit P0-2): how long a run keeps retrying a page that fails with a temporary
    # error (a 5xx, a timeout) before it sets aside the record that keeps failing. The wait
    # doubles from the base up to the cap, with jitter, never under the source's Retry-After.
    # The defaults (8 tries, 30 s doubling to 1 h: about 1-2 h in all) ride out a typical
    # help desk outage without holding a run for good. A 429 never counts as a try.
    import_retry_attempts: int = Field(
        default=8, ge=1, le=50,
        description="Failed tries of an import page before its failing records are set aside.",
    )  # fmt: skip
    import_retry_base_seconds: float = Field(
        default=30, gt=0, le=3600, description="The first wait after a failed import page."
    )
    import_retry_max_seconds: float = Field(
        default=3600, gt=0, le=3600, description="The longest wait between import page tries."
    )
    # Push notifications to the mobile app (md P8), through Firebase Cloud Messaging (it
    # delivers to iPhones too, with the app's Apple key uploaded to Firebase). Both empty:
    # push is off, and nothing else changes.
    push_fcm_project_id: str = Field(
        default="", description="The Firebase project's id, e.g. 'cedesk-mobile'."
    )
    push_fcm_service_account: SecretStr | None = Field(
        default=None,
        description="The Firebase service account's JSON key (the whole file's content), "
        "with the 'Firebase Cloud Messaging API' role. Never logged.",
    )
    # Self-service sign-up: anyone can create a workspace from the sign-in page, after
    # proving their email with a code. Off unless turned on, so no install opens by accident.
    signup_enabled: bool = Field(
        default=False,
        description="Lets anyone create a workspace from the sign-in page (email code first). "
        "Off: the sign-up routes answer 404.",
    )
    signup_default_plan: str = Field(
        default="free",
        description="The cloud plan a workspace made by sign-up starts on (one of "
        "CEDESK_PLANS). Ignored on self_hosted. Empty: no plan (no limits).",
    )
    signup_captcha_site_key: str = Field(
        default="", description="Cloudflare Turnstile site key the sign-up page shows."
    )
    signup_captcha_secret: SecretStr | None = Field(
        default=None,
        description="Cloudflare Turnstile secret the API checks each sign-up with. Unset: no "
        "CAPTCHA (refused on the cloud edition outside dev).",
    )
    signup_per_ip_hour: int = Field(
        default=5, ge=1, description="Sign-ups started per IP per hour (kept in the database)."
    )
    signup_workspaces_per_ip_day: int = Field(
        default=3, ge=1, description="Workspaces created by sign-up per IP per day."
    )
    signup_mail_concurrency: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Sign-up code emails the worker sends at once (stay under the mail "
        "provider's rate).",
    )
    signup_hash_slots: int = Field(
        default=4,
        ge=1,
        le=64,
        description="Sign-up passwords hashed at once per API process (each Argon2 hash "
        "uses about 64 MB of memory).",
    )
    report_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=120,
        description="A custom or built-in report query that runs longer than this is "
        "cancelled (the answer says to narrow the range). Scheduled reports use "
        "`report_schedule_timeout_seconds` instead.",
    )
    report_schedule_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="A scheduled report's query that runs longer than this is cancelled and "
        "the schedule says why. Lower than the interactive limit: the worker's one loop also "
        "sends ticket email, SLA alerts and pushes, and waits for it.",
    )
    report_schedule_budget_seconds: int = Field(
        default=20,
        ge=1,
        le=300,
        description="The most time one worker pass spends on scheduled reports. Once it is "
        "spent no new schedule is started (one already running finishes); the rest stay due "
        "and go out on the next pass, a minute later.",
    )
    report_concurrency: int = Field(
        default=4,
        ge=1,
        le=50,
        description="Report queries one API process runs at once; more wait a few seconds, "
        "then answer 429.",
    )

    def push_on(self) -> bool:
        return bool(self.push_fcm_project_id and self.push_fcm_service_account)

    def allowed_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    def unsafe(self) -> list[str]:
        """What must change before this runs anywhere but a developer's machine."""
        if self.env is Env.dev:
            return []
        problems = []
        key = self.secret_key.get_secret_value()
        if key == DEV_SECRET_KEY or len(key) < 32:
            problems.append("CEDESK_SECRET_KEY: set a random value of 32+ characters")
        if "_dev@" in self.database_url:
            problems.append("CEDESK_DATABASE_URL: uses the dev password")
        if "*" in self.allowed_origins():
            problems.append("CEDESK_CORS_ORIGINS: list the sites, not *")
        if not self.cevanos_url.startswith("https://"):
            problems.append("CEDESK_CEVANOS_URL: use https")
        if not self.cevanos_app_url.startswith("https://"):
            problems.append("CEDESK_CEVANOS_APP_URL: use https")
        token = self.inbound_email_token
        if token is not None and len(token.get_secret_value()) < 32:
            problems.append("CEDESK_INBOUND_EMAIL_TOKEN: use a random value of 32+ characters")
        return problems

    def signup_problems(self) -> list[str]:
        """The API's check, with sign-up on (outside dev): what lets bots in, or leaves the
        codes with no sender. The worker checks the mail server (`mail_problems`)."""
        if not self.signup_enabled or self.env is Env.dev:
            return []
        problems = []
        if not self.mail_from_address:
            problems.append("CEDESK_MAIL_FROM_ADDRESS: sign-up codes need a sender")
        captcha = bool(self.signup_captcha_site_key) + bool(self.signup_captcha_secret)
        if captcha == 1:
            problems.append(
                "CEDESK_SIGNUP_CAPTCHA_SITE_KEY and CEDESK_SIGNUP_CAPTCHA_SECRET: set both"
            )
        elif captcha == 0 and self.edition is Edition.cloud:
            problems.append("CEDESK_SIGNUP_CAPTCHA_SECRET: the cloud edition needs a CAPTCHA")
        return problems

    def mail_problems(self) -> list[str]:
        """What stops the worker sending email (the API itself sends none) or scanning."""
        problems = []
        if self.scan_backend is ScanBackend.clamd and not self.clamd_host:
            problems.append("CEDESK_CLAMD_HOST: set it, or CEDESK_SCAN_BACKEND=none")
        if self.mail_backend is MailBackend.smtp and not self.smtp_host:
            problems.append("CEDESK_SMTP_HOST: set it, or choose another CEDESK_MAIL_BACKEND")
        if self.imap_host and not (self.imap_username and self.imap_password):
            problems.append("CEDESK_IMAP_USERNAME and CEDESK_IMAP_PASSWORD: set both")
        if bool(self.push_fcm_project_id) != bool(self.push_fcm_service_account):
            problems.append(
                "CEDESK_PUSH_FCM_PROJECT_ID and CEDESK_PUSH_FCM_SERVICE_ACCOUNT: set both, "
                "or neither (push off)"
            )
        if self.signup_enabled and self.env is not Env.dev:
            if self.mail_backend is not MailBackend.smtp:
                problems.append("CEDESK_MAIL_BACKEND: sign-up codes need smtp")
            if not self.mail_from_address:
                problems.append("CEDESK_MAIL_FROM_ADDRESS: sign-up codes need a sender")
        if self.env is not Env.dev and self.mail_backend is MailBackend.log:
            problems.append(
                "CEDESK_MAIL_BACKEND: `log` keeps every email (with sign-in codes and reset "
                "links) on this machine and is for a developer's machine only; use smtp, or "
                "none until a mail server is set up"
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
