from unittest.mock import Mock

import pytest
import requests

from aiops_platform.intelligence.discovery.prometheus_service_reader import (
    PrometheusServiceDiscoveryError,
    PrometheusServiceDiscoveryReader,
)


def make_response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_fetch_services_returns_sorted_unique_names_and_ignores_invalid_items():
    http_get = Mock(
        return_value=make_response(
            {
                "status": "success",
                "data": ["checkout", " frontend ", "checkout", "", None, 42],
            }
        )
    )
    reader = PrometheusServiceDiscoveryReader(
        base_url="http://localhost:9090/",
        http_get=http_get,
    )

    assert reader.fetch_services() == ["checkout", "frontend"]
    http_get.assert_called_once_with(
        "http://localhost:9090/api/v1/label/service_name/values",
        timeout=5.0,
    )


def test_fetch_services_accepts_a_valid_empty_snapshot():
    reader = PrometheusServiceDiscoveryReader(
        http_get=Mock(return_value=make_response({"status": "success", "data": []}))
    )

    assert reader.fetch_services() == []


def test_fetch_services_rejects_invalid_data_shape():
    reader = PrometheusServiceDiscoveryReader(
        http_get=Mock(return_value=make_response({"status": "success", "data": {}}))
    )

    with pytest.raises(PrometheusServiceDiscoveryError, match="list named 'data'"):
        reader.fetch_services()


def test_fetch_services_rejects_prometheus_api_error():
    reader = PrometheusServiceDiscoveryReader(
        http_get=Mock(
            return_value=make_response(
                {"status": "error", "error": "query execution failed"}
            )
        )
    )

    with pytest.raises(PrometheusServiceDiscoveryError, match="query execution failed"):
        reader.fetch_services()


def test_fetch_services_wraps_http_errors():
    reader = PrometheusServiceDiscoveryReader(
        http_get=Mock(side_effect=requests.Timeout("Prometheus timeout"))
    )

    with pytest.raises(PrometheusServiceDiscoveryError, match="Prometheus timeout"):
        reader.fetch_services()


def test_invalid_base_url_is_rejected():
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        PrometheusServiceDiscoveryReader(base_url="localhost:9090")
