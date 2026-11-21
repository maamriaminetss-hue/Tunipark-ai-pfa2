import pytest
from fastapi import HTTPException

from aiops_platform.api.routers import anomaly_detection


def test_detection_token_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("ANOMALY_DETECTION_RUN_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        anomaly_detection.require_detection_token("anything")
    assert exc_info.value.status_code == 503


def test_detection_token_rejects_wrong_value(monkeypatch):
    monkeypatch.setenv("ANOMALY_DETECTION_RUN_TOKEN", "correct-token")
    with pytest.raises(HTTPException) as exc_info:
        anomaly_detection.require_detection_token("wrong-token")
    assert exc_info.value.status_code == 401


def test_detection_token_accepts_configured_value(monkeypatch):
    monkeypatch.setenv("ANOMALY_DETECTION_RUN_TOKEN", "correct-token")
    assert anomaly_detection.require_detection_token("correct-token") is None


def test_run_route_returns_detection_cycle_result(monkeypatch):
    expected = {"status": "ok", "events_created": 1}
    monkeypatch.setattr(
        anomaly_detection.detection_service,
        "run_once",
        lambda db: expected,
    )

    result = anomaly_detection.run_anomaly_detection(
        db=object(),
        _authorized=None,
    )
    assert result == expected


def test_detection_route_is_registered():
    paths = {route.path for route in anomaly_detection.router.routes}
    assert "/anomaly-detection/run" in paths
