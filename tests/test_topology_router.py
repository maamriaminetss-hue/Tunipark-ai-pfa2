import pytest
from fastapi import HTTPException

from aiops_platform.api.routers import topology
from aiops_platform.intelligence.topology.jaeger_reader import (
    JaegerClientError,
)


def test_topology_route_returns_reader_result(monkeypatch):
    expected = {
        "source": "jaeger",
        "topology": {
            "services": {
                "frontend": {"source": "jaeger"},
            },
            "dependencies": {
                "frontend": [],
            },
        },
        "edges": [],
        "rejected_edges": [],
        "warnings": [],
    }

    def fake_fetch_topology(lookback_ms, end_ts):
        assert lookback_ms == 60_000
        assert end_ts == 123456
        return expected

    monkeypatch.setattr(
        topology.reader,
        "fetch_topology",
        fake_fetch_topology,
    )

    result = topology.get_topology(
        lookback_ms=60_000,
        end_ts=123456,
    )

    assert result == expected


def test_topology_route_returns_502_when_jaeger_fails(monkeypatch):
    def fake_fetch_topology(lookback_ms, end_ts):
        raise JaegerClientError("Connection timeout")

    monkeypatch.setattr(
        topology.reader,
        "fetch_topology",
        fake_fetch_topology,
    )

    with pytest.raises(HTTPException) as exc_info:
        topology.get_topology(
            lookback_ms=60_000,
            end_ts=123456,
        )

    assert exc_info.value.status_code == 502
    assert "Jaeger service unavailable" in exc_info.value.detail


def test_topology_route_is_registered_in_openapi():
    from aiops_platform.api.main import app

    schema = app.openapi()

    assert "/topology" in schema["paths"]
    assert "get" in schema["paths"]["/topology"]