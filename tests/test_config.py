"""Tests for configuration management."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reliability_agent.config import Config


def _make_config(**kwargs):
    """Create a Config with .env file loading disabled to avoid test pollution."""
    return Config(_env_file=None, **kwargs)


class TestConfig:
    def test_valid_config(self):
        config = _make_config(pagerduty_api_key="test-key-123")
        assert config.pagerduty_api_key == "test-key-123"
        assert config.pagerduty_base_url == "https://api.pagerduty.com"
        assert config.copilot_model == "claude-sonnet-4-6"
        assert config.timezone == "UTC"

    def test_missing_pagerduty_key_raises(self):
        with pytest.raises(ValidationError, match="PAGERDUTY_API_KEY"):
            _make_config(pagerduty_api_key="")

    def test_custom_base_url(self):
        config = _make_config(
            pagerduty_api_key="test-key",
            pagerduty_base_url="https://custom.pagerduty.com",
        )
        assert config.pagerduty_base_url == "https://custom.pagerduty.com"

    def test_custom_model(self):
        config = _make_config(pagerduty_api_key="test-key", copilot_model="gpt-4.1")
        assert config.copilot_model == "gpt-4.1"

    def test_invalid_timezone_raises(self):
        with pytest.raises(ValidationError, match="timezone"):
            _make_config(pagerduty_api_key="test-key", report_timezone="Not/A/Timezone")

    def test_valid_timezone(self):
        config = _make_config(pagerduty_api_key="test-key", report_timezone="America/New_York")
        assert config.timezone == "America/New_York"

    def test_timezone_property_alias(self):
        config = _make_config(pagerduty_api_key="test-key", report_timezone="Europe/London")
        assert config.timezone == config.report_timezone

    def test_frozen_config(self):
        config = _make_config(pagerduty_api_key="test-key")
        with pytest.raises(ValidationError):
            config.pagerduty_api_key = "new-key"

    def test_validate_config_returns_empty_list(self):
        config = _make_config(pagerduty_api_key="test-key")
        assert config.validate_config() == []
