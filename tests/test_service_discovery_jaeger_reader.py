from unittest.mock import Mock

import pytest

from aiops_platform.intelligence.topology.jaeger_reader import (
    JaegerClientError,
    JaegerDependencyReader,
)


def make_response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_fetch_services_returns_sorted_unique_names(monkeypatch):
    monkeypatch.delenv("JAEGER_API_BASE_URL", raising=False)

    http_get = Mock(return_value=make_response(
        {"data": ["checkout", "frontend", "checkout", " ", None]}
    ))

    reader = JaegerDependencyReader(http_get=http_get)

    assert reader.fetch_services() == ["checkout", "frontend"]

    http_get.assert_called_once_with(
        "http://localhost:16686/api/services",
        params=None,
        timeout=10.0,
    )

def test_fetch_services_rejects_invalid_payload():
    reader = JaegerDependencyReader(
        http_get=Mock(return_value=make_response({"unexpected": []}))
    )

    with pytest.raises(JaegerClientError, match="services"):
        reader.fetch_services()
