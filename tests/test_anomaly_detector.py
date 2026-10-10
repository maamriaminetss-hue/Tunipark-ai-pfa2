from pathlib import Path
import sys

import pandas as pd
import pytest

DETECTOR_DIR = (
    Path(__file__).resolve().parents[1] / "platform" / "detector"
)
sys.path.insert(0, str(DETECTOR_DIR))

from anomaly_detector import detect_anomalies  # noqa: E402


def make_data(values, currency="USD"):
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z",
        periods=len(values),
        freq="30s",
    )
    return pd.DataFrame({
        "demo_payment_currency": currency,
        "timestamp": timestamps,
        "value": values,
    })


def test_mad_detects_a_large_spike():
    data = make_data([1.0] * 15 + [10.0])
    result = detect_anomalies(data, method="mad", min_history=12)
    assert bool(result.iloc[-1]["is_anomaly"]) is True
    assert result.iloc[-1]["reason"] == "deviation_from_constant_history"
    assert pd.isna(result.iloc[-1]["score"])


def test_ewma_detects_a_large_spike():
    normal = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00] * 3
    data = make_data(normal + [10.0])
    result = detect_anomalies(
        data, method="ewma", min_history=12, threshold=3.0
    )
    assert bool(result.iloc[-1]["is_anomaly"]) is True


def test_fixed_method_uses_configured_limits():
    data = make_data([0.01, 0.02, 0.08])
    result = detect_anomalies(
        data, method="fixed", fixed_lower=0.0, fixed_upper=0.05
    )
    assert result["is_anomaly"].tolist() == [False, False, True]
    assert result.iloc[-1]["reason"] == "above_fixed_upper"


def test_mad_does_not_flag_before_minimum_history():
    data = make_data([1.0, 1.0, 100.0])
    result = detect_anomalies(data, method="mad", min_history=5)
    assert not result["is_anomaly"].any()
    assert set(result["reason"]) == {"insufficient_history"}


def test_each_currency_is_processed_independently():
    usd = make_data([1.0] * 14 + [10.0], currency="USD")
    cad = make_data([1.0] * 15, currency="CAD")
    data = pd.concat([usd, cad], ignore_index=True)

    result = detect_anomalies(data, method="mad", min_history=12)
    usd_last = result[result["demo_payment_currency"] == "USD"].iloc[-1]
    cad_last = result[result["demo_payment_currency"] == "CAD"].iloc[-1]

    assert bool(usd_last["is_anomaly"]) is True
    assert bool(cad_last["is_anomaly"]) is False


def test_missing_required_column_is_rejected():
    with pytest.raises(ValueError, match="Colonnes obligatoires manquantes"):
        detect_anomalies(pd.DataFrame({"timestamp": []}), method="mad")


def test_fixed_method_requires_at_least_one_limit():
    with pytest.raises(ValueError, match="nécessite"):
        detect_anomalies(make_data([1.0, 2.0]), method="fixed")
def test_mad_handles_constant_history_with_small_variation():
    data = make_data([0.004167] * 12 + [0.008334])

    result = detect_anomalies(
        data,
        method="mad",
        min_history=12,
    )

    last = result.iloc[-1]

    assert bool(last["is_anomaly"]) is True
    assert last["reason"] == "deviation_from_constant_history"
    assert pd.isna(last["score"])