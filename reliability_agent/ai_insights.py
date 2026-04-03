"""
AI-powered insight generation using GitHub Copilot SDK.

Feeds the computed ReportAnalysis to Claude (via Copilot SDK) and gets
back the natural-language sections of the report:
  - Executive summary paragraph
  - On-call health paragraph
  - Escalation watch callout
  - Takeaways (numbered action items + positive note)

The prompt is structured so the model acts as a Senior SRE writing
for a mixed audience of engineers and business leaders.

Authentication is handled by the Copilot CLI — run `copilot auth login`
with a GitHub PAT before using this tool.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import structlog
from copilot import CopilotClient, PermissionHandler, SystemMessageReplaceConfig

from .analytics import ReportAnalysis
from .config import Config

logger = structlog.get_logger(__name__)


@dataclass
class GeneratedInsights:
    """Natural-language sections produced by the AI agent."""
    executive_summary: str = ""
    oncall_health_paragraph: str = ""
    escalation_watch: str = ""
    takeaways: list[dict] = field(default_factory=list)
    # Each takeaway: {"number": 1, "severity": "red|orange|amber|green",
    #                  "title": "...", "body": "..."}


SYSTEM_PROMPT = """\
You are a Senior Site Reliability Engineer writing the narrative sections \
of a weekly reliability digest email. Your audience is mixed: engineering \
managers who want technical detail and business leaders who want the \
bottom line.

Rules:
- Write in short, direct sentences. No filler. Vary your sentence length.
- Reference specific numbers from the data (counts, percentages, durations).
- Compare this week to previous week and the 4-week trend where relevant.
- When something is bad, say so plainly. When something is good, say so.
- Do not use corporate buzzwords, em dashes, or the phrase "it's worth noting".
- Do not use bullet points. Write in paragraphs.
- Keep each section to 2-4 sentences.
- For takeaways, write 3 action items (things to fix) and 1 positive callout.
- Each takeaway gets a short bold title and 1-2 sentences of context.
- Never use these words: crucial, pivotal, landscape, foster, underscore, \
highlight, delve, enhance, leverage, streamline, garner, showcase, testament.
- Never start with "Additionally" or "Furthermore".
- Use "is" and "are" instead of "serves as" or "stands as".
- Do not use "Not only...but also" constructions.
- Do not force ideas into groups of three.
- Sound like a person wrote this at their desk, not like a press release.

