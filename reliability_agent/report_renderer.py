"""
Report renderer.

Takes a ReportAnalysis + GeneratedInsights and produces the final
HTML email using a Jinja2 template with the Home Depot color palette.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader

from .ai_insights import GeneratedInsights
from .analytics import ReportAnalysis

logger = structlog.get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _severity_color(severity: str) -> str:
    return {
        "red": "#C62828",
        "orange": "#F96302",
        "amber": "#F57C00",
        "green": "#2E7D32",
    }.get(severity, "#5b5e5e")


def _uptime_color(raw: float) -> str:
    if raw >= 99.9:
        return "#2E7D32"
    if raw >= 99.7:
        return "#F57C00"
    return "#C62828"


def _mttr_color(seconds: int) -> str:
    if seconds > 3600:
        return "#C62828"
    if seconds > 1800:
        return "#F57C00"
    return "#232525"


def _ack_color(seconds: int) -> str:
    if seconds < 120:
        return "#2E7D32"
    if seconds < 300:
        return "#F57C00"
    return "#C62828"


def _kpi_change_color(kpi) -> str:
    if kpi.is_improvement is True:
        return "#2E7D32"
    if kpi.is_improvement is False:
        return "#C62828"
    return "#999999"


def _kpi_arrow(kpi) -> str:
    return {
        "up": "&uarr;",
        "down": "&darr;",
        "flat": "&rarr;",
    }.get(kpi.change_direction, "")


class ReportRenderer:
    """Renders the HTML email from analysis + insights."""

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=True,
        )
        # Register custom filters
        self._env.filters["severity_color"] = _severity_color
        self._env.filters["uptime_color"] = _uptime_color
        self._env.filters["mttr_color"] = _mttr_color
        self._env.filters["ack_color"] = _ack_color
        self._env.filters["kpi_change_color"] = _kpi_change_color
        self._env.filters["kpi_arrow"] = _kpi_arrow

    def render(
        self,
        analysis: ReportAnalysis,
        insights: GeneratedInsights,
        team_name: str = "",
    ) -> str:
        template = self._env.get_template("weekly_digest.html")
        return template.render(
            a=analysis,
            insights=insights,
            team_name=team_name,
            severity_color=_severity_color,
            uptime_color=_uptime_color,
            mttr_color=_mttr_color,
            ack_color=_ack_color,
            kpi_change_color=_kpi_change_color,
            kpi_arrow=_kpi_arrow,
        )

    def save(self, html: str, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        logger.info("Report saved to %s", path)
        return path
