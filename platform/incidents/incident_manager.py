
"""Build anomaly events and correlate them into incidents."""

from __future__ import annotations
from aiops_observability.telemetry import observe
import json
import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

import pandas as pd


EVENT_NAMESPACE = UUID("f17a7d13-b11e-4c84-9b4e-65a920a8a901")
INCIDENT_NAMESPACE = UUID("6cbb57ad-2bf2-4a70-b44e-7ee6d09f5f21")

REQUIRED_COLUMNS = {"timestamp", "value", "is_anomaly"}

SIGNAL_COLUMNS = {
    "timestamp",
    "value",
    "method",
    "expected",
    "score",
    "is_anomaly",
    "reason",
}


def _is_anomaly(value: Any) -> bool:
    """Interpret boolean values read from CSV or DataFrames."""
    if value is None or pd.isna(value):
        return False

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()

    if normalized in {"true", "1", "yes"}:
        return True

    if normalized in {"false", "0", "no", ""}:
        return False

    raise ValueError(f"Invalid is_anomaly value: {value!r}")


def _clean_dimension(value: Any) -> Any:
    """Convert dimension values to JSON-compatible scalar values."""
    if value is None:
        return None

    missing = pd.isna(value)
    if isinstance(missing, bool) and missing:
        return None

    if hasattr(value, "item"):
        value = value.item()

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


def _timestamp(value: Any) -> pd.Timestamp:
    """Parse a timestamp and normalize it to UTC."""
    parsed = pd.to_datetime(value, utc=True, errors="coerce")

    if pd.isna(parsed):
        raise ValueError(f"Invalid event timestamp: {value!r}")

    return parsed


def _timestamp_string(value: pd.Timestamp) -> str:
    """Return an ISO-8601 UTC timestamp."""
    return value.isoformat().replace("+00:00", "Z")


