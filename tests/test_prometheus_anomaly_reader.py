from datetime import datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from aiops_platform.intelligence.monitoring.prometheus_anomaly_reader import (
    DEFAULT_ANOMALY_QUERIES,
    TUNIPARK_ANOMALY_QUERIES,
    PrometheusAnomalyReader,
    PrometheusAnomalyReaderError,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_reader_fetches_and_normalizes_service_samples():
    http = Mock(return_value=FakeResponse({
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{
                "metric": {
                    "service_name": "frontend",
                    "service_namespace": "opentelemetry-demo",
                },
                "values": [[1794852000, "0.12"], [1794852060, "0.18"]],
            }],
        },
    }))
    reader = PrometheusAnomalyReader(
        prometheus_url="http://localhost:9090",
        session=Mock(get=http),
        queries={"http_5xx_ratio": "up"},
    )

    frame = reader.fetch_metric(
        "http_5xx_ratio",
        minutes=10,
        step="1m",
        end=datetime.fromtimestamp(1794852060, tz=timezone.utc),
    )

    assert list(frame.columns) == [
        "timestamp", "value", "metric_name", "service_name", "service_namespace"
    ]
    assert frame["service_name"].tolist() == ["frontend", "frontend"]
    assert frame["value"].tolist() == [0.12, 0.18]
    assert pd.api.types.is_datetime64_any_dtype(frame["timestamp"])
    request_kwargs = http.call_args.kwargs
    assert request_kwargs["params"]["query"] == "up"
    assert request_kwargs["params"]["step"] == "1m"
    assert request_kwargs["timeout"] == 10.0


def test_reader_ignores_series_without_service_name_and_non_finite_values():
    http = Mock(return_value=FakeResponse({
        "status": "success",
        "data": {"result": [
            {"metric": {"job": "prometheus"}, "values": [[1794852060, "4"]]},
            {"metric": {"service_name": "frontend"}, "values": [
                [1794852000, "NaN"], [1794852060, "+Inf"], [1794852060, "0.25"]
            ]},
        ]},
    }))
    reader = PrometheusAnomalyReader(
        session=Mock(get=http), queries={"metric": "up"}
    )
    frame = reader.fetch_metric(
        "metric", minutes=10,
        end=datetime.fromtimestamp(1794852060, tz=timezone.utc),
    )
    assert len(frame) == 1
    assert frame.iloc[0]["value"] == 0.25


def test_reader_returns_empty_frame_for_valid_empty_snapshot():
    http = Mock(return_value=FakeResponse({
        "status": "success", "data": {"result": []}
    }))
    reader = PrometheusAnomalyReader(
        session=Mock(get=http), queries={"metric": "up"}
    )
    frame = reader.fetch_metric("metric", minutes=5)
    assert frame.empty
    assert "service_name" in frame.columns


def test_reader_rejects_prometheus_api_error():
    http = Mock(return_value=FakeResponse({
        "status": "error", "error": "bad query"
    }))
    reader = PrometheusAnomalyReader(
        session=Mock(get=http), queries={"metric": "up"}
    )
    with pytest.raises(PrometheusAnomalyReaderError, match="bad query"):
        reader.fetch_metric("metric", minutes=5)


def test_reader_wraps_http_errors():
    http = Mock(side_effect=requests.Timeout("timed out"))
    reader = PrometheusAnomalyReader(
        session=Mock(get=http), queries={"metric": "up"}
    )
    with pytest.raises(PrometheusAnomalyReaderError, match="request failed"):
        reader.fetch_metric("metric", minutes=5)


def test_reader_rejects_bad_inputs():
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        PrometheusAnomalyReader(prometheus_url="localhost:9090")

    reader = PrometheusAnomalyReader(queries={"metric": "up"})
    with pytest.raises(ValueError, match="positive integer"):
        reader.fetch_metric("metric", minutes=0)
    with pytest.raises(ValueError, match="positive Prometheus duration"):
        reader.fetch_metric("metric", minutes=5, step="0m")
    with pytest.raises(ValueError, match="Unknown anomaly metric"):
        reader.fetch_metric("unknown", minutes=5)


def test_default_queries_target_application_metrics_not_infrastructure():
    assert set(DEFAULT_ANOMALY_QUERIES) == {
        "http_5xx_ratio",
        "http_p95_latency_seconds",
        "cpu_busy_ratio",
        "memory_used_ratio",
    }
    for query in DEFAULT_ANOMALY_QUERIES.values():
        assert 'service_namespace="opentelemetry-demo"' in query


def test_fetch_all_reuses_one_consistent_end_timestamp():
    http = Mock(return_value=FakeResponse({
        "status": "success", "data": {"result": []}
    }))
    reader = PrometheusAnomalyReader(
        session=Mock(get=http),
        queries={"m1": "up", "m2": "process_cpu_seconds_total"},
    )
    end = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)

    result = reader.fetch_all(minutes=20, step="1m", end=end)

    assert set(result) == {"m1", "m2"}
    assert all(frame.empty for frame in result.values())
    assert http.call_count == 2
    assert http.call_args_list[0].kwargs["params"]["end"] == end.timestamp()
    assert http.call_args_list[1].kwargs["params"]["end"] == end.timestamp()


def test_default_promql_parentheses_are_balanced():
    for query in DEFAULT_ANOMALY_QUERIES.values():
        depth = 0
        for char in query:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                assert depth >= 0
        assert depth == 0

def test_tunipark_queries_normalize_exported_job():
    assert set(TUNIPARK_ANOMALY_QUERIES) == {
        "tunipark_http_5xx_ratio",
        "tunipark_http_p95_latency_seconds",
    }

    for query in TUNIPARK_ANOMALY_QUERIES.values():
        assert 'exported_job="tunipark-ai-backend"' in query
        assert '"service_name", "$1", "exported_job"' in query

    for query in TUNIPARK_ANOMALY_QUERIES.values():
        depth = 0
        for char in query:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                assert depth >= 0
        assert depth == 0
