"""
Configuration management.

Uses Pydantic BaseSettings to read from environment variables (with .env
support) and validate that required keys are present before any API calls.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        extra="ignore",
    )

    pagerduty_api_key: str = ""
    pagerduty_base_url: str = "https://api.pagerduty.com"
    copilot_model: str = "claude-sonnet-4-6"
    report_timezone: str = "UTC"

    # SMTP email delivery (optional)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    report_from_address: str = ""
    report_to_addresses: str = ""  # comma-separated

    @field_validator("pagerduty_api_key")
    @classmethod
    def api_key_must_be_set(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "PAGERDUTY_API_KEY must be set in the environment or .env file"
            )
        return v

    @field_validator("report_timezone")
    @classmethod
    def valid_timezone(cls, v: str) -> str:
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(v)
        except (KeyError, Exception):
            raise ValueError(f"Unknown timezone: {v!r}")
        return v

    @property
    def timezone(self) -> str:
        """Alias for backward compatibility with existing code."""
        return self.report_timezone

    def validate_config(self) -> list[str]:
        """Return a list of non-fatal warnings about the config."""
        problems: list[str] = []
        return problems
