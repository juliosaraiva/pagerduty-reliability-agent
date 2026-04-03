"""
PagerDuty REST API client.

Thin wrapper around httpx that handles auth, pagination, rate-limit
back-off, and the quirks of the analytics POST endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import Config

logger = structlog.get_logger(__name__)

# PagerDuty returns 429 when you hit the rate limit.
# Their REST API allows 960 req/min with 6-request bursts.
_RETRYABLE = (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout)


class PagerDutyClient:
    """Stateless client for the PagerDuty v2 REST + Analytics APIs."""

    def __init__(self, config: Config) -> None:
        self._base = config.pagerduty_base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            headers={
                "Authorization": f"Token token={config.pagerduty_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.pagerduty+json;version=2",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Low-level helpers ────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        reraise=True,
    )
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client.get(path, params=params or {})
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning("Rate limited, backing off %ds", retry_after)
            raise httpx.HTTPStatusError(
                "rate limited", request=resp.request, response=resp
            )
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        reraise=True,
    )
    def _post(self, path: str, body: dict) -> dict:
        resp = self._client.post(path, json=body)
        if resp.status_code == 429:
            raise httpx.HTTPStatusError(
                "rate limited", request=resp.request, response=resp
            )
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(
        self, path: str, resource_key: str, params: dict | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch all pages of an offset-paginated GET endpoint."""
        params = dict(params or {})
        params["limit"] = limit
        params["offset"] = 0
        all_items: list[dict] = []

        while True:
            data = self._get(path, params)
            items = data.get(resource_key, [])
            all_items.extend(items)
            more = data.get("more", False)
            if not more or not items:
                break
            params["offset"] += len(items)

        return all_items

    def _post_paginated(
        self, path: str, body: dict, limit: int = 1000,
    ) -> list[dict]:
        """Fetch all pages of a cursor-paginated POST analytics endpoint."""
        body = dict(body)
        body.setdefault("limit", limit)
        all_items: list[dict] = []

        while True:
            data = self._post(path, body)
            items = data.get("data", [])
            all_items.extend(items)
            cursor = data.get("starting_after")
            if not cursor or len(items) < body["limit"]:
                break
            body["starting_after"] = cursor

        return all_items

    # ── Analytics endpoints ──────────────────────────────────────

    def get_aggregated_incident_metrics(
        self,
        start: str,
        end: str,
        aggregate_unit: str | None = None,
        timezone: str = "UTC",
        filters: dict | None = None,
    ) -> list[dict]:
        """POST /analytics/metrics/incidents/all"""
        body: dict[str, Any] = {
            "filters": {
                "created_at_start": start,
                "created_at_end": end,
                **(filters or {}),
            },
            "time_zone": timezone,
        }
        if aggregate_unit:
            body["aggregate_unit"] = aggregate_unit

        data = self._post("/analytics/metrics/incidents/all", body)
        return data.get("data", [])

    def get_service_metrics(
        self, start: str, end: str, timezone: str = "UTC",
    ) -> list[dict]:
        """POST /analytics/metrics/incidents/services/all"""
        body = {
            "filters": {
                "created_at_start": start,
                "created_at_end": end,
            },
            "time_zone": timezone,
        }
        return self._post_paginated(
            "/analytics/metrics/incidents/services/all", body
        )

    def get_team_metrics(
        self, start: str, end: str, timezone: str = "UTC",
    ) -> list[dict]:
        """POST /analytics/metrics/incidents/teams/all"""
        body = {
            "filters": {
                "created_at_start": start,
                "created_at_end": end,
            },
            "time_zone": timezone,
        }
        return self._post_paginated(
            "/analytics/metrics/incidents/teams/all", body
        )

    def get_responder_metrics(
        self, start: str, end: str, timezone: str = "UTC",
    ) -> list[dict]:
        """POST /analytics/metrics/responders/all"""
        body = {
            "filters": {
                "created_at_start": start,
                "created_at_end": end,
            },
            "time_zone": timezone,
        }
        return self._post_paginated(
            "/analytics/metrics/responders/all", body
        )

    def get_raw_incidents(
        self,
        start: str,
        end: str,
        timezone: str = "UTC",
        filters: dict | None = None,
    ) -> list[dict]:
        """POST /analytics/raw/incidents"""
        body = {
            "filters": {
                "created_at_start": start,
                "created_at_end": end,
                **(filters or {}),
            },
            "time_zone": timezone,
        }
        return self._post_paginated("/analytics/raw/incidents", body)

    # ── REST endpoints ───────────────────────────────────────────

    def list_incidents(
        self,
        since: str,
        until: str,
        statuses: list[str] | None = None,
        urgencies: list[str] | None = None,
    ) -> list[dict]:
        """GET /incidents"""
        params: dict[str, Any] = {"since": since, "until": until}
        if statuses:
            params["statuses[]"] = statuses
        if urgencies:
            params["urgencies[]"] = urgencies
        return self._get_paginated("/incidents", "incidents", params)

    def list_services(self) -> list[dict]:
        """GET /services"""
        return self._get_paginated("/services", "services")

    def list_oncalls(
        self,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """GET /oncalls"""
        params: dict[str, str] = {}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return self._get_paginated("/oncalls", "oncalls", params)

    def list_change_events(
        self, since: str, until: str,
    ) -> list[dict]:
        """GET /change_events"""
        params = {"since": since, "until": until}
        return self._get_paginated("/change_events", "change_events", params)

    def get_incident_log_entries(self, incident_id: str) -> list[dict]:
        """GET /incidents/{id}/log_entries"""
        return self._get_paginated(
            f"/incidents/{incident_id}/log_entries", "log_entries"
        )
