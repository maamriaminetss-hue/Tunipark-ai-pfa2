
"""Read source-sample freshness for anomaly-detection metric families."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import requests


DEFAULT_FRESHNESS_QUERIES: dict[str, str] = {
    "http_5xx_ratio": (
        'time() - max by (service_name) (timestamp('
        'http_server_request_duration_seconds_count{'
        'service_namespace="opentelemetry-demo"}))'
    ),
    "http_p95_latency_seconds": (
        'time() - max by (service_name) (timestamp('
        'http_server_request_duration_seconds_count{'
        'service_namespace="opentelemetry-demo"}))'
    ),
    "cpu_busy_ratio": (
        'time() - max by (service_name) (timestamp('
        'system_cpu_utilization_ratio{'
        'service_namespace="opentelemetry-demo",state="idle"}))'
    ),
    "memory_used_ratio": (
        'time() - max by (service_name) (timestamp('
        'system_memory_utilization_ratio{'
        'service_namespace="opentelemetry-demo",state="used"}))'
    ),
}


TUNIPARK_FRESHNESS_QUERIES: dict[str, str] = {
    "tunipark_http_5xx_ratio": (
        'time() - max by (service_name) (timestamp('
        'label_replace(http_server_duration_milliseconds_count{'
        'exported_job="tunipark-ai-backend"}, '
        '"service_name", "$1", "exported_job", "(.*)")))'
    ),
    "tunipark_http_p95_latency_seconds": (
        'time() - max by (service_name) (timestamp('
        'label_replace(http_server_duration_milliseconds_bucket{'
        'exported_job="tunipark-ai-backend"}, '
        '"service_name", "$1", "exported_job", "(.*)")))'
    ),
}


class PrometheusFreshnessReaderError(RuntimeError):
    """Raised when Prometheus freshness data cannot be validated."""


class PrometheusMetricFreshnessReader:
    """Return age in seconds of the latest underlying scrape per service."""

    def __init__(
        self,
        prometheus_url: str | None = None,
        timeout_seconds: float = 10.0,
        session: Any = None,
        queries: dict[str, str] | None = None,
    ) -> None:
        url = (
            prometheus_url
            if prometheus_url is not None
            else os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
        )

        if not isinstance(url, str) or not url.strip():
            raise ValueError("prometheus_url must be a non-empty string")

        self.prometheus_url = url.strip().rstrip("/")

        if not self.prometheus_url.startswith(("http://", "https://")):
            raise ValueError("Prometheus URL must use HTTP or HTTPS")

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")

        self.timeout_seconds = float(timeout_seconds)
        self.session = session if session is not None else requests

        self.queries = dict(
            DEFAULT_FRESHNESS_QUERIES if queries is None else queries
        )

    @staticmethod
    def _parse_end(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)

        if not isinstance(value, datetime):
            raise ValueError("end must be a datetime")

        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)

    def fetch_sample_ages(
        self,
        metric_name: str,
        *,
        end: datetime | None = None,
    ) -> dict[str, float]:
        """Fetch one instantaneous age query, keyed by service name."""

        if metric_name not in self.queries:
            raise ValueError(f"Unknown freshness metric: {metric_name}")

        end_at = self._parse_end(end)

        try:
            response = self.session.get(
                f"{self.prometheus_url}/api/v1/query",
                params={
                    "query": self.queries[metric_name],
                    "time": end_at.timestamp(),
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()

        except requests.RequestException as exc:
            raise PrometheusFreshnessReaderError(
                f"Prometheus freshness request failed for {metric_name}: {exc}"
            ) from exc

        except (ValueError, TypeError) as exc:
            raise PrometheusFreshnessReaderError(
                f"Invalid Prometheus freshness response for {metric_name}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or payload.get("status") != "success":
            detail = (
                payload.get("error", "unknown Prometheus error")
                if isinstance(payload, dict)
                else "response is not a JSON object"
            )

            raise PrometheusFreshnessReaderError(
                f"Prometheus freshness query failed for {metric_name}: {detail}"
            )

        data = payload.get("data")
        results = data.get("result") if isinstance(data, dict) else None

        if not isinstance(results, list):
            raise PrometheusFreshnessReaderError(
                f"Malformed freshness result for {metric_name}"
            )

        ages: dict[str, float] = {}

        for index, item in enumerate(results):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("metric"), dict)
            ):
                raise PrometheusFreshnessReaderError(
                    f"Malformed freshness series {index} for {metric_name}"
                )

            service_name = item["metric"].get("service_name")
            value = item.get("value")

            if not isinstance(service_name, str) or not service_name.strip():
                continue

            if not isinstance(value, (list, tuple)) or len(value) != 2:
                continue

            try:
                age = float(value[1])
            except (TypeError, ValueError):
                continue

            if not math.isfinite(age) or age < 0:
                continue

            ages[service_name.strip()] = age

        return ages
