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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CEDESK_", env_file=".env", extra="ignore")

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
    attachment_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    # S3 caps a presigned link at 7 days.
    download_link_seconds: int = Field(default=300, ge=10, le=7 * 24 * 3600)

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
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
