"""Tests for data collection with mocked PagerDuty client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from reliability_agent.data_collector import DataCollector, ReportData, _week_bounds
from datetime import datetime, timezone


class TestWeekBounds:
    def test_monday_input(self):
        dt = datetime(2025, 3, 24, 12, 0, tzinfo=timezone.utc)  # Monday
        start, end = _week_bounds(dt)
        assert start.weekday() == 0  # Monday
        assert end.weekday() == 6  # Sunday
        assert start.hour == 0
        assert end.hour == 23

    def test_friday_input(self):
        dt = datetime(2025, 3, 28, 12, 0, tzinfo=timezone.utc)  # Friday
        start, end = _week_bounds(dt)
        assert start.strftime("%Y-%m-%d") == "2025-03-24"
        assert end.strftime("%Y-%m-%d") == "2025-03-30"

    def test_sunday_input(self):
        dt = datetime(2025, 3, 30, 12, 0, tzinfo=timezone.utc)  # Sunday
        start, end = _week_bounds(dt)
        assert start.strftime("%Y-%m-%d") == "2025-03-24"


class TestDataCollector:
    def _make_mock_client(self, aggregated, services, incidents):
        """Create a mock PagerDutyClient that returns fixture data."""
        mock = MagicMock()
        mock.get_aggregated_incident_metrics.return_value = [aggregated]
        mock.get_service_metrics.return_value = services
        mock.get_team_metrics.return_value = []
        mock.get_responder_metrics.return_value = []
        mock.get_raw_incidents.return_value = incidents
        mock.list_change_events.return_value = []
        mock.list_oncalls.return_value = []
        mock.list_services.return_value = []
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_collect_returns_report_data(
        self, sample_aggregated, sample_services, sample_incidents,
    ):
        config = MagicMock()
        config.timezone = "UTC"

        mock_client = self._make_mock_client(
            sample_aggregated, sample_services, sample_incidents,
        )

        collector = DataCollector(config)
        with patch(
            "reliability_agent.data_collector.PagerDutyClient",
            return_value=mock_client,
        ):
            result = collector.collect()

        assert isinstance(result, ReportData)
        assert result.week_start != ""
        assert result.week_end != ""

    def test_collect_populates_aggregated(
        self, sample_aggregated, sample_services, sample_incidents,
    ):
        config = MagicMock()
        config.timezone = "UTC"

        mock_client = self._make_mock_client(
            sample_aggregated, sample_services, sample_incidents,
        )

        collector = DataCollector(config)
        with patch(
            "reliability_agent.data_collector.PagerDutyClient",
            return_value=mock_client,
        ):
            result = collector.collect()

        assert result.current_aggregated.get("total_incident_count") == 42

    def test_collect_handles_noncritical_failure(
        self, sample_aggregated, sample_services, sample_incidents,
    ):
        config = MagicMock()
        config.timezone = "UTC"

        mock_client = self._make_mock_client(
            sample_aggregated, sample_services, sample_incidents,
        )
        mock_client.list_change_events.side_effect = RuntimeError("fail")

        collector = DataCollector(config)
        with patch(
            "reliability_agent.data_collector.PagerDutyClient",
            return_value=mock_client,
        ):
            result = collector.collect()

        # Should still succeed, change_events just empty
        assert isinstance(result, ReportData)
        assert result.change_events == []