Return valid JSON matching this schema:
{
  "executive_summary": "string (2-4 sentences)",
  "oncall_health_paragraph": "string (2-4 sentences)",
  "escalation_watch": "string (1-2 sentences for the callout box)",
  "takeaways": [
    {"number": 1, "severity": "red", "title": "string", "body": "string"},
    {"number": 2, "severity": "orange", "title": "string", "body": "string"},
    {"number": 3, "severity": "amber", "title": "string", "body": "string"},
    {"number": 0, "severity": "green", "title": "string", "body": "string"}
  ]
}
"""


def _build_context(analysis: ReportAnalysis) -> str:
    """Serialize the analysis into a compact context string for the LLM."""
    kpis = []
    for k in analysis.kpis:
        kpis.append({
            "label": k.label,
            "value": k.value,
            "previous": k.subtitle,
            "change": k.change_label,
            "improved": k.is_improvement,
        })

    trend = []
    for w in analysis.weekly_trend:
        trend.append({
            "week": w.range_start,
            "incidents": w.incidents,
            "mttr_p50": w.mttr_p50_formatted,
            "mtta_p50": w.mtta_p50_formatted,
            "sleep_interrupts": w.sleep_interruptions,
        })

    majors = []
    for m in analysis.major_incidents[:5]:
        majors.append({
            "number": m.incident_number,
            "description": m.description,
            "service": m.service_name,
            "priority": m.priority,
            "duration": m.duration,
            "escalations": m.escalation_count,
            "responders": m.engaged_user_count,
            "sleep_hour": m.is_sleep_hour,
        })

    services = []
    for s in analysis.top_services[:8]:
        services.append({
            "name": s.service_name,
            "incidents": s.incident_count,
            "mttr": s.mttr,
            "escalations": s.escalation_count,
            "uptime": s.uptime_pct,
            "sleep_pages": s.sleep_interruptions,
        })

    responders = []
    for r in analysis.responder_rows[:10]:
        responders.append({
            "name": r.name,
            "incidents": r.incident_count,
            "interrupts": r.interruptions,
            "sleep_pages": r.sleep_interruptions,
            "avg_ack": r.avg_ack_formatted,
            "timeout_esc": r.timeout_escalations_from,
        })

    context = {
        "week": f"{analysis.week_start} to {analysis.week_end}",
        "kpis": kpis,
        "trend_4_weeks": trend,
        "major_incidents": majors,
        "top_services": services,
        "response_performance": {
            "mtta": {"mean": analysis.mtta_mean, "p50": analysis.mtta_p50,
                     "p75": analysis.mtta_p75, "p90": analysis.mtta_p90,
                     "p95": analysis.mtta_p95},
            "mttr": {"p50": analysis.mttr_p50, "p75": analysis.mttr_p75,
                     "p90": analysis.mttr_p90, "p95": analysis.mttr_p95},
            "timeout_escalations": analysis.timeout_escalations,
            "prev_timeout_escalations": analysis.prev_timeout_escalations,
            "manual_escalations": analysis.manual_escalations,
            "reassignment_rate_pct": analysis.reassignment_rate,
        },
        "oncall_health": {
            "business_hour_interruptions": analysis.business_hour_interruptions,
            "off_hour_interruptions": analysis.off_hour_interruptions,
            "sleep_hour_interruptions": analysis.sleep_hour_interruptions,
            "total_interruptions": analysis.total_interruptions,
        },
        "responder_load": responders,
        "incident_breakdown": {
            "priority": analysis.priority_breakdown,
            "auto_resolved_pct": analysis.auto_resolved_pct,
            "ack_rate_pct": analysis.ack_rate,
        },
        "change_events_count": len(analysis.change_events),
    }

    return json.dumps(context, indent=2, default=str)


class InsightAgent:
    """Uses GitHub Copilot SDK (Claude) to generate report narrative from structured metrics."""

    def __init__(self, config: Config) -> None:
        self._config = config

    async def _async_generate(self, analysis: ReportAnalysis) -> GeneratedInsights:
        """Call Claude via Copilot SDK and parse the structured response."""
        context = _build_context(analysis)
        user_message = (
            "Here is the structured data for this week's reliability report. "
            "Analyze it and generate the narrative sections.\n\n"
            f"```json\n{context}\n```"
        )

        log = logger.bind(
            week=f"{analysis.week_start}:{analysis.week_end}",
            model=self._config.copilot_model,
        )
        log.info("calling_copilot_sdk")

        async with CopilotClient() as client:
            async with await client.create_session(
                model=self._config.copilot_model,
                system_message=SystemMessageReplaceConfig(mode="replace", content=SYSTEM_PROMPT),
                on_permission_request=PermissionHandler.approve_all,
            ) as session:
                response = await session.send_and_wait(user_message)

                # Extract text content from the response
                raw_text = response.data.content.strip()
                log.info("copilot_sdk_response_received", response_length=len(raw_text))

                # Strip markdown code fences if the model wrapped them
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("\n", 1)[-1]
                if raw_text.endswith("```"):
                    raw_text = raw_text.rsplit("```", 1)[0]

                parsed = json.loads(raw_text)
                return GeneratedInsights(
                    executive_summary=parsed.get("executive_summary", ""),
                    oncall_health_paragraph=parsed.get("oncall_health_paragraph", ""),
                    escalation_watch=parsed.get("escalation_watch", ""),
                    takeaways=parsed.get("takeaways", []),
                )

    def generate(self, analysis: ReportAnalysis) -> GeneratedInsights:
        """Call Claude and parse the structured response.

        Wraps the async Copilot SDK call in asyncio.run() to maintain
        a synchronous interface for the Click CLI.
        Falls back to rule-based insights on any error.
        """
        try:
            return asyncio.run(self._async_generate(analysis))
        except json.JSONDecodeError as e:
            logger.error("Failed to parse AI response as JSON: %s", e)
            return self._fallback(analysis)
        except Exception as e:
            logger.error("Copilot SDK error: %s", e)
            return self._fallback(analysis)

    @staticmethod
    def _fallback(analysis: ReportAnalysis) -> GeneratedInsights:
        """Generate basic insights without AI when the Copilot SDK is unavailable."""
        a = analysis
        total = int(a.kpis[0].raw_value) if a.kpis else 0
        prev_label = a.kpis[0].subtitle if a.kpis else ""

        # Build a simple summary from the numbers
        summary_parts = []
        if a.kpis:
            k = a.kpis[0]
            direction = "up" if k.change_direction == "up" else "down"
            summary_parts.append(
                f"Incident volume was {direction} this week at {k.value} ({prev_label})."
            )
        if len(a.kpis) > 1:
            summary_parts.append(
                f"Median resolution time (MTTR p50) was {a.kpis[1].value}."
            )
        if len(a.kpis) > 4:
            summary_parts.append(
                f"Sleep-hour pages came in at {a.kpis[4].value}."
            )

        oncall_parts = []
        if a.total_interruptions:
            biz_pct = round(a.business_hour_interruptions / a.total_interruptions * 100)
            oncall_parts.append(
                f"{biz_pct}% of interruptions landed during business hours."
            )
        oncall_parts.append(
            f"Reassignment rate was {a.reassignment_rate}%."
        )

        esc_text = (
            f"{a.timeout_escalations} timeout escalations this week "
            f"(previous week: {a.prev_timeout_escalations})."
        )

        takeaways = []
        if a.top_services:
            svc = a.top_services[0]
            takeaways.append({
                "number": 1, "severity": "red",
                "title": f"{svc.service_name} led in incident volume.",
                "body": (f"{svc.incident_count} incidents, {svc.mttr} average MTTR, "
                         f"{svc.escalation_count} escalations."),
            })
        takeaways.append({
            "number": 2, "severity": "orange",
            "title": "Review escalation patterns.",
            "body": (f"{a.timeout_escalations} timeout escalations and "
                     f"{a.manual_escalations} manual escalations this week."),
        })
        takeaways.append({
            "number": 3, "severity": "amber",
            "title": "Monitor sleep-hour page trends.",
            "body": f"{a.sleep_hour_interruptions} sleep-hour interruptions this week.",
        })
        # Positive note
        improving = any(
            k.is_improvement for k in a.kpis if k.label in ("MTTR (p50)", "MTTR (p95)")
        )
        if improving:
            takeaways.append({
                "number": 0, "severity": "green",
                "title": "Resolution times are trending down.",
                "body": f"MTTR p50 at {a.mttr_p50}, p95 at {a.mttr_p95}.",
            })

        return GeneratedInsights(
            executive_summary=" ".join(summary_parts),
            oncall_health_paragraph=" ".join(oncall_parts),
            escalation_watch=esc_text,
            takeaways=takeaways,
        )
