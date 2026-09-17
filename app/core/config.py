"""Environment-backed application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    app_name: str = "FlowPilot"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ai_provider: Literal["openai"] = "openai"
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "FLOWPILOT_OPENAI_API_KEY"),
    )
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    ai_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    webhook_ingress_secret: SecretStr | None = Field(default=None, min_length=32)
    admin_enabled: str = "false"
    database_host: str = "localhost"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "flowpilot"
    database_user: str = "flowpilot"
    database_password: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FLOWPILOT_",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def admin_api_enabled(self) -> bool:
        """Enable internal admin routes only for the exact value ``true``."""
        return self.admin_enabled.lower() == "true"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per application process."""
    return Settings()
