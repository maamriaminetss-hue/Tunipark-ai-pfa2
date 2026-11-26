
"""Runbook catalog, recommendation and execution tracking."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class RunbookManager:
    """Manage operational procedures without executing real-world actions."""

    VALID_RISKS = {"low", "medium", "high", "critical"}
    VALID_STEP_STATUSES = {"in_progress", "completed", "failed", "skipped"}

    def __init__(self) -> None:
        self._runbooks: dict[str, dict[str, Any]] = {}
        self._executions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    def register_runbook(self, runbook: dict[str, Any]) -> None:
        """Validate and register a runbook."""
        if not isinstance(runbook, dict):
            raise ValueError("Runbook must be a dictionary")

        runbook_id = self._require_text(
            runbook.get("runbook_id"), "runbook_id"
        )
        title = self._require_text(runbook.get("title"), "title")
        description = self._require_text(
            runbook.get("description"), "description"
        )

        if runbook_id in self._runbooks:
            raise ValueError(f"Runbook already exists: {runbook_id}")

        risk_level = runbook.get("risk_level")

        if risk_level not in self.VALID_RISKS:
            raise ValueError(
                f"Invalid risk_level. Expected one of: "
                f"{sorted(self.VALID_RISKS)}"
            )

        incident_types = runbook.get("incident_types")
        if not isinstance(incident_types, list) or not incident_types:
            raise ValueError("incident_types must be a non-empty list")

        incident_types = [
            self._require_text(item, "incident_type").casefold()
            for item in incident_types
        ]

        services = runbook.get("services", [])
        if not isinstance(services, list):
            raise ValueError("services must be a list")

        services = [
            self._require_text(item, "service").casefold()
            for item in services
        ]

        steps = runbook.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("A runbook must contain at least one step")

        normalized_steps = []
        seen_step_ids = set()

        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("Each step must be a dictionary")

            step_id = self._require_text(
                step.get("step_id"), "step_id"
            )
            step_title = self._require_text(
                step.get("title"), "step title"
            )
            step_description = self._require_text(
                step.get("description"), "step description"
            )

            if step_id in seen_step_ids:
                raise ValueError(f"Duplicate step_id: {step_id}")
            seen_step_ids.add(step_id)

            requires_approval = step.get("requires_approval", False)
            if not isinstance(requires_approval, bool):
                raise ValueError("requires_approval must be a boolean")

            normalized_steps.append({
                "step_id": step_id,
                "title": step_title,
                "description": step_description,
                "requires_approval": requires_approval,
            })

        self._runbooks[runbook_id] = {
            "runbook_id": runbook_id,
            "title": title,
            "description": description,
            "incident_types": sorted(set(incident_types)),
            "services": sorted(set(services)),
            "risk_level": risk_level,
            "steps": normalized_steps,
        }

    def get_runbook(self, runbook_id: str) -> dict[str, Any]:
        """Return a copy of a registered runbook."""
        if runbook_id not in self._runbooks:
            raise ValueError(f"Unknown runbook: {runbook_id}")

        return deepcopy(self._runbooks[runbook_id])

    def list_runbooks(
        self,
        incident_type: str | None = None,
        service_name: str | None = None,
        risk_level: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter the runbook catalog."""
        results = []

        for runbook in self._runbooks.values():
            if (
                incident_type is not None
                and incident_type.casefold()
                not in runbook["incident_types"]
            ):
                continue

            if (
                service_name is not None
                and service_name.casefold() not in runbook["services"]
            ):
                continue

            if risk_level is not None and risk_level != runbook["risk_level"]:
                continue

            results.append(deepcopy(runbook))

        return sorted(results, key=lambda item: item["runbook_id"])

    def recommend_runbooks(
        self,
        incident: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Recommend runbooks using incident type and affected services."""
        if not isinstance(incident, dict):
            raise ValueError("Incident must be a dictionary")

        incident_type = self._require_text(
            incident.get("incident_type"), "incident_type"
        ).casefold()

        affected_services = incident.get("affected_services", [])
        if not isinstance(affected_services, list):
            raise ValueError("affected_services must be a list")

        affected_services = {
            self._require_text(service, "affected_service").casefold()
            for service in affected_services
        }

        recommendations = []

        for runbook in self._runbooks.values():
            type_match = incident_type in runbook["incident_types"]
            matching_services = sorted(
                affected_services.intersection(runbook["services"])
            )

            if not type_match and not matching_services:
                continue

            # Matching score is a ranking indicator, not a probability.
            score = (
                0.7 * int(type_match)
                + 0.3 * int(bool(matching_services))
            )

            reasons = []
            if type_match:
                reasons.append("Incident type matches")
            if matching_services:
                reasons.append(
                    "Affected services match: "
                    + ", ".join(matching_services)
                )

            recommendations.append({
                "runbook": deepcopy(runbook),
                "match_score": round(score, 2),
                "match_reasons": reasons,
            })

        risk_priority = {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
        }

        return sorted(
            recommendations,
            key=lambda item: (
                -item["match_score"],
                -risk_priority[item["runbook"]["risk_level"]],
                item["runbook"]["runbook_id"],
            ),
        )

    def create_execution(
        self,
        runbook_id: str,
        incident_id: str,
    ) -> dict[str, Any]:
        """Create an execution record without executing any action."""
        runbook = self.get_runbook(runbook_id)
        incident_id = self._require_text(incident_id, "incident_id")

        requires_approval = (
            runbook["risk_level"] in {"high", "critical"}
            or any(step["requires_approval"] for step in runbook["steps"])
        )

        execution_id = str(uuid4())

        execution = {
            "execution_id": execution_id,
            "runbook_id": runbook_id,
            "incident_id": incident_id,
            "status": "pending_approval" if requires_approval else "ready",
            "approved_by": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "steps": [
                {
                    **deepcopy(step),
                    "status": "pending",
                    "note": None,
                }
                for step in runbook["steps"]
            ],
        }

        self._executions[execution_id] = execution
        return deepcopy(execution)

    def approve_execution(
        self,
        execution_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        """Record human approval for a sensitive runbook."""
        execution = self._require_execution(execution_id)

        approved_by = self._require_text(approved_by, "approved_by")

        if execution["status"] != "pending_approval":
            raise ValueError("Execution is not awaiting approval")

        execution["approved_by"] = approved_by
        execution["status"] = "ready"

        return deepcopy(execution)

    def _require_execution(self, execution_id: str) -> dict[str, Any]:
        if execution_id not in self._executions:
            raise ValueError(f"Unknown execution: {execution_id}")

        return self._executions[execution_id]

    def update_step(
        self,
        execution_id: str,
        step_id: str,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Update a step's tracking status; does not execute the step."""
        execution = self._require_execution(execution_id)

        if execution["status"] not in {"ready", "in_progress"}:
            raise ValueError(
                "Execution must be approved and active before updating steps"
            )

        if status not in self.VALID_STEP_STATUSES:
            raise ValueError(f"Invalid step status: {status}")

        steps = execution["steps"]
        step_index = next(
            (
                index
                for index, step in enumerate(steps)
                if step["step_id"] == step_id
            ),
            None,
        )

        if step_index is None:
            raise ValueError(f"Unknown step: {step_id}")

        # Enforce the declared execution order.
        previous_steps = steps[:step_index]
        if any(
            step["status"] not in {"completed", "skipped"}
            for step in previous_steps
        ):
            raise ValueError("Previous steps must be completed or skipped")

        step = steps[step_index]

        if step["status"] in {"completed", "failed", "skipped"}:
            raise ValueError("Step is already in a final state")

        if note is not None and not isinstance(note, str):
            raise ValueError("note must be a string")

        step["status"] = status
        step["note"] = note

        if status == "failed":
            execution["status"] = "failed"
        elif all(
            item["status"] in {"completed", "skipped"}
            for item in steps
        ):
            execution["status"] = "completed"
        else:
            execution["status"] = "in_progress"

        return deepcopy(execution)

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        """Return the current execution tracking record."""
        return deepcopy(self._require_execution(execution_id))
