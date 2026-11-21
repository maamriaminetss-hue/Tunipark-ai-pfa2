"""Read-only Prometheus range reader for post-remediation verification."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import requests


DEFAULT_RESOLUTION_QUERIES = {
    "http_p95_latency_seconds": (
        'histogram_quantile(0.95, '
        'sum by (service_name, le) '
        '(rate(http_server_request_duration_seconds_bucket[5m])))'
    ),
    "http_5xx_error_ratio": (
        '('
        'sum by (service_name) '
        '(rate(http_server_request_duration_seconds_count'
        '{http_response_status_code=~"5.."}[5m])) '
        'or on (service_name) '
        '(0 * sum by (service_name) '
        '(rate(http_server_request_duration_seconds_count[5m])))'
        ') '
        '/ on (service_name) '
        'sum by (service_name) '
        '(rate(http_server_request_duration_seconds_count[5m]))'
    ),
}


class PrometheusReaderError(RuntimeError):
    """Raised when Prometheus cannot provide an unambiguous valid response."""


class PrometheusResolutionReader:
    """Fetch and normalize time-series observations from Prometheus."""

    def __init__(
        self,
        prometheus_url: str = "http://localhost:9090",
        timeout: float = 10.0,
        session: Any = None,
    ) -> None:
        if not isinstance(prometheus_url, str) or not prometheus_url.strip():
            raise ValueError("prometheus_url must be a non-empty string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self.prometheus_url = prometheus_url.rstrip("/")
        self.timeout = float(timeout)
        self.session = session if session is not None else requests

    @staticmethod
    def _parse_time(value: datetime | str, field: str) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Invalid {field}: {value!r}") from exc
        else:
            raise ValueError(f"{field} must be a datetime or ISO-8601 string")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _validate_step(step: str) -> str:
        if not isinstance(step, str) or not re.fullmatch(r"[1-9]\d*(?:ms|s|m|h|d|w)", step):
            raise ValueError("step must be a positive Prometheus duration such as '30s' or '1m'")
        return step

    def fetch_observations(
        self,
        service_name: str,
        start: datetime | str,
        end: datetime | str,
        step: str = "30s",
        queries: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return normalized records for one service over an explicit time window.

        Each record has service_name, metric_name, timestamp, value and labels.
        Missing/NaN samples are skipped; duplicate points for the same metric and
        timestamp are rejected instead of silently overwriting data.
        """
        if not isinstance(service_name, str) or not service_name.strip():
            raise ValueError("service_name must be a non-empty string")
        service_name = service_name.strip()
        start_at = self._parse_time(start, "start")
        end_at = self._parse_time(end, "end")
        if end_at < start_at:
            raise ValueError("end must be greater than or equal to start")
        step = self._validate_step(step)
        selected_queries = dict(DEFAULT_RESOLUTION_QUERIES if queries is None else queries)
        if not selected_queries:
            raise ValueError("queries must contain at least one metric query")
        for metric_name, query in selected_queries.items():
            if not isinstance(metric_name, str) or not metric_name.strip():
                raise ValueError("query metric names must be non-empty strings")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"query for {metric_name!r} must be a non-empty string")

        url = f"{self.prometheus_url}/api/v1/query_range"
        observations: list[dict[str, Any]] = []
        seen: set[tuple[str, datetime]] = set()

        for metric_name, query in selected_queries.items():
            try:
                response = self.session.get(
                    url,
                    params={
                        "query": query,
                        "start": start_at.timestamp(),
                        "end": end_at.timestamp(),
                        "step": step,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException as exc:
                raise PrometheusReaderError(
                    f"Prometheus request failed for {metric_name}: {exc}"
                ) from exc
            except (ValueError, TypeError) as exc:
                raise PrometheusReaderError(
                    f"Invalid Prometheus response for {metric_name}: {exc}"
                ) from exc

            if not isinstance(payload, dict) or payload.get("status") != "success":
                detail = payload.get("error", "unknown Prometheus error") if isinstance(payload, dict) else "invalid JSON object"
                raise PrometheusReaderError(f"Prometheus query failed for {metric_name}: {detail}")

            data = payload.get("data")
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, list):
                raise PrometheusReaderError(f"Malformed Prometheus result for {metric_name}")

            for series in result:
                if not isinstance(series, dict):
                    raise PrometheusReaderError(f"Malformed time series for {metric_name}")
                labels = series.get("metric")
                values = series.get("values")
                if not isinstance(labels, dict) or not isinstance(values, list):
                    raise PrometheusReaderError(f"Malformed labels/values for {metric_name}")
                if labels.get("service_name") != service_name:
                    continue

                for sample in values:
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
                    identity = (metric_name, timestamp)
                    if identity in seen:
                        raise PrometheusReaderError(
                            f"Ambiguous duplicate timestamp for {service_name}/{metric_name} at {timestamp.isoformat()}"
                        )
                    seen.add(identity)
                    observations.append({
                        "service_name": service_name,
                        "metric_name": metric_name,
                        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                        "value": value,
                        "labels": dict(labels),
                    })

        observations.sort(key=lambda item: (item["metric_name"], item["timestamp"]))
        return observations
