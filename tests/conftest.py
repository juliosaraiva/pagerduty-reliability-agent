"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reliability_agent.data_collector import ReportData

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_aggregated() -> dict:
    return json.loads((FIXTURES / "sample_aggregated.json").read_text())


@pytest.fixture
def sample_previous_aggregated() -> dict:
    data = json.loads((FIXTURES / "sample_aggregated.json").read_text())
    # Slightly different from current to test WoW comparison
    data["total_incident_count"] = 50
    data["p50_seconds_to_resolve"] = 1500
    data["p95_seconds_to_resolve"] = 6000
    data["up_time_pct"] = 99.80
    data["total_sleep_hour_interruptions"] = 7
    return data


@pytest.fixture
def sample_incidents() -> list[dict]:
    return json.loads((FIXTURES / "sample_incidents.json").read_text())


@pytest.fixture
def sample_services() -> list[dict]:
    return json.loads((FIXTURES / "sample_services.json").read_text())


@pytest.fixture
def sample_report_data(
    sample_aggregated, sample_previous_aggregated, sample_incidents, sample_services,
) -> ReportData:
    return ReportData(
        week_start="2025-03-24",
        week_end="2025-03-30",
        prev_week_start="2025-03-17",
        prev_week_end="2025-03-23",
        current_aggregated=sample_aggregated,
        previous_aggregated=sample_previous_aggregated,
        weekly_trend=[],
        service_metrics=sample_services,
        team_metrics=[],
        responder_metrics=[],
        raw_incidents=sample_incidents,
        major_incidents=sample_incidents[:1],
        change_events=[],
        oncalls=[],
        services=[],
    )
