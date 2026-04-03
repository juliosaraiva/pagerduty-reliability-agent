"""Tests for the analytics engine — pure Python, no I/O."""

from __future__ import annotations

from reliability_agent.analytics import Analyzer, _fmt_duration, _pct_change
from reliability_agent.data_collector import ReportData


class TestHelpers:
    def test_fmt_duration_seconds(self):
        assert _fmt_duration(45) == "45s"

    def test_fmt_duration_minutes(self):
        assert _fmt_duration(120) == "2m"

    def test_fmt_duration_minutes_seconds(self):
        assert _fmt_duration(150) == "2m 30s"

    def test_fmt_duration_hours(self):
        assert _fmt_duration(3600) == "1h"

    def test_fmt_duration_hours_minutes(self):
        assert _fmt_duration(5400) == "1h 30m"

    def test_pct_change_increase(self):
        assert _pct_change(120, 100) == 20.0

    def test_pct_change_decrease(self):
        assert _pct_change(80, 100) == -20.0

    def test_pct_change_zero_previous(self):
        assert _pct_change(100, 0) is None

    def test_pct_change_no_change(self):
        assert _pct_change(100, 100) == 0.0


class TestMakeKPI:
    def test_fewer_incidents_is_improvement(self):
        kpi = Analyzer._make_kpi("Incidents", 10, 15, fmt=str, invert=True)
        assert kpi.is_improvement is True
        assert kpi.change_direction == "down"

    def test_more_incidents_is_regression(self):
        kpi = Analyzer._make_kpi("Incidents", 20, 10, fmt=str, invert=True)
        assert kpi.is_improvement is False
        assert kpi.change_direction == "up"

    def test_higher_uptime_is_improvement(self):
        kpi = Analyzer._make_kpi("Uptime", 99.95, 99.80, fmt=str, invert=False)
        assert kpi.is_improvement is True
        assert kpi.change_direction == "up"

    def test_lower_uptime_is_regression(self):
        kpi = Analyzer._make_kpi("Uptime", 99.70, 99.90, fmt=str, invert=False)
        assert kpi.is_improvement is False
        assert kpi.change_direction == "down"

    def test_no_previous_data(self):
        kpi = Analyzer._make_kpi("Incidents", 10, 0, fmt=str, invert=True)
        assert kpi.change_pct is None
        assert kpi.is_improvement is None

    def test_change_label_format(self):
        kpi = Analyzer._make_kpi("Incidents", 80, 100, fmt=str, invert=True)
        assert "20.0%" in kpi.change_label
        assert "\u2193" in kpi.change_label  # down arrow


class TestAnalyzer:
    def test_analyze_empty_data(self):
        data = ReportData(week_start="2025-03-24", week_end="2025-03-30")
        result = Analyzer().analyze(data)
        assert result.kpis == []
        assert result.major_incidents == []
        assert result.top_services == []

    def test_analyze_produces_five_kpis(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert len(result.kpis) == 5
        labels = [k.label for k in result.kpis]
        assert "Incidents" in labels
        assert "MTTR (p50)" in labels
        assert "MTTR (p95)" in labels
        assert "Uptime" in labels
        assert "Sleep pages" in labels

    def test_analyze_incident_count(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        incidents_kpi = result.kpis[0]
        assert incidents_kpi.raw_value == 42
        assert incidents_kpi.is_improvement is True  # 42 < 50

    def test_analyze_top_services_sorted(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert len(result.top_services) == 3
        assert result.top_services[0].service_name == "api-gateway"
        assert result.top_services[0].incident_count == 15

    def test_analyze_major_incidents(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert len(result.major_incidents) == 1
        assert result.major_incidents[0].incident_number == 1001

    def test_analyze_sleep_hour_detection(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        # Incident 1001 was at 02:30 — should be flagged as sleep hour
        assert result.major_incidents[0].is_sleep_hour is True

    def test_analyze_priority_breakdown(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert "P1" in result.priority_breakdown
        assert "P2" in result.priority_breakdown
        assert "P3" in result.priority_breakdown

    def test_analyze_auto_resolved(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert result.auto_resolved_count == 8
        assert result.auto_resolved_pct == round(8 / 42 * 100, 1)

    def test_analyze_ack_rate(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert result.ack_rate == round(36 / 42 * 100, 1)

    def test_analyze_response_performance(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert result.mtta_p50 == _fmt_duration(90)
        assert result.mttr_p50 == _fmt_duration(1200)

    def test_analyze_oncall_health(self, sample_report_data):
        result = Analyzer().analyze(sample_report_data)
        assert result.business_hour_interruptions == 20
        assert result.off_hour_interruptions == 10
        assert result.sleep_hour_interruptions == 5
        assert result.total_interruptions == 35
