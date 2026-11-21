"""Explainable, topology-aware ranking of probable root-cause services (v2).

This is a deterministic heuristic, not a statistical probability or proof of
causality. It ranks anomalous services using the dependency direction, how
much of each service's blast radius is anomalous, and whether the candidate
itself depends on a service that is already anomalous (in which case it is
more likely a victim than an origin).

Changes compared with v1 (see README.txt):
  * ties are broken with the topology, never with the service name;
  * anomalies that start within ``simultaneity_tolerance_seconds`` of each
    other count as concurrent, because the metric sampling step (for example
    30 s) is usually longer than the real propagation delay;
  * a candidate whose own dependencies are anomalous is marked as
    "likely propagated" and its score is capped;
  * events of several incidents can be separated with ``analyze_incidents``;
  * cyclic or invalid dependency edges are skipped with a warning instead of
    making the whole analyzer fail.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from aiops_platform.intelligence.topology import ServiceTopology


SCORING_VERSION = "rca-heuristic-v2"

# Score components (they add up to at most 100).
BASE_POINTS = 20.0       # the candidate itself is anomalous
COVERAGE_POINTS = 40.0   # share of the candidate's blast radius that is anomalous
TEMPORAL_POINTS = 20.0   # share of those dependents consistent with propagation
ORIGIN_POINTS = 20.0     # none of the candidate's own dependencies is anomalous

# A candidate that depends on an anomalous service is a probable victim.
PROPAGATED_CAP = 60
PROPAGATED_CAP_STEP = 5

# A "high" confidence needs a clear lead over the nearest competitor.
HIGH_SCORE = 80
MEDIUM_SCORE = 60
HIGH_MARGIN = 15


class RootCauseAnalysisError(ValueError):
    """Raised when RCA input or configuration is invalid."""


def _check_number(name: str, value: Any, *, allow_zero: bool = False) -> float:
    kind = "non-negative" if allow_zero else "positive"
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not allow_zero)
    ):
        raise ValueError(f"{name} must be {kind} and finite")
    return float(value)


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


class RootCauseAnalyzer:
    """Rank anomalous services as probable causes using a service topology.

    ``ServiceTopology`` uses the edge ``A -> B`` to mean that service A
    depends on service B. Consequently, when B is anomalous, A is one of the
    potentially affected dependents of B.

    Score (0 to 100, an index, not a probability):
      * 20 points   the candidate has at least one anomaly;
      * 0-40 points share of its affected services (transitive dependents)
                    that are anomalous within the propagation window;
      * 0-20 points share of those anomalous dependents that start later than
                    the candidate, or at the same time within the tolerance;
      * 20 points   none of the candidate's own dependencies is anomalous
                    within the window (the candidate looks like an origin).
    A candidate with an anomalous dependency is "likely propagated" and its
    score is capped at 60 (minus 5 for each additional anomalous dependency).
    """

    def __init__(
        self,
        topology: ServiceTopology | Mapping[str, Any],
        *,
        service_field: str = "service_name",
        propagation_window_seconds: float = 300.0,
        simultaneity_tolerance_seconds: float = 60.0,
        incident_gap_seconds: float | None = None,
    ) -> None:
        self.topology_warnings: list[str] = []
        if isinstance(topology, ServiceTopology):
            resolved_topology = topology
        elif isinstance(topology, Mapping):
            resolved_topology, self.topology_warnings = (
                self._topology_from_mapping(topology)
            )
        else:
            raise TypeError(
                "topology must be a ServiceTopology or a Jaeger topology mapping"
            )
        if not isinstance(service_field, str) or not service_field.strip():
            raise ValueError("service_field must be a non-empty string")

        window = _check_number(
            "propagation_window_seconds", propagation_window_seconds
        )
        tolerance = _check_number(
            "simultaneity_tolerance_seconds",
            simultaneity_tolerance_seconds,
            allow_zero=True,
        )
        if tolerance > window:
            raise ValueError(
                "simultaneity_tolerance_seconds must not exceed "
                "propagation_window_seconds"
            )
        gap = (
            window
            if incident_gap_seconds is None
            else _check_number("incident_gap_seconds", incident_gap_seconds)
        )

        self.topology = resolved_topology
        self.service_field = service_field.strip()
        self.propagation_window_seconds = window
        self.simultaneity_tolerance_seconds = tolerance
        self.incident_gap_seconds = gap

    # ------------------------------------------------------------------
    # Topology loading
    # ------------------------------------------------------------------
    @staticmethod
    def _topology_from_mapping(
        payload: Mapping[str, Any],
    ) -> tuple[ServiceTopology, list[str]]:
        """Build a graph from ``ServiceTopology.to_dict()`` or Jaeger output.

        Edges rejected by the graph (self-dependency, cycle, unknown service)
        are skipped and reported as warnings. Real call graphs can contain
        cycles, for example with asynchronous messaging.
        """
        raw_topology = payload.get("topology", payload)
        if not isinstance(raw_topology, Mapping):
            raise RootCauseAnalysisError("topology payload must be an object")

        services = raw_topology.get("services")
        dependencies = raw_topology.get("dependencies")
        if not isinstance(services, Mapping) or not isinstance(dependencies, Mapping):
            raise RootCauseAnalysisError(
                "topology payload must contain services and dependencies objects"
            )

        graph = ServiceTopology()
        warnings: list[str] = []

        for service_name, metadata in services.items():
            if not isinstance(service_name, str) or not service_name.strip():
                raise RootCauseAnalysisError("topology contains an invalid service name")
            if metadata is not None and not isinstance(metadata, Mapping):
                raise RootCauseAnalysisError(
                    f"metadata for service '{service_name}' must be an object"
                )
            graph.add_service(service_name, dict(metadata or {}))

        for service_name, service_dependencies in dependencies.items():
            if not isinstance(service_name, str) or not service_name.strip():
                raise RootCauseAnalysisError("topology contains an invalid dependency owner")
            if not isinstance(service_dependencies, (list, tuple, set)):
                raise RootCauseAnalysisError(
                    f"dependencies for service '{service_name}' must be a list"
                )
            for dependency in service_dependencies:
                if not isinstance(dependency, str) or not dependency.strip():
                    raise RootCauseAnalysisError(
                        f"service '{service_name}' has an invalid dependency name"
                    )
                try:
                    graph.add_dependency(service_name, dependency)
                except Exception as exc:  # the graph rejects cycles, loops, unknowns
                    warnings.append(
                        f"Dependency '{service_name}' -> '{dependency}' "
                        f"ignored: {exc}"
                    )

        return graph, warnings

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        """Parse an event timestamp and normalize it to UTC."""
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        else:
            raise ValueError("timestamp must be a non-empty ISO-8601 string")

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # Event preparation
    # ------------------------------------------------------------------
    def _prepare(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> tuple[
        dict[str, list[tuple[datetime, Mapping[str, Any]]]],
        list[tuple[datetime, Mapping[str, Any]]],
        list[str],
    ]:
        """Group valid events by service; collect warnings for the others."""
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise TypeError("events must be a sequence of event mappings")

        known_services = set(self.topology.get_services())
        by_service: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = defaultdict(list)
        flat: list[tuple[datetime, Mapping[str, Any]]] = []
        warnings: list[str] = []

        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                warnings.append(f"Event {index} is not an object and was ignored")
                continue

            dimensions = event.get("dimensions")
            if not isinstance(dimensions, Mapping):
                warnings.append(f"Event {index} has no valid dimensions object")
                continue

            raw_service = dimensions.get(self.service_field)
            if not isinstance(raw_service, str) or not raw_service.strip():
                warnings.append(
                    f"Event {index} has no valid '{self.service_field}' dimension"
                )
                continue

            service_name = raw_service.strip()
            if service_name not in known_services:
                warnings.append(
                    f"Event {index} references service '{service_name}' absent from topology"
                )
                continue

            try:
                occurred_at = self._parse_timestamp(event.get("occurred_at"))
            except (TypeError, ValueError, OverflowError) as exc:
                warnings.append(f"Event {index} has an invalid occurred_at: {exc}")
                continue

            by_service[service_name].append((occurred_at, event))
            flat.append((occurred_at, event))

        for records in by_service.values():
            records.sort(key=lambda item: item[0])
        flat.sort(key=lambda item: item[0])

        return by_service, flat, warnings

    def _split_clusters(
        self,
        flat: Sequence[tuple[datetime, Mapping[str, Any]]],
    ) -> list[list[tuple[datetime, Mapping[str, Any]]]]:
        """Split time-sorted events where the gap exceeds the incident gap."""
        clusters: list[list[tuple[datetime, Mapping[str, Any]]]] = []
        previous: datetime | None = None
        for item in flat:
            if (
                previous is None
                or (item[0] - previous).total_seconds() > self.incident_gap_seconds
            ):
                clusters.append([])
            clusters[-1].append(item)
            previous = item[0]
        return clusters

    @staticmethod
    def _confidence_level(score: int, *, propagated: bool, margin: int | None) -> str:
        if propagated:
            return "low"
        if score >= HIGH_SCORE and (margin is None or margin >= HIGH_MARGIN):
            return "high"
        if score >= MEDIUM_SCORE:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def analyze(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Rank probable root causes for events that belong to ONE incident.

        Events are normalized anomaly events such as ``build_events`` output.
        Each must contain ``occurred_at`` and a ``dimensions`` mapping with the
        configured service label (``service_name`` by default). Invalid or
        unmappable events are skipped and reported in ``warnings``. If the
        events are separated by gaps longer than ``incident_gap_seconds``, a
        warning recommends ``analyze_incidents``.
        """
        by_service, flat, warnings = self._prepare(events)
        warnings = list(self.topology_warnings) + warnings

        if len(self._split_clusters(flat)) > 1:
            warnings.append(
                "Events span several time clusters (gap larger than "
                f"{self.incident_gap_seconds:g} s); use analyze_incidents() "
                "so that unrelated anomalies are not mixed"
            )

        window = self.propagation_window_seconds
        tolerance = self.simultaneity_tolerance_seconds

        onsets = {service: records[0][0] for service, records in by_service.items()}
        affected_map = {
            service: sorted(set(self.topology.get_affected_services(service)) - {service})
            for service in by_service
        }

        candidates: list[dict[str, Any]] = []
        sort_keys: dict[str, tuple[int, int, int, datetime]] = {}

        for service, records in by_service.items():
            first_at = onsets[service]
            affected = affected_map[service]

            related: list[tuple[str, float]] = []
            for dependent in affected:
                if dependent not in onsets:
                    continue
                delay = (onsets[dependent] - first_at).total_seconds()
                if abs(delay) <= window:
                    related.append((dependent, delay))

            consistent = [(n, d) for n, d in related if -tolerance <= d <= window]
            earlier = [(n, d) for n, d in related if d < -tolerance]

            upstream: list[tuple[str, float]] = []
            for other, other_affected in affected_map.items():
                if other != service and service in other_affected:
                    delay = (onsets[other] - first_at).total_seconds()
                    if abs(delay) <= window:
                        upstream.append((other, delay))
            upstream.sort(key=lambda item: (item[1], item[0]))

            coverage = len(related) / len(affected) if affected else 0.0
            temporal = len(consistent) / len(related) if related else 0.0
            propagated = bool(upstream)

            coverage_points = COVERAGE_POINTS * coverage
            temporal_points = TEMPORAL_POINTS * temporal
            origin_points = 0.0 if propagated else ORIGIN_POINTS

            raw_score = BASE_POINTS + coverage_points + temporal_points + origin_points
            cap: int | None = None
            if propagated:
                cap = max(0, PROPAGATED_CAP - PROPAGATED_CAP_STEP * (len(upstream) - 1))
                raw_score = min(raw_score, cap)
            score = max(0, min(100, _round_half_up(raw_score)))

            supporting = sorted(consistent, key=lambda item: (item[1], item[0]))
            symptoms = sorted(earlier, key=lambda item: (item[1], item[0]))

            methods = sorted({
                str(method)
                for _, event in records
                for method in (event.get("methods") or [])
            })
            reasons = sorted({
                str(reason)
                for _, event in records
                for reason in (event.get("reasons") or [])
            })

            candidates.append({
                "service_name": service,
                "score": score,
                "first_anomaly_at": self._format_timestamp(first_at),
                "anomaly_event_count": len(records),
                "likely_propagated": propagated,
                "affected_services": affected,
                "supporting_affected_services": [
                    {
                        "service_name": name,
                        "first_anomaly_at": self._format_timestamp(onsets[name]),
                        "delay_seconds": round(delay, 3),
                        "relation": "concurrent" if abs(delay) <= tolerance else "later",
                    }
                    for name, delay in supporting
                ],
                "earlier_affected_services": [
                    {
                        "service_name": name,
                        "first_anomaly_at": self._format_timestamp(onsets[name]),
                        "delay_seconds": round(delay, 3),
                    }
                    for name, delay in symptoms
                ],
                "anomalous_upstream_services": [
                    {
                        "service_name": name,
                        "first_anomaly_at": self._format_timestamp(onsets[name]),
                        # Negative: the dependency became anomalous earlier.
                        "delay_seconds": round(delay, 3),
                    }
                    for name, delay in upstream
                ],
                "evidence": {
                    "detector_methods": methods,
                    "reasons": reasons,
                    "affected_service_count": len(affected),
                    "anomalous_affected_service_count": len(related),
                    "coverage_ratio": round(coverage, 4),
                    "temporal_consistency_ratio": round(temporal, 4),
                    "score_components": {
                        "base": BASE_POINTS,
                        "coverage": round(coverage_points, 2),
                        "temporal": round(temporal_points, 2),
                        "origin_bonus": origin_points,
                        "propagated_cap": cap,
                    },
                    "score_is_probability": False,
                },
            })
            # Tie-breakers come from the topology and the evidence, never from
            # the service name: fewer anomalous upstream services first, then
            # more anomalous dependents, then the larger blast radius, then the
            # earliest onset. The name is only a final deterministic fallback.
            sort_keys[service] = (
                len(upstream),
                -len(related),
                -len(affected),
                first_at,
            )

        candidates.sort(
            key=lambda item: (
                -item["score"],
                sort_keys[item["service_name"]],
                item["service_name"],
            )
        )

        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
            competitors = [c["score"] for c in candidates if c is not candidate]
            margin = (
                candidate["score"] - max(competitors) if competitors else None
            )
            candidate["margin_to_nearest_competitor"] = margin
            candidate["confidence_level"] = self._confidence_level(
                candidate["score"],
                propagated=candidate["likely_propagated"],
                margin=margin,
            )

        return {
            "status": "ranked" if candidates else "insufficient_data",
            "source": "anomaly_events_and_service_topology",
            "scoring_version": SCORING_VERSION,
            "service_field": self.service_field,
            "propagation_window_seconds": window,
            "simultaneity_tolerance_seconds": tolerance,
            "summary": {
                "events_received": len(events),
                "events_used": len(flat),
                "services_with_anomalies": len(by_service),
                "candidates_count": len(candidates),
            },
            "ranked_candidates": candidates,
            "warnings": warnings,
            "scoring_note": (
                "Score heuristique explicable sur 100, pas une probabilité ni "
                "une preuve de causalité. Il combine l'anomalie du candidat, "
                "la part de ses dépendants anormaux, leur ordre temporel et "
                "l'absence d'anomalie dans ses propres dépendances."
            ),
        }

    def analyze_incidents(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Split events into incidents by time gap and analyze each one."""
        _, flat, warnings = self._prepare(events)
        incidents = []
        for cluster in self._split_clusters(flat):
            report = self.analyze([event for _, event in cluster])
            incidents.append({
                "start_time": self._format_timestamp(cluster[0][0]),
                "end_time": self._format_timestamp(cluster[-1][0]),
                "event_count": len(cluster),
                "report": report,
            })

        return {
            "status": "ranked" if incidents else "insufficient_data",
            "scoring_version": SCORING_VERSION,
            "incident_gap_seconds": self.incident_gap_seconds,
            "incident_count": len(incidents),
            "incidents": incidents,
            "warnings": list(self.topology_warnings) + warnings,
        }
