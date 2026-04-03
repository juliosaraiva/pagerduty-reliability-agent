"""
Data collector.

Orchestrates the PagerDuty API calls needed for one weekly report
and returns a normalized ReportData object that downstream modules
(analytics, renderer) can consume without touching the API again.

API calls are executed in parallel using ThreadPoolExecutor for
~5-8x faster data collection.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import structlog

from .config import Config
from .pagerduty_client import PagerDutyClient

logger = structlog.get_logger(__name__)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _week_bounds(reference: datetime) -> tuple[datetime, datetime]:
    """Return Monday 00:00 -> Sunday 23:59:59 for the week containing `reference`."""
    monday = reference - timedelta(days=reference.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return monday, sunday


@dataclass
class ReportData:
    """Everything needed to render one weekly digest."""

    # Date bounds
    week_start: str = ""
    week_end: str = ""
    prev_week_start: str = ""
    prev_week_end: str = ""

    # Aggregated metrics (current + previous week)
    current_aggregated: dict = field(default_factory=dict)
    previous_aggregated: dict = field(default_factory=dict)

    # 4-week trend (list of weekly buckets)
    weekly_trend: list[dict] = field(default_factory=list)

    # Per-service metrics (current week)
    service_metrics: list[dict] = field(default_factory=list)

    # Per-team metrics (current week)
    team_metrics: list[dict] = field(default_factory=list)

    # Per-responder metrics (current week)
    responder_metrics: list[dict] = field(default_factory=list)

    # Raw incidents for drill-down
    raw_incidents: list[dict] = field(default_factory=list)
    major_incidents: list[dict] = field(default_factory=list)

    # Change events (current week)
    change_events: list[dict] = field(default_factory=list)

    # On-call roster snapshot
    oncalls: list[dict] = field(default_factory=list)

    # Service catalog
    services: list[dict] = field(default_factory=list)


class DataCollector:
    """Fetches all the data for a single weekly report."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._tz = config.timezone

    def _make_task(
        self,
        key: str,
        func: Callable[..., Any],
        *args: Any,
        critical: bool = True,
        **kwargs: Any,
    ) -> dict:
        """Build a task descriptor for the thread pool."""
        return {
            "key": key,
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "critical": critical,
        }

    def collect(self, reference_date: datetime | None = None) -> ReportData:
        """
        Pull data for the week containing `reference_date`.
        Defaults to the most recently completed week.

        All API calls are independent and run in parallel for speed.
        Each thread gets its own PagerDutyClient instance for thread safety.
        """
        if reference_date is None:
            now = datetime.now(timezone.utc)
            reference_date = now - timedelta(weeks=1)

        week_start, week_end = _week_bounds(reference_date)
        prev_start, prev_end = _week_bounds(week_start - timedelta(days=1))
        trend_start = week_start - timedelta(weeks=3)

        report = ReportData(
            week_start=week_start.strftime("%Y-%m-%d"),
            week_end=week_end.strftime("%Y-%m-%d"),
            prev_week_start=prev_start.strftime("%Y-%m-%d"),
            prev_week_end=prev_end.strftime("%Y-%m-%d"),
        )

        # Define all tasks — each will run in its own thread with its own client
        ws, we = _iso(week_start), _iso(week_end)
        ps, pe = _iso(prev_start), _iso(prev_end)
        ts = _iso(trend_start)

        def _run_task(task_func: Callable, *args: Any, **kwargs: Any) -> Any:
            """Execute a PagerDuty API call in its own client context."""
            with PagerDutyClient(self._config) as pd:
                return task_func(pd, *args, **kwargs)

        tasks: dict[str, dict] = {
            "current_aggregated": {
                "func": lambda pd: (
                    pd.get_aggregated_incident_metrics(ws, we, timezone=self._tz)
                ),
                "critical": True,
            },
            "previous_aggregated": {
                "func": lambda pd: (
                    pd.get_aggregated_incident_metrics(ps, pe, timezone=self._tz)
                ),
                "critical": True,
            },
            "weekly_trend": {
                "func": lambda pd: (
                    pd.get_aggregated_incident_metrics(
                        ts, we, aggregate_unit="week", timezone=self._tz,
                    )
                ),
                "critical": True,
            },
            "service_metrics": {
                "func": lambda pd: pd.get_service_metrics(ws, we, timezone=self._tz),
                "critical": True,
            },
            "team_metrics": {
                "func": lambda pd: pd.get_team_metrics(ws, we, timezone=self._tz),
                "critical": True,
            },
            "responder_metrics": {
                "func": lambda pd: pd.get_responder_metrics(ws, we, timezone=self._tz),
                "critical": True,
            },
            "raw_incidents": {
                "func": lambda pd: pd.get_raw_incidents(ws, we, timezone=self._tz),
                "critical": True,
            },
            "major_incidents": {
                "func": lambda pd: pd.get_raw_incidents(
                    ws, we, timezone=self._tz, filters={"major": True},
                ),
                "critical": True,
            },
            "change_events": {
                "func": lambda pd: pd.list_change_events(ws, we),
                "critical": False,
            },
            "oncalls": {
                "func": lambda pd: pd.list_oncalls(ws, we),
                "critical": False,
            },
            "services": {
                "func": lambda pd: pd.list_services(),
                "critical": False,
            },
        }

        logger.info("Fetching PagerDuty data (%d parallel calls)...", len(tasks))

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_to_key = {
                pool.submit(_run_task, task["func"]): key
                for key, task in tasks.items()
            }

            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                    logger.debug("Fetched %s", key)
                except Exception as e:
                    if tasks[key]["critical"]:
                        logger.error("Failed to fetch %s: %s", key, e)
                        results[key] = [] if key != "current_aggregated" else {}
                    else:
                        logger.warning("Could not fetch %s: %s", key, e)
                        results[key] = []

        # Map results to ReportData fields
        agg = results.get("current_aggregated", [])
        report.current_aggregated = agg[0] if agg else {}

        prev_agg = results.get("previous_aggregated", [])
        report.previous_aggregated = prev_agg[0] if prev_agg else {}

        report.weekly_trend = results.get("weekly_trend", [])
        report.service_metrics = results.get("service_metrics", [])
        report.team_metrics = results.get("team_metrics", [])
        report.responder_metrics = results.get("responder_metrics", [])
        report.raw_incidents = results.get("raw_incidents", [])
        report.major_incidents = results.get("major_incidents", [])
        report.change_events = results.get("change_events", [])
        report.oncalls = results.get("oncalls", [])
        report.services = results.get("services", [])

        logger.info("Data collection complete.")
        return report
