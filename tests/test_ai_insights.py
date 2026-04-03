"""Tests for AI insight generation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reliability_agent.ai_insights import GeneratedInsights, InsightAgent, _build_context
from reliability_agent.analytics import Analyzer


@pytest.fixture
def sample_analysis(sample_report_data):
    return Analyzer().analyze(sample_report_data)


@pytest.fixture
def valid_ai_response():
    return json.dumps({
        "executive_summary": "Incident volume dropped 16% to 42 this week.",
        "oncall_health_paragraph": "57% of interruptions landed during business hours.",
        "escalation_watch": "3 timeout escalations, down from previous week.",
        "takeaways": [
            {"number": 1, "severity": "red", "title": "api-gateway noise", "body": "15 incidents."},
            {"number": 2, "severity": "orange", "title": "Escalations", "body": "Review patterns."},
            {"number": 3, "severity": "amber", "title": "Sleep pages", "body": "5 this week."},
            {"number": 0, "severity": "green", "title": "MTTR improving", "body": "Down 20%."},
        ],
    })


class TestBuildContext:
    def test_returns_valid_json(self, sample_analysis):
        context = _build_context(sample_analysis)
        parsed = json.loads(context)
        assert "week" in parsed
        assert "kpis" in parsed
        assert isinstance(parsed["kpis"], list)

    def test_includes_kpis(self, sample_analysis):
        context = json.loads(_build_context(sample_analysis))
        assert len(context["kpis"]) == 5

    def test_includes_services(self, sample_analysis):
        context = json.loads(_build_context(sample_analysis))
        assert len(context["top_services"]) == 3


class TestFallback:
    def test_fallback_returns_insights(self, sample_analysis):
        config = MagicMock()
        agent = InsightAgent(config)
        result = agent._fallback(sample_analysis)
        assert isinstance(result, GeneratedInsights)
        assert result.executive_summary != ""
        assert result.oncall_health_paragraph != ""
        assert result.escalation_watch != ""
        assert len(result.takeaways) >= 2

    def test_fallback_mentions_incident_count(self, sample_analysis):
        config = MagicMock()
        agent = InsightAgent(config)
        result = agent._fallback(sample_analysis)
        assert "42" in result.executive_summary

    def test_fallback_with_empty_analysis(self):
        from reliability_agent.data_collector import ReportData

        empty_data = ReportData(week_start="2025-01-06", week_end="2025-01-12")
        empty_analysis = Analyzer().analyze(empty_data)

        config = MagicMock()
        agent = InsightAgent(config)
        result = agent._fallback(empty_analysis)
        assert isinstance(result, GeneratedInsights)


class TestGenerate:
    def test_generate_falls_back_on_copilot_error(self, sample_analysis):
        config = MagicMock()
        config.copilot_model = "claude-sonnet-4-6"
        agent = InsightAgent(config)

        with patch.object(agent, "_async_generate", side_effect=RuntimeError("no auth")):
            result = agent.generate(sample_analysis)
        assert isinstance(result, GeneratedInsights)
        assert result.executive_summary != ""

    @pytest.mark.asyncio
    async def test_async_generate_parses_valid_json(self, sample_analysis, valid_ai_response):
        config = MagicMock()
        config.copilot_model = "claude-sonnet-4-6"
        agent = InsightAgent(config)

        mock_response = MagicMock()
        mock_response.data.content = valid_ai_response

        mock_session = AsyncMock()
        mock_session.send_and_wait.return_value = mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("reliability_agent.ai_insights.CopilotClient", return_value=mock_client):
            result = await agent._async_generate(sample_analysis)

        assert result.executive_summary == "Incident volume dropped 16% to 42 this week."
        assert len(result.takeaways) == 4

    @pytest.mark.asyncio
    async def test_async_generate_strips_markdown_fences(self, sample_analysis, valid_ai_response):
        config = MagicMock()
        config.copilot_model = "claude-sonnet-4-6"
        agent = InsightAgent(config)

        wrapped = f"```json\n{valid_ai_response}\n```"
        mock_response = MagicMock()
        mock_response.data.content = wrapped

        mock_session = AsyncMock()
        mock_session.send_and_wait.return_value = mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("reliability_agent.ai_insights.CopilotClient", return_value=mock_client):
            result = await agent._async_generate(sample_analysis)

        assert result.executive_summary == "Incident volume dropped 16% to 42 this week."
