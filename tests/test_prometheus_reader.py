import sys
from pathlib import Path

import pandas as pd
import pytest

# Import the reader without conflicting with Python's standard "platform" module.
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "platform" / "detector"),
)

import prometheus_reader


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_fetch_range_converts_prometheus_samples(monkeypatch):
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "service_name": "payment",
                        "demo_payment_currency": "USD",
                    },
                    "values": [
                        [1791528000, "0.02"],
                        [1791528030, "0.03"],
                    ],
                }
            ],
        },
    }

    def fake_get(url, params, timeout):
        assert url == "http://localhost:9090/api/v1/query_range"
        assert params["query"] == "up"
        assert timeout == 10
        return FakeResponse(payload)

    monkeypatch.setattr(prometheus_reader.requests, "get", fake_get)

    df = prometheus_reader.fetch_range(
        "http://localhost:9090",
        "up",
        minutes=5,
    )

    assert len(df) == 2
    assert df["value"].tolist() == [0.02, 0.03]
    assert df["service_name"].tolist() == ["payment", "payment"]
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])


def test_fetch_range_returns_empty_dataframe(monkeypatch):
    payload = {
        "status": "success",
        "data": {"resultType": "matrix", "result": []},
    }

    monkeypatch.setattr(
        prometheus_reader.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    df = prometheus_reader.fetch_range(
        "http://localhost:9090",
        "up{unknown_label='unknown'}",
        minutes=5,
    )

    assert df.empty


def test_fetch_range_rejects_invalid_duration():
    with pytest.raises(ValueError, match="minutes must be greater than zero"):
        prometheus_reader.fetch_range(
            "http://localhost:9090",
            "up",
            minutes=0,
        )
