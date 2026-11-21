from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd

from aiops_platform.intelligence.monitoring.anomaly_detection_service import (
    AnomalyDetectionService,
)


class FakeReader:
    queries = {"http_5xx_ratio": "unused"}
    prometheus_url = "http://prometheus.test"

    def __init__(self, frame):
        self.frame = frame

    def fetch_metric(self, metric_name, *, minutes, step, end):
        return self.frame.copy()


class FakeFreshnessReader:
    def __init__(self, ages):
        self.ages = ages

    def fetch_sample_ages(self, metric_name, *, end):
        return dict(self.ages)


class FakeState:
    def __init__(self, last_processed_at=None, is_active=False):
        self.last_processed_at = last_processed_at
        self.anomaly_streak = 0
        self.recovery_streak = 0
        self.is_active = is_active
        self.last_event_id = None


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, statement):
        return None  # No business Service row is required for a metric event.

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_frame(values, start=None):
    start = start or datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
    timestamps = [start + timedelta(minutes=index) for index in range(len(values))]
    return pd.DataFrame({
        "timestamp": timestamps,
        "value": values,
        "metric_name": "http_5xx_ratio",
        "service_name": "frontend",
        "service_namespace": "opentelemetry-demo",
    })


def make_service(frame, *, ages=None, fixed_limits=None):
    return AnomalyDetectionService(
        reader=FakeReader(frame),
        freshness_reader=FakeFreshnessReader(ages or {"frontend": 10.0}),
        min_history=12,
        window=12,
        confirmation_samples=2,
        recovery_samples=2,
        fixed_upper_limits=(
            {"http_5xx_ratio": 0.05}
            if fixed_limits is None else fixed_limits
        ),
    )


def test_analyze_metric_frame_detects_fixed_guardrail():
    service = make_service(make_frame([0.01] * 12 + [0.20, 0.25]))

    result = service.analyze_metric_frame(
        "http_5xx_ratio",
        make_frame([0.01] * 12 + [0.20, 0.25]),
    )

    assert result[-1]["is_anomaly"] is True
    assert "fixed" in result[-1]["methods"]
    assert result[-1]["service_name"] == "frontend"


def test_analyze_metric_frame_keeps_normal_samples_clear():
    frame = make_frame([0.01, 0.011, 0.009, 0.012] * 4 + [0.01, 0.011])
    service = make_service(frame)

    result = service.analyze_metric_frame("http_5xx_ratio", frame)

    assert not any(item["is_anomaly"] for item in result[-2:])


def test_run_once_creates_one_event_after_two_confirmations(monkeypatch):
    frame = make_frame([0.01] * 12 + [0.20, 0.25])
    service = make_service(frame)
    state = FakeState()
    events = []

    def fake_lock_state(db, metric_name, service_name):
        assert metric_name == "http_5xx_ratio"
        assert service_name == "frontend"
        return state

    def fake_create_event(**kwargs):
        events.append(kwargs)
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.lock_or_create_detection_state",
        fake_lock_state,
    )
    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.create_event",
        fake_create_event,
    )

    db = FakeDB()
    result = service.run_once(db)

    assert result["status"] == "ok"
    assert result["events_created"] == 1
    assert len(events) == 1
    assert events[0]["event_type"] == "metric_anomaly"
    assert events[0]["severity"] == "high"
    assert events[0]["commit"] is False
    assert state.is_active is True
    assert state.anomaly_streak == 2
    assert db.commits == 1


def test_run_once_does_not_duplicate_an_active_episode(monkeypatch):
    start = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
    frame = make_frame([0.01] * 12 + [0.20, 0.25], start=start)
    state = FakeState(last_processed_at=frame.iloc[-1]["timestamp"], is_active=True)
    state.anomaly_streak = 2
    service = make_service(frame)
    events = []

    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.lock_or_create_detection_state",
        lambda *args, **kwargs: state,
    )
    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.create_event",
        lambda **kwargs: events.append(kwargs),
    )

    result = service.run_once(FakeDB())

    assert result["events_created"] == 0
    assert events == []


def test_run_once_skips_stale_service_samples(monkeypatch):
    frame = make_frame([0.01] * 12 + [0.20, 0.25])
    service = make_service(frame, ages={"frontend": 600.0})

    def unexpected_state(*args, **kwargs):
        raise AssertionError("Stale service must not update detection state")

    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.lock_or_create_detection_state",
        unexpected_state,
    )

    result = service.run_once(FakeDB())

    assert result["events_created"] == 0
    assert result["stale_service_series_skipped"] == 1


def test_run_once_rearms_after_consecutive_normal_samples(monkeypatch):
    start = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
    frame = make_frame([0.01] * 12 + [0.01, 0.01], start=start)
    state = FakeState(
        last_processed_at=start + timedelta(minutes=11),
        is_active=True,
    )
    state.anomaly_streak = 2
    service = make_service(frame)

    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.lock_or_create_detection_state",
        lambda *args, **kwargs: state,
    )
    monkeypatch.setattr(
        "aiops_platform.intelligence.monitoring.anomaly_detection_service.create_event",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("No event expected")),
    )

    result = service.run_once(FakeDB())

    assert result["events_created"] == 0
    assert state.is_active is False
    assert state.recovery_streak == 0
