from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from aiops_platform.intelligence.monitoring.prometheus_freshness_reader import (
    PrometheusFreshnessReaderError,
    PrometheusMetricFreshnessReader,
)


def make_response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_fetch_sample_ages_returns_age_by_service():
    session = Mock(return_value=None)
    session.get.return_value = make_response({
        "status": "success",
        "data": {
            "result": [
                {"metric": {"service_name": "frontend"}, "value": [1, "12.5"]},
                {"metric": {"service_name": "ad"}, "value": [1, "34"]},
            ]
        },
    })
    reader = PrometheusMetricFreshnessReader(session=session)

    ages = reader.fetch_sample_ages(
        "http_5xx_ratio",
        end=datetime(2026, 11, 16, tzinfo=timezone.utc),
    )

    assert ages == {"frontend": 12.5, "ad": 34.0}
    assert session.get.call_args.kwargs["params"]["time"] == datetime(
        2026, 11, 16, tzinfo=timezone.utc
    ).timestamp()


def test_fetch_sample_ages_ignores_invalid_values():
    session = Mock()
    session.get.return_value = make_response({
        "status": "success",
        "data": {"result": [
            {"metric": {"service_name": "frontend"}, "value": [1, "NaN"]},
            {"metric": {}, "value": [1, "1"]},
            {"metric": {"service_name": "ad"}, "value": [1, "-1"]},
        ]},
    })
    reader = PrometheusMetricFreshnessReader(session=session)

    assert reader.fetch_sample_ages("http_5xx_ratio") == {}


def test_fetch_sample_ages_rejects_unknown_metric():
    reader = PrometheusMetricFreshnessReader(session=Mock())
    with pytest.raises(ValueError, match="Unknown freshness metric"):
        reader.fetch_sample_ages("unknown")


def test_fetch_sample_ages_wraps_http_errors():
    session = Mock()
    session.get.side_effect = requests.Timeout("timeout")
    reader = PrometheusMetricFreshnessReader(session=session)

    with pytest.raises(PrometheusFreshnessReaderError, match="freshness request failed"):
        reader.fetch_sample_ages("http_5xx_ratio")
