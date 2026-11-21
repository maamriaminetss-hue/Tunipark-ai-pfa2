from datetime import datetime, timezone

import pytest

from aiops_platform.intelligence.verification.prometheus_resolution_reader import (
    PrometheusReaderError,
    PrometheusResolutionReader,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return FakeResponse(self.payloads.pop(0))


def success(values, labels=None):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{
                "metric": {"service_name": "frontend", **(labels or {})},
                "values": values,
            }],
        },
    }


def test_fetch_observations_normalizes_prometheus_matrix():
    session = FakeSession([success([[1794607800, "0.2"], [1794607830, "0.3"]])])
    reader = PrometheusResolutionReader(session=session)
    records = reader.fetch_observations(
        "frontend",
        "2026-11-13T22:10:00Z",
        "2026-11-13T22:11:00Z",
        queries={"http_p95_latency_seconds": "up"},
    )
    assert len(records) == 2
    assert records[0]["service_name"] == "frontend"
    assert records[0]["metric_name"] == "http_p95_latency_seconds"
    assert records[0]["value"] == 0.2
    assert records[0]["timestamp"].endswith("Z")
    assert records[0]["labels"]["service_name"] == "frontend"
    assert session.calls[0][0].endswith("/api/v1/query_range")


def test_fetch_observations_filters_other_services():
    payload = success([[1794607800, "0.2"]])
    payload["data"]["result"][0]["metric"]["service_name"] = "shipping"
    reader = PrometheusResolutionReader(session=FakeSession([payload]))
    records = reader.fetch_observations(
        "frontend", "2026-11-13T22:10:00Z", "2026-11-13T22:11:00Z",
        queries={"latency": "up"},
    )
    assert records == []


def test_fetch_observations_skips_nan_and_infinity():
    session = FakeSession([success([[1794607800, "NaN"], [1794607830, "+Inf"], [1794607860, "0.4"]])])
    reader = PrometheusResolutionReader(session=session)
    records = reader.fetch_observations(
        "frontend", "2026-11-13T22:10:00Z", "2026-11-13T22:11:00Z",
        queries={"latency": "up"},
    )
    assert [item["value"] for item in records] == [0.4]


def test_fetch_observations_rejects_duplicate_timestamps():
    payload = {
        "status": "success",
        "data": {"result": [
            {"metric": {"service_name": "frontend", "route": "/a"}, "values": [[1794607800, "0.2"]]},
            {"metric": {"service_name": "frontend", "route": "/b"}, "values": [[1794607800, "0.3"]]},
        ]},
    }
    reader = PrometheusResolutionReader(session=FakeSession([payload]))
    with pytest.raises(PrometheusReaderError, match="duplicate timestamp"):
        reader.fetch_observations(
            "frontend", "2026-11-13T22:10:00Z", "2026-11-13T22:11:00Z",
            queries={"latency": "up"},
        )


@pytest.mark.parametrize("step", ["0s", "30", "1x", "", " 30s"])
def test_invalid_step_is_rejected(step):
    reader = PrometheusResolutionReader(session=FakeSession([]))
    with pytest.raises(ValueError, match="step"):
        reader.fetch_observations(
            "frontend", "2026-11-13T22:10:00Z", "2026-11-13T22:11:00Z",
            step=step, queries={"latency": "up"},
        )


def test_end_before_start_is_rejected():
    reader = PrometheusResolutionReader(session=FakeSession([]))
    with pytest.raises(ValueError, match="end"):
        reader.fetch_observations(
            "frontend", "2026-11-13T22:11:00Z", "2026-11-13T22:10:00Z",
            queries={"latency": "up"},
        )


def test_prometheus_error_is_reported():
    reader = PrometheusResolutionReader(
        session=FakeSession([{"status": "error", "error": "bad query"}])
    )
    with pytest.raises(PrometheusReaderError, match="bad query"):
        reader.fetch_observations(
            "frontend", "2026-11-13T22:10:00Z", "2026-11-13T22:11:00Z",
            queries={"latency": "up"},
        )


def test_default_queries_include_latency_and_5xx_ratio():
    from aiops_platform.intelligence.verification.prometheus_resolution_reader import DEFAULT_RESOLUTION_QUERIES
    assert set(DEFAULT_RESOLUTION_QUERIES) == {
        "http_p95_latency_seconds", "http_5xx_error_ratio"
    }
    assert 'http_response_status_code=~"5.."' in DEFAULT_RESOLUTION_QUERIES["http_5xx_error_ratio"]
