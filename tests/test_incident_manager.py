
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "platform" / "incidents"),
)

from incident_manager import build_events, create_incidents


def make_row(
    timestamp,
    value=0.008334,
    currency="CAD",
    method="mad",
    is_anomaly=True,
):
    return {
        "demo_payment_currency": currency,
        "timestamp": timestamp,
        "value": value,
        "method": method,
        "expected": 0.004167,
        "score": 4.0,
        "is_anomaly": is_anomaly,
        "reason": "deviation_above_mad_limit",
    }


def test_build_events_ignores_normal_observations():
    data = pd.DataFrame([
        make_row("2026-10-10T05:44:05Z", is_anomaly=False),
        make_row("2026-10-10T05:44:35Z"),
    ])

    events = build_events(data)

    assert len(events) == 1
    assert events[0]["dimensions"]["demo_payment_currency"] == "CAD"


def test_build_events_merges_same_measurement_from_two_detectors():
    timestamp = "2026-10-10T05:44:05Z"

    data = pd.DataFrame([
        make_row(timestamp, method="mad"),
        make_row(timestamp, method="ewma"),
    ])

    events = build_events(data)

    assert len(events) == 1
    assert events[0]["methods"] == ["ewma", "mad"]
    assert events[0]["source_signal_count"] == 2


def test_events_are_correlated_into_two_incidents():
    timestamps = [
        "2026-10-10T05:44:05Z",
        "2026-10-10T05:44:35Z",
        "2026-10-10T05:48:05Z",
        "2026-10-10T05:48:35Z",
        "2026-10-10T05:49:05Z",
        "2026-10-10T05:49:35Z",
    ]

    data = pd.DataFrame([
        make_row(timestamp)
        for timestamp in timestamps
    ])

    incidents = create_incidents(
        data,
        correlation_window_seconds=90,
    )

    assert len(incidents) == 2
    assert [incident["event_count"] for incident in incidents] == [2, 4]
    assert all(incident["status"] == "open" for incident in incidents)


def test_events_from_different_currencies_are_not_merged():
    data = pd.DataFrame([
        make_row("2026-10-10T05:44:05Z", currency="CAD"),
        make_row("2026-10-10T05:44:35Z", currency="USD"),
    ])

    incidents = create_incidents(data)

    assert len(incidents) == 2


def test_incident_ids_are_deterministic():
    data = pd.DataFrame([
        make_row("2026-10-10T05:44:05Z"),
    ])

    first = create_incidents(data)
    second = create_incidents(data)

    assert first[0]["incident_id"] == second[0]["incident_id"]


def test_build_events_rejects_missing_columns():
    data = pd.DataFrame({
        "timestamp": ["2026-10-10T05:44:05Z"],
    })

    with pytest.raises(ValueError, match="Required columns missing"):
        build_events(data)


def test_negative_correlation_window_is_rejected():
    data = pd.DataFrame([
        make_row("2026-10-10T05:44:05Z"),
    ])

    with pytest.raises(ValueError, match="non-negative"):
        create_incidents(
            data,
            correlation_window_seconds=-1,
        )
