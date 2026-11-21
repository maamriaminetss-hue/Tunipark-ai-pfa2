from datetime import datetime, timedelta, timezone

import pandas as pd

from aiops_platform.intelligence.monitoring.anomaly_detection_service import (
    AnomalyDetectionService,
)


class EmptyReader:
    queries = {}


class EmptyFreshnessReader:
    pass


def make_frame(values, metric_name, service_name):
    start = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
    return pd.DataFrame({
        "timestamp": [start + timedelta(minutes=i) for i in range(len(values))],
        "value": values,
        "metric_name": metric_name,
        "service_name": service_name,
        "service_namespace": "opentelemetry-demo",
    })


def make_service():
    return AnomalyDetectionService(
        reader=EmptyReader(),
        freshness_reader=EmptyFreshnessReader(),
        window=12,
        min_history=12,
        fixed_upper_limits={
            "http_5xx_ratio": 0.05,
            "http_p95_latency_seconds": 1.0,
            "cpu_busy_ratio": 0.90,
            "memory_used_ratio": 0.90,
        },
    )


def test_mad_filters_tiny_absolute_latency_change_on_flat_history():
    frame = make_frame(
        [0.00475] * 12 + [0.0095, 0.0095],
        "http_p95_latency_seconds",
        "ad",
    )

    result = make_service().analyze_metric_frame(
        "http_p95_latency_seconds", frame
    )

    assert result[-1]["is_anomaly"] is False
    assert "mad" not in result[-1]["methods"]
    assert result[-1]["reasons"]["mad"] == (
        "below_metric_minimum_absolute_deviation"
    )


def test_mad_filters_small_memory_shift_below_absolute_floor():
    baseline = 0.4457719989817466
    current = 0.4733184917219408
    frame = make_frame(
        [baseline] * 12 + [current, current],
        "memory_used_ratio",
        "recommendation",
    )

    result = make_service().analyze_metric_frame("memory_used_ratio", frame)

    assert result[-1]["is_anomaly"] is False
    assert "mad" not in result[-1]["methods"]
    assert result[-1]["reasons"]["mad"] == (
        "below_metric_minimum_absolute_deviation"
    )


def test_mad_and_fixed_guardrail_keep_material_cpu_spike():
    baseline = 0.2397
    current = 0.9365
    frame = make_frame(
        [baseline] * 12 + [current, current],
        "cpu_busy_ratio",
        "recommendation",
    )

    result = make_service().analyze_metric_frame("cpu_busy_ratio", frame)

    assert result[-1]["is_anomaly"] is True
    assert "mad" in result[-1]["methods"]
    assert "fixed" in result[-1]["methods"]


def test_http_5xx_mad_candidate_above_floor_is_preserved():
    frame = make_frame(
        [0.01] * 12 + [0.025, 0.025],
        "http_5xx_ratio",
        "frontend",
    )

    result = make_service().analyze_metric_frame("http_5xx_ratio", frame)

    assert result[-1]["is_anomaly"] is True
    assert "mad" in result[-1]["methods"]


def test_minimum_mad_deviation_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv(
        "ANOMALY_MAD_MIN_DEVIATION_HTTP_P95_LATENCY_SECONDS",
        "0.001",
    )
    service = AnomalyDetectionService(
        reader=EmptyReader(),
        freshness_reader=EmptyFreshnessReader(),
    )

    assert service.mad_minimum_absolute_deviations[
        "http_p95_latency_seconds"
    ] == 0.001
