"""
Analytics engine.

Takes raw ReportData and computes every derived metric, comparison,
and ranking the report needs. The output is a ReportAnalysis that
the renderer and AI agent both consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from .data_collector import ReportData

logger = structlog.get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────

def _safe_get(d: dict, key: str, default: Any = 0) -> Any:
    """Get a value from a dict, returning default if missing or None."""
    val = d.get(key)
    return val if val is not None else default


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _fmt_duration(seconds: int | float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m {s}s" if s else f"{m}m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _fmt_pct(val: float) -> str:
    return f"{val:.2f}%"


# ── Data structures ──────────────────────────────────────────────

@dataclass
class KPI:
    label: str
    value: str
    raw_value: float
    subtitle: str = ""
    change_pct: float | None = None
    change_direction: str = ""  # "up", "down", "flat"
    is_improvement: bool | None = None  # True = good, False = bad

    @property
    def change_label(self) -> str:
        if self.change_pct is None:
            return ""
        arrow = {"up": "\u2191", "down": "\u2193", "flat": "\u2192"}[self.change_direction]
        return f"{arrow} {abs(self.change_pct)}%"


@dataclass
class MajorIncidentRow:
    incident_number: int
    description: str
    service_name: str
    priority: str
    duration: str
    duration_seconds: int
    escalation_count: int
    engaged_user_count: int
    created_at: str
    is_sleep_hour: bool = False


@dataclass
class ServiceRow:
    service_name: str
    incident_count: int
    mttr: str
    mttr_seconds: int
    escalation_count: int
    uptime_pct: str
    uptime_raw: float
    sleep_interruptions: int


@dataclass
class ResponderRow:
    name: str
    incident_count: int
    interruptions: int
    sleep_interruptions: int
    avg_ack_seconds: int
    avg_ack_formatted: str
    timeout_escalations_from: int
    on_call_hours: float


@dataclass
class TrendWeek:
    range_start: str
    incidents: int
    mttr_p50_seconds: int
    mttr_p50_formatted: str
    mtta_p50_seconds: int
    mtta_p50_formatted: str
    sleep_interruptions: int
    escalation_rate: float


@dataclass
class ReportAnalysis:
    """Fully computed analysis ready for rendering."""

    # Metadata
    week_start: str = ""
    week_end: str = ""

    # Executive KPIs
    kpis: list[KPI] = field(default_factory=list)

    # Trend data
    weekly_trend: list[TrendWeek] = field(default_factory=list)

    # Incident breakdown
    major_incidents: list[MajorIncidentRow] = field(default_factory=list)
    top_services: list[ServiceRow] = field(default_factory=list)
    priority_breakdown: dict[str, int] = field(default_factory=dict)
    auto_resolved_count: int = 0
    auto_resolved_pct: float = 0.0
    ack_rate: float = 0.0

    # Response performance
    mtta_mean: str = ""
    mtta_p50: str = ""
    mtta_p75: str = ""
    mtta_p90: str = ""
    mtta_p95: str = ""
    mttr_p50: str = ""
    mttr_p75: str = ""
    mttr_p90: str = ""
    mttr_p95: str = ""
    timeout_escalations: int = 0
    manual_escalations: int = 0
    reassignment_rate: float = 0.0
    prev_timeout_escalations: int = 0
    prev_manual_escalations: int = 0

    # On-call health
    business_hour_interruptions: int = 0
    off_hour_interruptions: int = 0
    sleep_hour_interruptions: int = 0
    total_interruptions: int = 0
    responder_rows: list[ResponderRow] = field(default_factory=list)

    # Raw aggregated dicts for the AI agent to analyze
    current_raw: dict = field(default_factory=dict)
    previous_raw: dict = field(default_factory=dict)
    raw_incidents: list[dict] = field(default_factory=list)
    change_events: list[dict] = field(default_factory=list)


# ── Analyzer ─────────────────────────────────────────────────────

class Analyzer:
    """Transforms ReportData into ReportAnalysis."""

    def analyze(self, data: ReportData) -> ReportAnalysis:
        a = ReportAnalysis(
            week_start=data.week_start,
            week_end=data.week_end,
            current_raw=data.current_aggregated,
            previous_raw=data.previous_aggregated,
            raw_incidents=data.raw_incidents,
            change_events=data.change_events,
        )

        cur = data.current_aggregated
        prev = data.previous_aggregated

        if not cur:
            logger.warning("No aggregated data for current week.")
            return a

        # ── Executive KPIs ───────────────────────────────────────

        total = _safe_get(cur, "total_incident_count")
        prev_total = _safe_get(prev, "total_incident_count")
        a.kpis.append(self._make_kpi(
            "Incidents", total, prev_total,
            fmt=str, invert=True,  # fewer is better
        ))

        mttr_p50 = _safe_get(cur, "p50_seconds_to_resolve",
                             _safe_get(cur, "mean_seconds_to_resolve"))
        prev_mttr_p50 = _safe_get(prev, "p50_seconds_to_resolve",
                                  _safe_get(prev, "mean_seconds_to_resolve"))
        a.kpis.append(self._make_kpi(
            "MTTR (p50)", mttr_p50, prev_mttr_p50,
            fmt=_fmt_duration, invert=True,
        ))

        mttr_p95 = _safe_get(cur, "p95_seconds_to_resolve", 0)
        prev_mttr_p95 = _safe_get(prev, "p95_seconds_to_resolve", 0)
        a.kpis.append(self._make_kpi(
            "MTTR (p95)", mttr_p95, prev_mttr_p95,
            fmt=_fmt_duration, invert=True,
        ))

        uptime = _safe_get(cur, "up_time_pct", 100.0)
        prev_uptime = _safe_get(prev, "up_time_pct", 100.0)
        a.kpis.append(self._make_kpi(
            "Uptime", uptime, prev_uptime,
            fmt=_fmt_pct, invert=False,  # higher is better
        ))

        sleep = _safe_get(cur, "total_sleep_hour_interruptions")
        prev_sleep = _safe_get(prev, "total_sleep_hour_interruptions")
        a.kpis.append(self._make_kpi(
            "Sleep pages", sleep, prev_sleep,
            fmt=lambda x: str(int(x)), invert=True,
        ))

        # ── Trend ────────────────────────────────────────────────

        for week in data.weekly_trend:
            esc_count = _safe_get(week, "total_escalation_count")
            inc_count = _safe_get(week, "total_incident_count", 1)
            mttr_val = _safe_get(week, "p50_seconds_to_resolve",
                                 _safe_get(week, "mean_seconds_to_resolve"))
            mtta_val = _safe_get(week, "p50_seconds_to_first_ack",
                                 _safe_get(week, "mean_seconds_to_first_ack"))
            a.weekly_trend.append(TrendWeek(
                range_start=week.get("range_start", ""),
                incidents=int(inc_count),
                mttr_p50_seconds=int(mttr_val),
                mttr_p50_formatted=_fmt_duration(mttr_val),
                mtta_p50_seconds=int(mtta_val),
                mtta_p50_formatted=_fmt_duration(mtta_val),
                sleep_interruptions=int(_safe_get(week, "total_sleep_hour_interruptions")),
                escalation_rate=round(esc_count / inc_count, 2) if inc_count else 0,
            ))

        # ── Major incidents ──────────────────────────────────────

        for inc in sorted(
            data.major_incidents,
            key=lambda x: x.get("seconds_to_resolve", 0),
            reverse=True,
        ):
            created = inc.get("created_at", "")
            hour = 0
            try:
                hour = int(created[11:13]) if len(created) > 13 else 0
            except (ValueError, IndexError):
                pass
            a.major_incidents.append(MajorIncidentRow(
                incident_number=inc.get("incident_number", 0),
                description=inc.get("description", ""),
                service_name=inc.get("service_name", ""),
                priority=inc.get("priority_name", "P?"),
                duration=_fmt_duration(_safe_get(inc, "seconds_to_resolve")),
                duration_seconds=int(_safe_get(inc, "seconds_to_resolve")),
                escalation_count=int(_safe_get(inc, "escalation_count")),
                engaged_user_count=int(_safe_get(inc, "engaged_user_count")),
                created_at=created,
                is_sleep_hour=(hour >= 22 or hour < 8),
            ))

        # ── Service breakdown ────────────────────────────────────

        sorted_services = sorted(
            data.service_metrics,
            key=lambda x: x.get("total_incident_count", 0),
            reverse=True,
        )
        for svc in sorted_services[:10]:
            a.top_services.append(ServiceRow(
                service_name=svc.get("service_name", ""),
                incident_count=int(_safe_get(svc, "total_incident_count")),
                mttr=_fmt_duration(_safe_get(svc, "mean_seconds_to_resolve")),
                mttr_seconds=int(_safe_get(svc, "mean_seconds_to_resolve")),
                escalation_count=int(_safe_get(svc, "total_escalation_count")),
                uptime_pct=_fmt_pct(_safe_get(svc, "up_time_pct", 100.0)),
                uptime_raw=float(_safe_get(svc, "up_time_pct", 100.0)),
                sleep_interruptions=int(_safe_get(svc, "total_sleep_hour_interruptions")),
            ))

        # ── Priority breakdown ───────────────────────────────────

        for inc in data.raw_incidents:
            prio = inc.get("priority_name", "Unset")
            a.priority_breakdown[prio] = a.priority_breakdown.get(prio, 0) + 1

        auto = _safe_get(cur, "total_incidents_auto_resolved")
        if isinstance(auto, dict):
            auto = 0  # schema quirk: sometimes returns description object
        a.auto_resolved_count = int(auto)
        a.auto_resolved_pct = round(
            (a.auto_resolved_count / total * 100) if total else 0, 1
        )

        acked = _safe_get(cur, "total_incidents_acknowledged")
        a.ack_rate = round((acked / total * 100) if total else 0, 1)

        # ── Response performance ─────────────────────────────────

        a.mtta_mean = _fmt_duration(_safe_get(cur, "mean_seconds_to_first_ack"))
        a.mtta_p50 = _fmt_duration(_safe_get(cur, "p50_seconds_to_first_ack",
                                              _safe_get(cur, "mean_seconds_to_first_ack")))
        a.mtta_p75 = _fmt_duration(_safe_get(cur, "p75_seconds_to_first_ack", 0))
        a.mtta_p90 = _fmt_duration(_safe_get(cur, "p90_seconds_to_first_ack", 0))
        a.mtta_p95 = _fmt_duration(_safe_get(cur, "p95_seconds_to_first_ack", 0))
        a.mttr_p50 = _fmt_duration(_safe_get(cur, "p50_seconds_to_resolve",
                                              _safe_get(cur, "mean_seconds_to_resolve")))
        a.mttr_p75 = _fmt_duration(_safe_get(cur, "p75_seconds_to_resolve", 0))
        a.mttr_p90 = _fmt_duration(_safe_get(cur, "p90_seconds_to_resolve", 0))
        a.mttr_p95 = _fmt_duration(_safe_get(cur, "p95_seconds_to_resolve", 0))

        a.timeout_escalations = int(_safe_get(cur, "total_incidents_timeout_escalated"))
        a.manual_escalations = int(_safe_get(cur, "total_incidents_manual_escalated"))
        a.prev_timeout_escalations = int(_safe_get(prev, "total_incidents_timeout_escalated"))
        a.prev_manual_escalations = int(_safe_get(prev, "total_incidents_manual_escalated"))

        reassigned = _safe_get(cur, "total_incidents_reassigned")
        a.reassignment_rate = round((reassigned / total * 100) if total else 0, 1)

        # ── On-call health ───────────────────────────────────────

        a.business_hour_interruptions = int(_safe_get(cur, "total_business_hour_interruptions"))
        a.off_hour_interruptions = int(_safe_get(cur, "total_off_hour_interruptions"))
        a.sleep_hour_interruptions = int(_safe_get(cur, "total_sleep_hour_interruptions"))
        a.total_interruptions = int(_safe_get(cur, "total_interruptions"))

        for r in sorted(
            data.responder_metrics,
            key=lambda x: x.get("total_incident_count", 0),
            reverse=True,
        ):
            avg_ack = int(_safe_get(r, "mean_time_to_acknowledge_seconds"))
            on_call_sec = _safe_get(r, "total_seconds_on_call", 0)
            a.responder_rows.append(ResponderRow(
                name=r.get("responder_name", "Unknown"),
                incident_count=int(_safe_get(r, "total_incident_count")),
                interruptions=int(_safe_get(r, "total_interruptions")),
                sleep_interruptions=int(_safe_get(r, "total_sleep_hour_interruptions")),
                avg_ack_seconds=avg_ack,
                avg_ack_formatted=_fmt_duration(avg_ack),
                timeout_escalations_from=int(
                    _safe_get(r, "total_incidents_timeout_escalated_from", 0)
                ),
                on_call_hours=round(on_call_sec / 3600, 1) if on_call_sec else 0,
            ))

        return a

    # ── Private ──────────────────────────────────────────────────

    @staticmethod
    def _make_kpi(
        label: str,
        current: float,
        previous: float,
        fmt=str,
        invert: bool = False,
    ) -> KPI:
        pct = _pct_change(current, previous)
        if pct is not None:
            direction = "up" if pct > 0 else ("down" if pct < 0 else "flat")
            if invert:
                is_good = pct < 0
            else:
                is_good = pct > 0
        else:
            direction = "flat"
            is_good = None

        return KPI(
            label=label,
            value=fmt(current),
            raw_value=float(current),
            subtitle=f"prev: {fmt(previous)}",
            change_pct=pct,
            change_direction=direction,
            is_improvement=is_good,
        )
