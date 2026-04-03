"""Tests for HTML report rendering."""

from __future__ import annotations

from reliability_agent.ai_insights import GeneratedInsights
from reliability_agent.analytics import Analyzer
from reliability_agent.report_renderer import ReportRenderer


class TestReportRenderer:
    def test_render_produces_html(self, sample_report_data):
        analysis = Analyzer().analyze(sample_report_data)
        insights = GeneratedInsights(
            executive_summary="Test summary paragraph.",
            oncall_health_paragraph="Test oncall paragraph.",
            escalation_watch="3 timeout escalations.",
            takeaways=[
                {"number": 1, "severity": "red", "title": "Top issue", "body": "Details."},
                {"number": 0, "severity": "green", "title": "Good news", "body": "Improving."},
            ],
        )
        renderer = ReportRenderer()
        html = renderer.render(analysis, insights, team_name="SRE Team")
        assert "<!DOCTYPE" in html or "<html" in html
        assert "Test summary paragraph." in html
        assert "SRE Team" in html

    def test_render_contains_kpis(self, sample_report_data):
        analysis = Analyzer().analyze(sample_report_data)
        insights = GeneratedInsights(
            executive_summary="Summary.",
            oncall_health_paragraph="Oncall.",
            escalation_watch="Escalation.",
            takeaways=[],
        )
        renderer = ReportRenderer()
        html = renderer.render(analysis, insights)
        assert "Incidents" in html
        assert "MTTR" in html
        assert "Uptime" in html

    def test_render_contains_services(self, sample_report_data):
        analysis = Analyzer().analyze(sample_report_data)
        insights = GeneratedInsights()
        renderer = ReportRenderer()
        html = renderer.render(analysis, insights)
        assert "api-gateway" in html

    def test_save_creates_file(self, tmp_path, sample_report_data):
        analysis = Analyzer().analyze(sample_report_data)
        insights = GeneratedInsights(executive_summary="Test.")
        renderer = ReportRenderer()
        html = renderer.render(analysis, insights)

        out_path = tmp_path / "report.html"
        result = renderer.save(html, str(out_path))
        assert result.exists()
        assert result.read_text().startswith("<!") or "<html" in result.read_text()
