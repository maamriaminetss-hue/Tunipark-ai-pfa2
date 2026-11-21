"""Coordinate the runbook catalog with remediation plan management.

RunbookManager is the single source of truth for corrective procedures.
RemediationEngine receives a derived, read-only action projection so it can
rank and track plans without maintaining a second independently editable
catalog. This module does not execute commands or modify external systems.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from aiops_platform.intelligence.remediation import RemediationEngine
from aiops_platform.intelligence.runbooks import RunbookManager


class RunbookRemediationWorkflow:
    """Expose one coordinated flow from runbook catalog to remediation plan."""

    def __init__(self) -> None:
        self._runbooks = RunbookManager()
        self._remediation = RemediationEngine()

    @staticmethod
    def _runbook_to_action(runbook: dict[str, Any]) -> dict[str, Any]:
        """Build the remediation engine's plan projection from a runbook.

        The runbook remains canonical. The derived action is only an index
        used for matching and plan status tracking.
        """
        steps = runbook["steps"]
        return {
            "action_id": runbook["runbook_id"],
            "title": runbook["title"],
            "description": runbook["description"],
            "incident_types": deepcopy(runbook["incident_types"]),
            "services": deepcopy(runbook["services"]),
            "risk_level": runbook["risk_level"],
            "requires_approval": (
                runbook["risk_level"] in {"high", "critical"}
                or any(step["requires_approval"] for step in steps)
            ),
        }

    def register_runbook(self, runbook: dict[str, Any]) -> None:
        """Register a canonical runbook and project it into plan matching."""
        self._runbooks.register_runbook(runbook)
        canonical = self._runbooks.get_runbook(runbook["runbook_id"])
        self._remediation.register_action(self._runbook_to_action(canonical))

    def get_runbook(self, runbook_id: str) -> dict[str, Any]:
        """Return a defensive copy of a canonical runbook."""
        return self._runbooks.get_runbook(runbook_id)

    def list_runbooks(
        self,
        incident_type: str | None = None,
        service_name: str | None = None,
        risk_level: str | None = None,
    ) -> list[dict[str, Any]]:
        """List canonical runbooks with optional filters."""
        return self._runbooks.list_runbooks(
            incident_type=incident_type,
            service_name=service_name,
            risk_level=risk_level,
        )

    def recommend_runbooks(
        self,
        incident: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Recommend canonical runbooks for an incident."""
        return self._runbooks.recommend_runbooks(incident)

    def _with_runbook_snapshots(
        self,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach canonical runbook snapshots to a defensive plan copy."""
        result = deepcopy(plan)
        for action in result["actions"]:
            runbook_id = action["action_id"]
            action["runbook_id"] = runbook_id
            action["runbook"] = self._runbooks.get_runbook(runbook_id)
        return result

    def create_plan(
        self,
        incident: dict[str, Any],
        runbook_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a plan from recommended runbooks without executing them.

        Explicit selections must be among the runbooks recommended for this
        incident. This prevents callers from bypassing matching policy by
        supplying an arbitrary catalog identifier.
        """
        recommendations = self._runbooks.recommend_runbooks(incident)
        recommended_ids = {
            item["runbook"]["runbook_id"] for item in recommendations
        }

        if runbook_ids is None:
            selected_ids = [
                item["runbook"]["runbook_id"] for item in recommendations
            ]
        else:
            if not isinstance(runbook_ids, list) or not runbook_ids:
                raise ValueError("runbook_ids must be a non-empty list")

            selected_ids = []
            for runbook_id in runbook_ids:
                if not isinstance(runbook_id, str) or not runbook_id.strip():
                    raise ValueError("runbook_id must be a non-empty string")
                runbook_id = runbook_id.strip()
                if runbook_id in selected_ids:
                    raise ValueError(f"Duplicate runbook_id: {runbook_id}")
                if runbook_id not in recommended_ids:
                    raise ValueError(
                        f"Runbook is not recommended for this incident: {runbook_id}"
                    )
                selected_ids.append(runbook_id)

        if not selected_ids:
            raise ValueError("No runbooks match this incident")

        plan = self._remediation.create_plan(
            incident,
            action_ids=selected_ids,
        )
        return self._with_runbook_snapshots(plan)

    def approve_plan(
        self,
        plan_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        """Record approval for a plan that requires human validation."""
        plan = self._remediation.approve_plan(plan_id, approved_by)
        return self._with_runbook_snapshots(plan)

    def update_action(
        self,
        plan_id: str,
        runbook_id: str,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Track a runbook-level outcome; this does not run its steps."""
        plan = self._remediation.update_action(
            plan_id,
            runbook_id,
            status,
            note=note,
        )
        return self._with_runbook_snapshots(plan)

    def verify_resolution(
        self,
        plan_id: str,
        db: Any,
        checks: Sequence[Mapping[str, Any]],
        actor: str,
        *,
        prometheus_reader: Any = None,
        verifier: Any = None,
        queries: Mapping[str, str] | None = None,
        step: str = "30s",
        verified_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Verify a completed plan against Prometheus and audit the outcome.

        This method does not execute remediation steps. It only verifies
        post-remediation observations, updates the incident when recovery is
        confirmed, and persists the report and status transition atomically.
        """
        from aiops_platform.database.audit_crud import add_audit_log
        from aiops_platform.database.incident_crud import update_incident
        from aiops_platform.database.models import Incident, Service
        from aiops_platform.intelligence.verification.prometheus_resolution_reader import (
            DEFAULT_RESOLUTION_QUERIES,
            PrometheusReaderError,
            PrometheusResolutionReader,
        )
        from aiops_platform.intelligence.verification.resolution_verifier import (
            ResolutionVerifier,
        )

        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")

        plan = self.get_plan(plan_id)
        if plan["status"] != "completed":
            raise ValueError("Remediation plan must be completed before verification")

        completed_at = plan.get("remediation_completed_at")
        if not isinstance(completed_at, str) or not completed_at.strip():
            raise ValueError("Completed plan has no remediation_completed_at timestamp")

        if not any(
            action.get("status") == "completed"
            for action in plan["actions"]
        ):
            raise ValueError(
                "At least one remediation action must be reported as completed"
            )

        if (
            isinstance(checks, (str, bytes))
            or not isinstance(checks, Sequence)
            or not checks
        ):
            raise ValueError("checks must be a non-empty sequence")

        metric_names = []
        for index, check in enumerate(checks):
            if not isinstance(check, Mapping):
                raise ValueError(f"Check {index} must be a mapping")
            metric_name = check.get("metric_name")
            if not isinstance(metric_name, str) or not metric_name.strip():
                raise ValueError(
                    f"Check {index} must contain a non-empty metric_name"
                )
            metric_name = metric_name.strip()
            if metric_name in metric_names:
                raise ValueError(f"Duplicate metric check: {metric_name}")
            metric_names.append(metric_name)

        if queries is None:
            query_catalog = dict(DEFAULT_RESOLUTION_QUERIES)
        elif isinstance(queries, Mapping):
            query_catalog = dict(queries)
        else:
            raise ValueError("queries must be a mapping of metric names to PromQL")

        missing_queries = [
            metric_name
            for metric_name in metric_names
            if metric_name not in query_catalog
        ]
        if missing_queries:
            raise ValueError(
                "Missing Prometheus queries for metrics: "
                + ", ".join(missing_queries)
            )

        selected_queries = {
            metric_name: query_catalog[metric_name]
            for metric_name in metric_names
        }

        reader = (
            prometheus_reader
            if prometheus_reader is not None
            else PrometheusResolutionReader()
        )
        resolution_verifier = (
            verifier if verifier is not None else ResolutionVerifier()
        )
        checked_at = (
            verified_at
            if verified_at is not None
            else datetime.now(timezone.utc)
        )

        try:
            incident_uuid = UUID(str(plan["incident_id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(
                "Plan incident_id must be a valid database UUID"
            ) from exc

        incident = db.get(Incident, incident_uuid)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_uuid}")

        service = (
            db.get(Service, incident.service_id)
            if incident.service_id is not None
            else None
        )

        def timestamp_for_report(value: datetime | str) -> str:
            if isinstance(value, datetime):
                parsed = value
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
            return value

        if service is None:
            report = {
                "service_name": None,
                "status": "inconclusive",
                "remediation_completed_at": completed_at,
                "verified_at": timestamp_for_report(checked_at),
                "required_consecutive_samples": (
                    resolution_verifier.required_consecutive_samples
                ),
                "metrics": [],
                "warnings": [
                    "incident_has_no_associated_service_for_prometheus_verification"
                ],
                "read_only": True,
            }
        else:
            try:
                observations = reader.fetch_observations(
                    service_name=service.name,
                    start=completed_at,
                    end=checked_at,
                    step=step,
                    queries=selected_queries,
                )
            except PrometheusReaderError as exc:
                report = {
                    "service_name": service.name,
                    "status": "inconclusive",
                    "remediation_completed_at": completed_at,
                    "verified_at": timestamp_for_report(checked_at),
                    "required_consecutive_samples": (
                        resolution_verifier.required_consecutive_samples
                    ),
                    "metrics": [],
                    "warnings": [
                        f"prometheus_query_failed: {exc}"
                    ],
                    "read_only": True,
                }
            else:
                report = resolution_verifier.verify(
                    service_name=service.name,
                    observations=observations,
                    checks=checks,
                    remediation_completed_at=completed_at,
                    verified_at=checked_at,
                )

        status_before = incident.status

        try:
            if (
                report["status"] == "resolved"
                and incident.status not in {"resolved", "closed"}
            ):
                updated_incident = update_incident(
                    db=db,
                    incident_id=incident_uuid,
                    status="resolved",
                    commit=False,
                )
                if updated_incident is None:
                    raise ValueError(
                        f"Incident not found during resolution update: {incident_uuid}"
                    )
                incident = updated_incident

            add_audit_log(
                db=db,
                actor=actor.strip(),
                action="incident.resolution_verification",
                resource_type="incident",
                resource_id=incident_uuid,
                details={
                    "plan_id": plan_id,
                    "verification_report": deepcopy(report),
                    "incident_status_before": status_before,
                    "incident_status_after": incident.status,
                    "verification_window": {
                        "start": completed_at,
                        "end": report["verified_at"],
                        "step": step,
                        "metrics": metric_names,
                    },
                },
            )

            db.commit()
            db.refresh(incident)
        except Exception:
            db.rollback()
            raise

        result = deepcopy(report)
        result["plan_id"] = plan_id
        result["incident_id"] = str(incident_uuid)
        result["incident_status"] = incident.status
        result["audit_action"] = "incident.resolution_verification"
        return result

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        """Return a plan enriched with its canonical runbook definitions."""
        return self._with_runbook_snapshots(
            self._remediation.get_plan(plan_id)
        )