def _dimensions_key(dimensions: dict[str, Any]) -> str:
    """Create a deterministic key for a set of dimensions."""
    return json.dumps(
        dimensions,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

@observe("build_events")
def build_events(
    results: pd.DataFrame,
    group_by: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Convert anomaly results into normalized events.

    Multiple detectors reporting the same dimension, timestamp and value
    are merged into one event. Their methods, reasons and scores are retained.
    """
    if not isinstance(results, pd.DataFrame):
        raise TypeError("results must be a pandas DataFrame")

    missing = REQUIRED_COLUMNS - set(results.columns)
    if missing:
        raise ValueError(
            f"Required columns missing: {', '.join(sorted(missing))}"
        )

    if group_by is None:
        dimension_columns = [
            column
            for column in results.columns
            if column not in SIGNAL_COLUMNS
        ]
    else:
        if isinstance(group_by, str):
            raise TypeError("group_by must be a sequence of column names")

        dimension_columns = list(group_by)

        missing_dimensions = set(dimension_columns) - set(results.columns)
        if missing_dimensions:
            raise ValueError(
                "Unknown dimension columns: "
                + ", ".join(sorted(missing_dimensions))
            )

        reserved = set(dimension_columns) & SIGNAL_COLUMNS
        if reserved:
            raise ValueError(
                "Signal columns cannot be dimensions: "
                + ", ".join(sorted(reserved))
            )

    buckets: dict[tuple, dict[str, Any]] = {}

    for _, row in results.iterrows():
        if not _is_anomaly(row["is_anomaly"]):
            continue

        occurred_at = _timestamp(row["timestamp"])
        occurred_at_string = _timestamp_string(occurred_at)

        try:
            value = float(row["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid anomaly value: {row['value']!r}"
            ) from exc

        if not math.isfinite(value):
            raise ValueError(f"Non-finite anomaly value: {value!r}")

        dimensions = {
            column: _clean_dimension(row[column])
            for column in dimension_columns
        }

        dimension_key = _dimensions_key(dimensions)

        # A signal from MAD and another from EWMA at the same measurement
        # become one event, not two separate events.
        event_key = (dimension_key, occurred_at_string, value)

        method = str(row.get("method", "unknown"))
        reason = str(row.get("reason", "unknown"))

        raw_score = row.get("score")
        score = None if pd.isna(raw_score) else float(raw_score)

        if event_key not in buckets:
            event_identity = json.dumps(
                {
                    "dimensions": dimensions,
                    "occurred_at": occurred_at_string,
                    "value": value,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )

            buckets[event_key] = {
                "event_id": str(uuid5(EVENT_NAMESPACE, event_identity)),
                "occurred_at": occurred_at_string,
                "dimensions": dimensions,
                "value": value,
                "methods": set(),
                "reasons": set(),
                "scores": {},
                "source_signal_count": 0,
            }

        event = buckets[event_key]
        event["methods"].add(method)
        event["reasons"].add(reason)
        event["scores"][method] = score
        event["source_signal_count"] += 1

    events = []

    for event in buckets.values():
        events.append({
            **event,
            "methods": sorted(event["methods"]),
            "reasons": sorted(event["reasons"]),
        })

    return sorted(
        events,
        key=lambda event: (event["occurred_at"], event["event_id"]),
    )


def correlate_events(
    events: Sequence[dict[str, Any]],
    correlation_window_seconds: float = 90,
) -> list[dict[str, Any]]:
    """
    Group events into incidents by dimensions and temporal proximity.

    Two consecutive events belong to the same incident when their time
    difference is less than or equal to correlation_window_seconds.
    """
    if (
        not math.isfinite(correlation_window_seconds)
        or correlation_window_seconds < 0
    ):
        raise ValueError("correlation_window_seconds must be non-negative")

    grouped_events: dict[str, list[tuple[pd.Timestamp, dict]]] = defaultdict(list)
    dimensions_by_key: dict[str, dict[str, Any]] = {}

    for event in events:
        if "occurred_at" not in event or "dimensions" not in event:
            raise ValueError("Each event needs occurred_at and dimensions")

        dimensions = event["dimensions"]

        if not isinstance(dimensions, dict):
            raise ValueError("Event dimensions must be a dictionary")

        key = _dimensions_key(dimensions)
        dimensions_by_key[key] = dimensions

        grouped_events[key].append(
            (_timestamp(event["occurred_at"]), event)
        )

    incidents = []

    for key, group in grouped_events.items():
        group.sort(key=lambda item: item[0])

        current_events = []
        previous_timestamp = None

        def save_incident() -> None:
            if not current_events:
                return

            first_timestamp = current_events[0][0]
            last_timestamp = current_events[-1][0]

            start_time = _timestamp_string(first_timestamp)
            last_seen = _timestamp_string(last_timestamp)
            dimensions = dimensions_by_key[key]

            identity = json.dumps(
                {
                    "dimensions": dimensions,
                    "start_time": start_time,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )

            incidents.append({
                "incident_id": str(
                    uuid5(INCIDENT_NAMESPACE, identity)
                ),
                "status": "open",
                "dimensions": dimensions,
                "start_time": start_time,
                "last_seen": last_seen,
                "event_count": len(current_events),
                "events": [
                    event for _, event in current_events
                ],
            })

        for event_timestamp, event in group:
            if previous_timestamp is None:
                current_events = [(event_timestamp, event)]
            else:
                gap = (
                    event_timestamp - previous_timestamp
                ).total_seconds()

                if gap <= correlation_window_seconds:
                    current_events.append((event_timestamp, event))
                else:
                    save_incident()
                    current_events = [(event_timestamp, event)]

            previous_timestamp = event_timestamp

        save_incident()

    return sorted(
        incidents,
        key=lambda incident: incident["start_time"],
    )

@observe("create_incidents")
def create_incidents(
    results: pd.DataFrame,
    correlation_window_seconds: float = 90,
    group_by: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Build anomaly events and correlate them into incidents."""
    events = build_events(results, group_by=group_by)

    return correlate_events(
        events,
        correlation_window_seconds=correlation_window_seconds,
    )
