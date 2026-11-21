"""Prometheus range reader for service-level AIOps anomaly detection.

The reader deliberately returns observations only. It does not create events,
incidents, or remediation actions. The caller controls detection thresholds,
history requirements, persistence, and scheduling.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests


DEFAULT_ANOMALY_QUERIES: dict[str, str] = {
    "http_5xx_ratio": (
        "(sum by (service_name) ("
        'rate(http_server_request_duration_seconds_count{'
        'service_namespace="opentelemetry-demo",'
        'http_response_status_code=~"5.."}[5m])) '
        "or on (service_name) "
        "(0 * sum by (service_name) ("
        'rate(http_server_request_duration_seconds_count{'
        'service_namespace="opentelemetry-demo"}[5m])))'
        ") / clamp_min(sum by (service_name) ("
        'rate(http_server_request_duration_seconds_count{'
        'service_namespace="opentelemetry-demo"}[5m])), 0.000001)'
    ),
    "http_p95_latency_seconds": (
        "histogram_quantile(0.95, sum by (service_name, le) ("
        'rate(http_server_request_duration_seconds_bucket{'
        'service_namespace="opentelemetry-demo"}[5m])))'
    ),
    "cpu_busy_ratio": (
        "1 - avg by (service_name) ("
        'system_cpu_utilization_ratio{'
        'service_namespace="opentelemetry-demo",state="idle"}'
        ")"
    ),
    "memory_used_ratio": (
        "max by (service_name) ("
        'system_memory_utilization_ratio{'
        'service_namespace="opentelemetry-demo",state="used"}'
        ")"
    ),
}

_RESULT_COLUMNS = [
    "timestamp",
    "value",
    "metric_name",
    "service_name",
    "service_namespace",
]
_STEP_PATTERN = re.compile(r"^[1-9]\d*(?:ms|s|m|h|d|w)$")


class PrometheusAnomalyReaderError(RuntimeError):
    """Raised when Prometheus cannot return a valid metric range."""


class PrometheusAnomalyReader:
    """Fetch normalized service-level metric samples from Prometheus."""

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

        selected_queries = dict(
            DEFAULT_ANOMALY_QUERIES if queries is None else queries
        )
        if not selected_queries:
            raise ValueError("queries must contain at least one query")
        for name, query in selected_queries.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("metric query names must be non-empty strings")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"query for {name!r} must be a non-empty string")

        self.timeout_seconds = float(timeout_seconds)
        self.session = session if session is not None else requests
        self.queries = selected_queries

    @staticmethod
    def _parse_datetime(value: datetime | None, field: str) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if not isinstance(value, datetime):
            raise ValueError(f"{field} must be a datetime")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_step(step: str) -> str:
        if not isinstance(step, str) or not _STEP_PATTERN.fullmatch(step):
            raise ValueError("step must be a positive Prometheus duration such as '1m'")
        return step

    def fetch_metric(
        self,
        metric_name: str,
        *,
        minutes: int = 60,
        step: str = "1m",
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch one metric query and return normalized samples.

        The returned frame contains one row per service and timestamp. Series
        without ``service_name`` are ignored to keep the detector scoped to
        application services rather than cluster-wide infrastructure.
        """
        if metric_name not in self.queries:
            raise ValueError(f"Unknown anomaly metric: {metric_name}")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            raise ValueError("minutes must be a positive integer")
        step = self._validate_step(step)
        end_at = self._parse_datetime(end, "end")
        start_at = end_at - timedelta(minutes=minutes)

        try:
            response = self.session.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params={
                    "query": self.queries[metric_name],
                    "start": start_at.timestamp(),
                    "end": end_at.timestamp(),
                    "step": step,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PrometheusAnomalyReaderError(
                f"Prometheus request failed for {metric_name}: {exc}"
            ) from exc
        except (ValueError, TypeError) as exc:
            raise PrometheusAnomalyReaderError(
                f"Invalid Prometheus response for {metric_name}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or payload.get("status") != "success":
            detail = (
                payload.get("error", "unknown Prometheus error")
                if isinstance(payload, dict)
                else "response is not a JSON object"
            )
            raise PrometheusAnomalyReaderError(
                f"Prometheus query failed for {metric_name}: {detail}"
            )

        data = payload.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list):
            raise PrometheusAnomalyReaderError(
                f"Malformed Prometheus result for {metric_name}"
            )

        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, datetime]] = set()
        for series_index, series in enumerate(result):
            if not isinstance(series, dict):
                raise PrometheusAnomalyReaderError(
                    f"Malformed series {series_index} for {metric_name}"
                )
            labels = series.get("metric")
            samples = series.get("values")
            if not isinstance(labels, dict) or not isinstance(samples, list):
                raise PrometheusAnomalyReaderError(
                    f"Malformed labels/values for {metric_name}"
                )

            service_name = labels.get("service_name")
            if not isinstance(service_name, str) or not service_name.strip():
                continue
            service_namespace = labels.get("service_namespace")

            for sample in samples:
                if not isinstance(sample, (list, tuple)) or len(sample) != 2:
                    continue
                try:
                    epoch = float(sample[0])
                    value = float(sample[1])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(epoch) or not math.isfinite(value):
                    continue

                timestamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
                if timestamp < start_at or timestamp > end_at:
                    continue
                identity = (service_name.strip(), timestamp)
                if identity in seen:
                    raise PrometheusAnomalyReaderError(
                        "Duplicate service/timestamp for "
                        f"{metric_name}: {service_name} at {timestamp.isoformat()}"
                    )
                seen.add(identity)
                rows.append({
                    "timestamp": timestamp,
                    "value": value,
                    "metric_name": metric_name,
                    "service_name": service_name.strip(),
                    "service_namespace": service_namespace,
                })

        if not rows:
            return pd.DataFrame(columns=_RESULT_COLUMNS)
        return pd.DataFrame(rows, columns=_RESULT_COLUMNS).sort_values(
            ["service_name", "timestamp"], kind="stable"
        ).reset_index(drop=True)

    def fetch_all(
        self,
        *,
        minutes: int = 60,
        step: str = "1m",
        end: datetime | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch all configured metric queries independently.

        A failure raises with the metric name; callers can also call
        ``fetch_metric`` individually to implement per-metric partial success.
        """
        end_at = self._parse_datetime(end, "end")
        return {
            metric_name: self.fetch_metric(
                metric_name,
                minutes=minutes,
                step=step,
                end=end_at,
            )
            for metric_name in self.queries
        }
