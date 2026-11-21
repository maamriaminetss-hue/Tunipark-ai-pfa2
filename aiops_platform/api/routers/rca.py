"""API routes for explainable root-cause analysis."""

from datetime import timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from aiops_platform.api.dependencies import get_db
from aiops_platform.database.models import (
    Event,
    Incident,
    IncidentEvent,
)
from aiops_platform.intelligence.rca.root_cause_analyzer import (
    RootCauseAnalyzer,
)
from aiops_platform.intelligence.topology.jaeger_reader import (
    JaegerClientError,
    JaegerDependencyReader,
)


router = APIRouter(
    prefix="/rca",
    tags=["RCA"],
)

reader = JaegerDependencyReader()


class RCAAnalyzeRequest(BaseModel):
    """Parameters for analyzing one existing incident."""

    incident_id: UUID

    lookback_ms: int = Field(
        default=3_600_000,
        gt=0,
        le=604_800_000,
    )

    propagation_window_seconds: float = Field(
        default=300.0,
        gt=0,
        le=86_400,
        allow_inf_nan=False,
    )

    simultaneity_tolerance_seconds: float = Field(
        default=60.0,
        ge=0,
        le=86_400,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def validate_time_windows(self):
        if (
            self.simultaneity_tolerance_seconds
            > self.propagation_window_seconds
        ):
            raise ValueError(
                "simultaneity_tolerance_seconds must not exceed "
                "propagation_window_seconds"
            )

        return self


def _to_utc(value):
    """Normalize a database datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _empty_analysis(warnings: list[str]) -> dict[str, Any]:
    """Return a consistent response when no usable events exist."""
    return {
        "status": "insufficient_data",
        "source": "anomaly_events_and_service_topology",
        "summary": {
            "events_received": 0,
            "events_used": 0,
            "services_with_anomalies": 0,
            "candidates_count": 0,
        },
        "ranked_candidates": [],
        "warnings": warnings,
        "scoring_note": (
            "Aucune cause racine ne peut être classée sans données "
            "d'anomalie exploitables."
        ),
    }


@router.post(
    "/analyze",
    summary="Analyze the probable root cause of an incident",
    description=(
        "Load associated anomaly events, retrieve the historical "
        "Jaeger topology and rank explainable root-cause candidates."
    ),
)
def analyze_incident(
    payload: RCAAnalyzeRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Analyze one incident using PostgreSQL events and Jaeger topology."""

    # --------------------------------------------------
    # 1. Retrieve the incident
    # --------------------------------------------------

    incident = db.scalar(
        select(Incident)
        .options(joinedload(Incident.service))
        .where(Incident.id == payload.incident_id)
    )

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found.",
        )

    # --------------------------------------------------
    # 2. Retrieve only events linked to this incident
    # --------------------------------------------------

    statement = (
        select(Event)
        .options(joinedload(Event.service))
        .join(
            IncidentEvent,
            IncidentEvent.event_id == Event.id,
        )
        .where(
            IncidentEvent.incident_id == incident.id,
        )
        .order_by(Event.occurred_at.asc())
    )

    events = db.scalars(statement).all()

    incident_service = (
        incident.service.name
        if incident.service is not None
        else None
    )

    if not events:
        warnings = [
            "No events are associated with this incident.",
            "Root-cause analysis cannot proceed without anomaly events.",
        ]

        return {
            "incident_id": str(incident.id),
            "incident_title": incident.title,
            "incident_status": incident.status,
            "incident_service": incident_service,
            "status": "insufficient_data",
            "events": [],
            "topology": None,
            "analysis": _empty_analysis(warnings),
            "warnings": warnings,
        }

    # --------------------------------------------------
    # 3. Normalize persisted events
    # --------------------------------------------------

    normalized_events: list[dict[str, Any]] = []
    event_details: list[dict[str, Any]] = []
    event_dates = []

    for event in events:
        occurred_at = event.occurred_at

        if occurred_at is not None:
            occurred_at = _to_utc(occurred_at)
            event_dates.append(occurred_at)

        service_name = (
            event.service.name
            if event.service is not None
            else None
        )

        normalized_events.append({
            "event_id": str(event.id),
            "occurred_at": (
                occurred_at.isoformat()
                if occurred_at is not None
                else None
            ),
            "dimensions": {
                "service_name": service_name,
                "metric_name": event.metric_name,
                "event_type": event.event_type,
                "severity": event.severity,
            },
            "message": event.message,
            "metric_value": event.metric_value,
            "source": event.source,
            "labels": event.labels or {},
        })

        event_details.append({
            "event_id": str(event.id),
            "service_name": service_name,
            "event_type": event.event_type,
            "severity": event.severity,
            "metric_name": event.metric_name,
            "metric_value": event.metric_value,
            "occurred_at": (
                occurred_at.isoformat()
                if occurred_at is not None
                else None
            ),
        })

    # --------------------------------------------------
    # 4. Require valid timestamps for historical topology
    # --------------------------------------------------

    if not event_dates:
        warnings = [
            "Associated events have no valid occurrence timestamp.",
            "Historical Jaeger topology cannot be selected.",
        ]

        return {
            "incident_id": str(incident.id),
            "incident_title": incident.title,
            "incident_status": incident.status,
            "incident_service": incident_service,
            "status": "insufficient_data",
            "events": event_details,
            "topology": None,
            "analysis": _empty_analysis(warnings),
            "warnings": warnings,
        }

    earliest_event = min(event_dates)
    latest_event = max(event_dates)

    # Extend the end of the Jaeger window to include propagation
    # after the last observed anomaly.
    topology_end = latest_event + timedelta(
        seconds=payload.propagation_window_seconds
    )

    end_ts = int(topology_end.timestamp() * 1000)

    # --------------------------------------------------
    # 5. Retrieve topology from Jaeger for this time window
    # --------------------------------------------------

    try:
        topology_payload = reader.fetch_topology(
            lookback_ms=payload.lookback_ms,
            end_ts=end_ts,
        )

    except JaegerClientError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Jaeger service unavailable: {exc}",
        ) from exc

    topology_data = topology_payload.get("topology", {})

    topology_services = topology_data.get("services", {})
    topology_edges = topology_payload.get("edges", [])

    # --------------------------------------------------
    # 6. Run explainable RCA
    # --------------------------------------------------

    try:
        analyzer = RootCauseAnalyzer(
            topology_payload,
            propagation_window_seconds=(
                payload.propagation_window_seconds
            ),
            simultaneity_tolerance_seconds=(
                payload.simultaneity_tolerance_seconds
            ),
        )

        analysis = analyzer.analyze(normalized_events)

    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"RCA analysis input error: {exc}",
        ) from exc

    # --------------------------------------------------
    # 7. Add explicit topology diagnostics
    # --------------------------------------------------

    warnings = list(topology_payload.get("warnings", []))
    warnings.extend(analysis.get("warnings", []))

    if not topology_edges:
        warnings.append(
            "No service dependencies were observed in the selected "
            "historical Jaeger window."
        )

    for service_name in sorted({
        event.get("dimensions", {}).get("service_name")
        for event in normalized_events
        if event.get("dimensions", {}).get("service_name")
    }):
        if service_name not in topology_services:
            warnings.append(
                f"Service '{service_name}' is absent from the "
                "historical Jaeger topology."
            )

    # Remove duplicate warning messages without changing their order.
    warnings = list(dict.fromkeys(warnings))

    analysis["warnings"] = warnings

    # --------------------------------------------------
    # 8. Return the complete analysis
    # --------------------------------------------------

    return {
        "incident_id": str(incident.id),
        "incident_title": incident.title,
        "incident_status": incident.status,
        "incident_service": incident_service,
        "status": analysis["status"],
        "events": event_details,
        "topology": {
            "source": topology_payload.get("source"),
            "services_count": len(topology_services),
            "edges_count": len(topology_edges),
            "services": sorted(topology_services.keys()),
            "edges": topology_edges,
            "window": {
                "start_ts": end_ts - payload.lookback_ms,
                "end_ts": end_ts,
                "lookback_ms": payload.lookback_ms,
                "earliest_event_at": earliest_event.isoformat(),
                "latest_event_at": latest_event.isoformat(),
            },
        },
        "analysis": analysis,
        "warnings": warnings,
    }
