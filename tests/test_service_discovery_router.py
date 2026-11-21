from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from aiops_platform.api.routers import service_discovery


def test_sync_token_is_disabled_when_secret_is_not_configured(monkeypatch):
    monkeypatch.delenv("SERVICE_DISCOVERY_SYNC_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        service_discovery.require_sync_token("anything")

    assert exc_info.value.status_code == 503


def test_sync_token_rejects_a_wrong_secret(monkeypatch):
    monkeypatch.setenv("SERVICE_DISCOVERY_SYNC_TOKEN", "expected-secret")

    with pytest.raises(HTTPException) as exc_info:
        service_discovery.require_sync_token("wrong-secret")

    assert exc_info.value.status_code == 401


def test_sync_token_accepts_the_configured_secret(monkeypatch):
    monkeypatch.setenv("SERVICE_DISCOVERY_SYNC_TOKEN", "expected-secret")

    assert service_discovery.require_sync_token("expected-secret") is None


def test_sync_route_returns_partial_success_without_marking_failed_source_empty(
    monkeypatch,
):
    monkeypatch.setenv("SERVICE_DISCOVERY_SYNC_TOKEN", "expected-secret")
    monkeypatch.setattr(
        service_discovery.jaeger_reader,
        "fetch_services",
        lambda: ["frontend", "checkout"],
    )

    def fail_prometheus():
        raise RuntimeError("Prometheus offline")

    monkeypatch.setattr(
        service_discovery.prometheus_reader,
        "fetch_services",
        fail_prometheus,
    )

    upsert = Mock(return_value=2)
    monkeypatch.setattr(service_discovery, "upsert_service_discoveries", upsert)
    db = Mock()

    # Verify the dependency first, then invoke the endpoint body directly.
    service_discovery.require_sync_token("expected-secret")
    result = service_discovery.synchronize_service_discoveries(db, None)

    assert result["status"] == "partial"
    assert result["sources"]["jaeger"]["status"] == "ok"
    assert result["sources"]["prometheus"]["status"] == "error"
    assert result["sources"]["prometheus"]["error_type"] == "RuntimeError"
    upsert.assert_called_once()
    db.rollback.assert_called_once()


def test_sync_route_fails_with_502_when_both_sources_fail(monkeypatch):
    monkeypatch.setenv("SERVICE_DISCOVERY_SYNC_TOKEN", "expected-secret")
    monkeypatch.setattr(
        service_discovery.jaeger_reader,
        "fetch_services",
        Mock(side_effect=RuntimeError("Jaeger offline")),
    )
    monkeypatch.setattr(
        service_discovery.prometheus_reader,
        "fetch_services",
        Mock(side_effect=RuntimeError("Prometheus offline")),
    )
    db = Mock()

    with pytest.raises(HTTPException) as exc_info:
        service_discovery.synchronize_service_discoveries(db, None)

    assert exc_info.value.status_code == 502
    assert db.rollback.call_count == 2
