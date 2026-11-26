
"""Explainable Root Cause Analysis engine."""

from datetime import datetime
from typing import Any


class RootCauseAnalyzer:
    """Rank potential root causes using incident events and service topology.

    Expected incident format:
    {
        "incident_id": "INC-001",
        "events": [
            {
                "event_id": "EVT-001",
                "service_name": "database",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 0.9,
                "reason": "High response time"
            }
        ]
    }

    Severity must be a numeric value between 0 and 1.
    """

    def __init__(self, topology: Any) -> None:
        self.topology = topology

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        """Parse an ISO 8601 timestamp."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Event timestamp must be a non-empty string")

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid event timestamp: {value}") from exc

    @staticmethod
    def _validate_severity(value: Any) -> float:
        """Validate and normalize severity."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Event severity must be a number between 0 and 1")

        if not 0 <= value <= 1:
            raise ValueError("Event severity must be between 0 and 1")

        return float(value)

    def _normalize_events(
        self,
        incident: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Validate events and convert them into a consistent format."""
        if not isinstance(incident, dict):
            raise ValueError("Incident must be a dictionary")

        events = incident.get("events")

        if not isinstance(events, list) or not events:
            raise ValueError("Incident must contain a non-empty events list")

        normalized = []

        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Each event must be a dictionary")

            service_name = event.get("service_name")

            if not isinstance(service_name, str) or not service_name.strip():
                raise ValueError("Each event must have a service_name")

            service_name = service_name.strip()

            if service_name not in self.topology.get_services():
                raise ValueError(f"Unknown service in incident: {service_name}")

            occurred_at = self._parse_timestamp(event.get("occurred_at"))
            severity = self._validate_severity(event.get("severity", 0.5))

            normalized.append({
                "event_id": event.get("event_id"),
                "service_name": service_name,
                "occurred_at": occurred_at,
                "severity": severity,
                "reason": event.get("reason", "No reason provided"),
            })

        # Mixing timezone-aware and naive timestamps is ambiguous.
        timezone_modes = {
            event["occurred_at"].utcoffset() is not None
            for event in normalized
        }

        if len(timezone_modes) > 1:
            raise ValueError(
                "All event timestamps must use the same timezone format"
            )

        return sorted(normalized, key=lambda event: event["occurred_at"])

    def analyze(self, incident: dict[str, Any]) -> dict[str, Any]:
        """Analyze an incident and return ranked root cause candidates."""
        events = self._normalize_events(incident)

        incident_id = incident.get("incident_id", "UNKNOWN")

        # Group events by service.
        service_events: dict[str, list[dict[str, Any]]] = {}

        for event in events:
            service_events.setdefault(event["service_name"], []).append(event)

        observed_services = list(service_events)

        first_seen = {
            service: min(
                event["occurred_at"]
                for event in service_events[service]
            )
            for service in observed_services
        }

        max_severity = {
            service: max(
                event["severity"]
                for event in service_events[service]
            )
            for service in observed_services
        }

        earliest_event = min(first_seen.values())
        latest_event = max(first_seen.values())
        time_span = (latest_event - earliest_event).total_seconds()

        candidate_reports = []

        for service in observed_services:
            # Find observed services that depend, directly or indirectly,
            # on the current candidate.
            affected_observed = [
                other
                for other in observed_services
                if other != service
                and service in self._get_dependencies_recursively(other)
            ]

            # Earlier anomalies receive a higher temporal score.
            if time_span == 0:
                temporal_score = 0.5
            else:
                elapsed = (
                    first_seen[service] - earliest_event
                ).total_seconds()

                temporal_score = 1.0 - (elapsed / time_span)

            # Topology score measures how many observed services depend
            # on this candidate.
            topology_score = (
                len(affected_observed) / (len(observed_services) - 1)
                if len(observed_services) > 1
                else 0.0
            )

            severity_score = max_severity[service]

            # Transparent heuristic score, not a calibrated probability.
            rank_score = (
                0.45 * temporal_score
                + 0.35 * topology_score
                + 0.20 * severity_score
            )

            evidence = [
                f"First anomaly observed at {first_seen[service].isoformat()}",
                f"Maximum observed severity: {severity_score:.2f}",
                (
                    f"{len(affected_observed)} observed dependent service(s)"
                    if affected_observed
                    else "No observed downstream dependency evidence"
                ),
            ]

            reasons = sorted({
                str(event["reason"])
                for event in service_events[service]
            })

            candidate_reports.append({
                "service_name": service,
                "rank_score": round(rank_score, 4),
                "first_seen": first_seen[service].isoformat(),
                "severity": severity_score,
                "affected_services": sorted(affected_observed),
                "evidence": evidence,
                "event_reasons": reasons,
                "temporal_score": round(temporal_score, 4),
            })

        candidate_reports.sort(
            key=lambda candidate: (
                -candidate["rank_score"],
                candidate["first_seen"],
                candidate["service_name"],
            )
        )

        for rank, candidate in enumerate(candidate_reports, start=1):
            candidate["rank"] = rank

        return {
            "incident_id": incident_id,
            "analysis_status": "completed",
            "method": "temporal_topology_severity_heuristic",
            "score_description": (
                "Heuristic ranking score between 0 and 1; "
                "not a calibrated probability."
            ),
            "events_analyzed": len(events),
            "candidates": candidate_reports,
            "top_candidate": (
                candidate_reports[0]["service_name"]
                if candidate_reports
                else None
            ),
        }

    def _get_dependencies_recursively(self, service: str) -> set[str]:
        """Return all direct and transitive dependencies of a service."""
        dependencies = set()
        pending = list(self.topology.get_dependencies(service))

        while pending:
            current = pending.pop()

            if current in dependencies:
                continue

            dependencies.add(current)
            pending.extend(self.topology.get_dependencies(current))

        return dependencies
