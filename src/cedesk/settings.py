"""Configuration, read once from the environment (prefix `CEDESK_`)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Edition(StrEnum):
    cloud = "cloud"
    self_hosted = "self_hosted"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CEDESK_", env_file=".env", extra="ignore")

    # The app connects as `cedesk_app`: it owns nothing and cannot bypass row-level security.
    database_url: str = "postgresql+asyncpg://cedesk_app:cedesk_app_dev@localhost:5447/cedesk"
    # Migrations run as `cedesk_owner`, which owns the tables.
    migration_database_url: str = (
        "postgresql+asyncpg://cedesk_owner:cedesk_owner_dev@localhost:5447/cedesk"
    )
    edition: Edition = Edition.cloud
    secret_key: SecretStr = Field(default=SecretStr("dev-only-change-me"))
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
