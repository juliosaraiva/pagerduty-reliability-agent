#!/usr/bin/env python3
"""
PagerDuty Weekly Reliability Digest — CLI entry point.

Usage:
    python main.py generate
    python main.py generate --date 2025-03-24 --output report.html
    python main.py generate --team "Platform Engineering"
    python main.py validate
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import structlog

from reliability_agent import __version__
from reliability_agent.ai_insights import InsightAgent
from reliability_agent.analytics import Analyzer
from reliability_agent.config import Config
from reliability_agent.data_collector import DataCollector
from reliability_agent.email_sender import EmailSender
from reliability_agent.report_renderer import ReportRenderer


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO

    # Quiet down noisy libraries unless we're in debug mode
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Use colored console output for terminals, JSON for CI/production
    if sys.stdout.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


@click.group()
@click.version_option(version=__version__, prog_name="reliability-digest")
def cli():
    """PagerDuty Weekly Reliability Digest generator."""
    pass


@cli.command()
@click.option(
    "--date", "-d",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Any date within the target week (defaults to last completed week).",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=None,
    help="Output file path. Defaults to ./reliability-digest-<week>.html",
)
@click.option(
    "--team", "-t",
    type=str,
    default="",
    help="Team name shown in the report header.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--send-email", is_flag=True, help="Send the report via SMTP after saving.")
@click.option("--email-to", multiple=True, help="Recipient email address (repeatable).")
def generate(date, output, team, verbose, send_email, email_to):
    """Generate a weekly reliability digest from PagerDuty data."""
    _setup_logging(verbose)
    logger = structlog.get_logger("reliability_digest")

    # ── Config ──────────────────────────────────────────────────
    try:
        config = Config()
    except Exception as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    for p in config.validate_config():
        logger.warning(p)

    # ── Reference date ──────────────────────────────────────────
    ref = date.replace(tzinfo=timezone.utc) if date else None

    # ── Collect ─────────────────────────────────────────────────
    click.echo("Fetching data from PagerDuty...")
    collector = DataCollector(config)
    report_data = collector.collect(reference_date=ref)
    click.echo(
        f"  Week: {report_data.week_start} to {report_data.week_end}  "
        f"({len(report_data.raw_incidents)} incidents)"
    )

    # ── Analyze ─────────────────────────────────────────────────
    click.echo("Computing analytics...")
    analyzer = Analyzer()
    analysis = analyzer.analyze(report_data)

    kpi_summary = ", ".join(f"{k.label}: {k.value}" for k in analysis.kpis)
    click.echo(f"  KPIs: {kpi_summary}")

    # ── AI Insights ─────────────────────────────────────────────
    click.echo("Generating narrative insights...")
    agent = InsightAgent(config)
    insights = agent.generate(analysis)

    if insights.executive_summary:
        # Show first sentence as a preview
        preview = insights.executive_summary.split(".")[0] + "."
        click.echo(f"  Summary: {preview}")

    # ── Render ──────────────────────────────────────────────────
    click.echo("Rendering HTML report...")
    renderer = ReportRenderer()
    html = renderer.render(analysis, insights, team_name=team)

    # ── Save ────────────────────────────────────────────────────
    if output is None:
        output = f"reliability-digest-{report_data.week_start}.html"
    out_path = renderer.save(html, output)

    click.echo("")
    click.echo(f"Report saved to {out_path}")

    # ── Email ───────────────────────────────────────────────────
    if send_email:
        recipients = list(email_to) or [
            a.strip() for a in config.report_to_addresses.split(",") if a.strip()
        ]
        if not recipients:
            click.echo("No recipients specified. Use --email-to or set REPORT_TO_ADDRESSES.", err=True)
            sys.exit(1)
        if not config.smtp_host:
            click.echo("SMTP_HOST not configured. Cannot send email.", err=True)
            sys.exit(1)

        click.echo(f"Sending report to {', '.join(recipients)}...")
        sender = EmailSender(
            host=config.smtp_host,
            port=config.smtp_port,
            user=config.smtp_user,
            password=config.smtp_password,
            from_address=config.report_from_address,
        )
        subject = f"Reliability Digest — {report_data.week_start} to {report_data.week_end}"
        sender.send(html, subject, recipients)
        click.echo("Email sent.")
    else:
        click.echo("Open it in a browser to preview.")


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def validate(verbose):
    """Check that the configuration and API connectivity are working."""
    _setup_logging(verbose)

    try:
        config = Config()
    except Exception as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    click.echo("Configuration check:")
    click.echo(f"  PagerDuty API key: set")
    click.echo(f"  PagerDuty base URL: {config.pagerduty_base_url}")
    click.echo(f"  Copilot model: {config.copilot_model}")
    click.echo(f"  Timezone: {config.timezone}")
    click.echo(f"  AI insights: via GitHub Copilot SDK (copilot auth login required)")

    # Quick connectivity test
    click.echo("")
    click.echo("Testing PagerDuty connectivity...")
    from reliability_agent.pagerduty_client import PagerDutyClient

    try:
        with PagerDutyClient(config) as pd:
            services = pd.list_services()
        click.echo(f"  Connected. Found {len(services)} services.")
    except Exception as e:
        click.echo(f"  Connection failed: {e}", err=True)
        sys.exit(1)

    click.echo("")
    click.echo("All checks passed.")


if __name__ == "__main__":
    cli()
