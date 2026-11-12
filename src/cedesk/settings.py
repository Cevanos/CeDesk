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
    log = "log"  # write emails to the log, send nothing (dev and test servers only)
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

    def mail_problems(self) -> list[str]:
        """What stops the worker sending email (the API itself sends none) or scanning."""
        problems = []
        if self.scan_backend is ScanBackend.clamd and not self.clamd_host:
            problems.append("CEDESK_CLAMD_HOST: set it, or CEDESK_SCAN_BACKEND=none")
        if self.mail_backend is MailBackend.smtp and not self.smtp_host:
            problems.append("CEDESK_SMTP_HOST: set it, or choose another CEDESK_MAIL_BACKEND")
        if self.imap_host and not (self.imap_username and self.imap_password):
            problems.append("CEDESK_IMAP_USERNAME and CEDESK_IMAP_PASSWORD: set both")
        if self.env is Env.production and self.mail_backend is MailBackend.log:
            problems.append(
                "CEDESK_MAIL_BACKEND: `log` writes emails (with sign-in codes) to the log; "
                "use smtp, or none until a mail server is set up"
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
