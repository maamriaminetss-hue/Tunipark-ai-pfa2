"""Baseline root-cause rankings for hypothesis H3.

Each function returns a list of service names, best candidate first, so that
every method can be scored with the same Top-1 / Top-3 evaluation.

Methods compared in EVALUATION.md (H3):
  * most anomalous service     -> ``rank_most_anomalous``
  * last changed service       -> ``rank_last_changed``
  * RCA without topology       -> ``rank_without_topology``
  * RCA with topology          -> ``rank_with_topology``

None of these functions receives the true root cause or the scenario label.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .root_cause_analyzer import RootCauseAnalyzer


def _summaries(
    events: Sequence[Mapping[str, Any]],
    service_field: str,
) -> dict[str, dict[str, Any]]:
    """Per service: first onset, number of events and strongest detector score."""
    summaries: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        dimensions = event.get("dimensions")
        if not isinstance(dimensions, Mapping):
            continue
        name = dimensions.get(service_field)
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            occurred_at = RootCauseAnalyzer._parse_timestamp(event.get("occurred_at"))
        except (TypeError, ValueError, OverflowError):
            continue

        summary = summaries.setdefault(
            name.strip(),
            {"onset": occurred_at, "count": 0, "max_score": 0.0},
        )
        summary["onset"] = min(summary["onset"], occurred_at)
        summary["count"] += 1

        scores = event.get("scores")
        if isinstance(scores, Mapping):
            for value in scores.values():
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ):
                    summary["max_score"] = max(summary["max_score"], float(value))
    return summaries


def rank_most_anomalous(
    events: Sequence[Mapping[str, Any]],
    *,
    service_field: str = "service_name",
) -> list[str]:
    """Rank by the strongest detector score, then by number of anomaly events."""
    summaries = _summaries(events, service_field)
    return sorted(
        summaries,
        key=lambda name: (
            -summaries[name]["max_score"],
            -summaries[name]["count"],
            summaries[name]["onset"],
            name,
        ),
    )


def rank_without_topology(
    events: Sequence[Mapping[str, Any]],
    *,
    service_field: str = "service_name",
) -> list[str]:
    """Rank by earliest anomaly onset only (temporal order, no dependency graph)."""
    summaries = _summaries(events, service_field)
    return sorted(
        summaries,
        key=lambda name: (
            summaries[name]["onset"],
            -summaries[name]["max_score"],
            -summaries[name]["count"],
            name,
        ),
    )


def rank_last_changed(
    events: Sequence[Mapping[str, Any]],
    changes: Sequence[Mapping[str, Any]],
    *,
    service_field: str = "service_name",
) -> list[str]:
    """Rank services by their most recent change before the first anomaly.

    ``changes`` is a list of ``{"service_name": ..., "changed_at": ...}``
    records, for example deployments or configuration changes. Services never
    changed before the incident are not ranked.
    """
    summaries = _summaries(events, service_field)
    if not summaries:
        return []
    reference = min(summary["onset"] for summary in summaries.values())

    latest: dict[str, datetime] = {}
    for change in changes or []:
        if not isinstance(change, Mapping):
            continue
        name = change.get("service_name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            changed_at = RootCauseAnalyzer._parse_timestamp(change.get("changed_at"))
        except (TypeError, ValueError, OverflowError):
            continue
        if changed_at > reference:
            continue
        name = name.strip()
        if name not in latest or changed_at > latest[name]:
            latest[name] = changed_at

    return sorted(latest, key=lambda name: (-latest[name].timestamp(), name))


def rank_with_topology(
    analyzer: RootCauseAnalyzer,
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Rank with the topology-aware ``RootCauseAnalyzer``."""
    report = analyzer.analyze(events)
    return [candidate["service_name"] for candidate in report["ranked_candidates"]]
