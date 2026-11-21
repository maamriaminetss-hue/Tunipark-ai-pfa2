from unittest.mock import Mock

import pytest
import requests

from aiops_platform.intelligence.topology.jaeger_reader import (
    JaegerClientError,
    JaegerDependencyReader,
)


def make_response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_fetch_topology_builds_graph_and_ignores_self_calls():
    http_get = Mock(
        side_effect=[
            make_response({
                "services": ["frontend", "catalog"],
            }),
            make_response({
                "data": [
                    {
                        "parent": "frontend",
                        "child": "catalog",
                        "callCount": 10,
                    },
                    {
                        "parent": "catalog",
                        "child": "catalog",
                        "callCount": 20,
                    },
                ],
            }),
        ]
    )

    reader = JaegerDependencyReader(
        api_base_url="http://localhost:16686/jaeger/ui/api",
        http_get=http_get,
    )

    result = reader.fetch_topology(
        lookback_ms=60_000,
        end_ts=123456,
    )

    assert result["source"] == "jaeger"
    assert result["topology"]["dependencies"]["frontend"] == ["catalog"]
    assert result["topology"]["dependencies"]["catalog"] == []
    assert result["edges"] == [
        {
            "parent": "frontend",
            "child": "catalog",
            "call_count": 10,
        }
    ]
    assert result["rejected_edges"][0]["reason"] == "self_call_ignored"

    assert http_get.call_count == 2

    dependency_call = http_get.call_args_list[1]
    assert dependency_call.kwargs["params"] == {
        "endTs": 123456,
        "lookback": 60_000,
    }


def test_fetch_topology_rejects_circular_dependency():
    http_get = Mock(
        side_effect=[
            make_response({"services": ["frontend", "catalog"]}),
            make_response({
                "data": [
                    {
                        "parent": "frontend",
                        "child": "catalog",
                        "callCount": 10,
                    },
                    {
                        "parent": "catalog",
                        "child": "frontend",
                        "callCount": 5,
                    },
                ],
            }),
        ]
    )

    reader = JaegerDependencyReader(http_get=http_get)

    result = reader.fetch_topology(end_ts=123456)

    assert len(result["edges"]) == 1
    assert len(result["rejected_edges"]) == 1
    assert "cycle" in result["rejected_edges"][0]["reason"].lower()


def test_fetch_topology_adds_services_found_in_dependencies():
    http_get = Mock(
        side_effect=[
            make_response({"services": ["frontend"]}),
            make_response({
                "data": [
                    {
                        "parent": "frontend",
                        "child": "catalog",
                        "callCount": 7,
                    },
                ],
            }),
        ]
    )

    reader = JaegerDependencyReader(http_get=http_get)

    result = reader.fetch_topology(end_ts=123456)

    assert result["topology"]["dependencies"]["frontend"] == ["catalog"]
    assert "catalog" in result["topology"]["services"]
    assert any("catalog" in warning for warning in result["warnings"])


def test_fetch_topology_rejects_invalid_services_response():
    http_get = Mock(
        return_value=make_response({"unexpected": []})
    )

    reader = JaegerDependencyReader(http_get=http_get)

    with pytest.raises(JaegerClientError, match="services"):
        reader.fetch_topology()


def test_fetch_topology_handles_http_error():
    http_get = Mock(side_effect=requests.Timeout("Jaeger timeout"))

    reader = JaegerDependencyReader(http_get=http_get)

    with pytest.raises(JaegerClientError, match="Jaeger"):
        reader.fetch_topology()


def test_invalid_lookback_is_rejected_before_http_request():
    http_get = Mock()
    reader = JaegerDependencyReader(http_get=http_get)

    with pytest.raises(ValueError, match="lookback_ms"):
        reader.fetch_topology(lookback_ms=0)

    http_get.assert_not_called()


def test_invalid_base_url_is_rejected():
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        JaegerDependencyReader(api_base_url="localhost:16686")