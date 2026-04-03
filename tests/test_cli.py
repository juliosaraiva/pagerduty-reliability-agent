"""Tests for the CLI interface using Click's test runner."""

from __future__ import annotations

from click.testing import CliRunner

from main import cli


class TestCLI:
    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "reliability-digest" in result.output

    def test_validate_missing_pagerduty_key(self, monkeypatch):
        monkeypatch.delenv("PAGERDUTY_API_KEY", raising=False)
        runner = CliRunner(env={"PAGERDUTY_API_KEY": ""})
        result = runner.invoke(cli, ["validate"])
        assert result.exit_code == 1
        assert "Configuration error" in result.output or "PAGERDUTY_API_KEY" in result.output

    def test_generate_missing_pagerduty_key(self, monkeypatch):
        monkeypatch.delenv("PAGERDUTY_API_KEY", raising=False)
        runner = CliRunner(env={"PAGERDUTY_API_KEY": ""})
        result = runner.invoke(cli, ["generate"])
        assert result.exit_code == 1
