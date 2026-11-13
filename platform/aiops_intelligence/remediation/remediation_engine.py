
"""Controlled remediation planning and tracking.

This module creates remediation plans and tracks their progress.
It does not execute commands or modify external systems.
"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class RemediationEngine:
    """Manage corrective actions, plans, approvals and tracking."""

    VALID_RISKS = {"low", "medium", "high", "critical"}
    VALID_ACTION_STATUSES = {"completed", "failed", "skipped"}

    def __init__(self) -> None:
        self._actions: dict[str, dict[str, Any]] = {}
        self._plans: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    def register_action(self, action: dict[str, Any]) -> None:
        """Register a corrective action template."""
        if not isinstance(action, dict):
            raise ValueError("Action must be a dictionary")

        action_id = self._require_text(
            action.get("action_id"), "action_id"
        )
        title = self._require_text(action.get("title"), "title")
        description = self._require_text(
            action.get("description"), "description"
        )

        if action_id in self._actions:
            raise ValueError(f"Action already exists: {action_id}")

        risk_level = action.get("risk_level")
        if risk_level not in self.VALID_RISKS:
            raise ValueError(
                f"Invalid risk_level. Expected one of: "
                f"{sorted(self.VALID_RISKS)}"
            )

        incident_types = action.get("incident_types")
        if not isinstance(incident_types, list) or not incident_types:
            raise ValueError("incident_types must be a non-empty list")

        incident_types = [
            self._require_text(item, "incident_type").casefold()
            for item in incident_types
        ]

        services = action.get("services", [])
        if not isinstance(services, list):
            raise ValueError("services must be a list")

        services = [
            self._require_text(item, "service").casefold()
            for item in services
        ]

        requires_approval = action.get("requires_approval", False)
        if not isinstance(requires_approval, bool):
            raise ValueError("requires_approval must be a boolean")

        self._actions[action_id] = {
            "action_id": action_id,
            "title": title,
            "description": description,
            "incident_types": sorted(set(incident_types)),
            "services": sorted(set(services)),
            "risk_level": risk_level,
            "requires_approval": requires_approval,
        }

    def get_action(self, action_id: str) -> dict[str, Any]:
        """Return a copy of a registered corrective action."""
        if action_id not in self._actions:
            raise ValueError(f"Unknown action: {action_id}")

        return deepcopy(self._actions[action_id])

    def list_actions(
        self,
        incident_type: str | None = None,
        service_name: str | None = None,
        risk_level: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter the corrective action catalog."""
        results = []

        for action in self._actions.values():
            if (
                incident_type is not None
                and incident_type.casefold() not in action["incident_types"]
            ):
                continue

            if (
                service_name is not None
                and service_name.casefold() not in action["services"]
            ):
                continue

            if risk_level is not None and risk_level != action["risk_level"]:
                continue

            results.append(deepcopy(action))

        return sorted(results, key=lambda item: item["action_id"])

    def recommend_actions(
        self,
        incident: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Rank corrective actions according to incident context."""
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

        for action in self._actions.values():
            type_match = incident_type in action["incident_types"]
            matching_services = sorted(
                affected_services.intersection(action["services"])
            )

            if not type_match and not matching_services:
                continue

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
                "action": deepcopy(action),
                "match_score": round(score, 2),
                "match_reasons": reasons,
            })

        return sorted(
            recommendations,
            key=lambda item: (
                -item["match_score"],
                item["action"]["action_id"],
            ),
        )

    def create_plan(
        self,
        incident: dict[str, Any],
        action_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a remediation plan; no corrective action is executed."""
        recommendations = self.recommend_actions(incident)

        if action_ids is None:
            selected_ids = [
                item["action"]["action_id"]
                for item in recommendations
            ]
        else:
            if not isinstance(action_ids, list) or not action_ids:
                raise ValueError("action_ids must be a non-empty list")

            selected_ids = []
            for action_id in action_ids:
                action_id = self._require_text(action_id, "action_id")
                if action_id not in self._actions:
                    raise ValueError(f"Unknown action: {action_id}")
                if action_id not in selected_ids:
                    selected_ids.append(action_id)

        if not selected_ids:
            raise ValueError("No corrective actions match this incident")

        recommendation_by_id = {
            item["action"]["action_id"]: item
            for item in recommendations
        }

        plan_actions = []
        for action_id in selected_ids:
            action = self._actions[action_id]
            recommendation = recommendation_by_id.get(action_id)

            plan_actions.append({
                **deepcopy(action),
                "match_score": (
                    recommendation["match_score"]
                    if recommendation else None
                ),
                "status": "pending",
                "note": None,
            })

        requires_approval = any(
            action["requires_approval"]
            or action["risk_level"] in {"high", "critical"}
            for action in plan_actions
        )

        incident_id = self._require_text(
            incident.get("incident_id"), "incident_id"
        )
        plan_id = str(uuid4())

        plan = {
            "plan_id": plan_id,
            "incident_id": incident_id,
            "incident_type": incident["incident_type"].strip().casefold(),
            "status": (
                "pending_approval" if requires_approval else "ready"
            ),
            "approved_by": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actions": plan_actions,
        }

        self._plans[plan_id] = plan
        return deepcopy(plan)

    def _require_plan(self, plan_id: str) -> dict[str, Any]:
        if plan_id not in self._plans:
            raise ValueError(f"Unknown remediation plan: {plan_id}")
        return self._plans[plan_id]

    def approve_plan(
        self,
        plan_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        """Record explicit human approval for a remediation plan."""
        plan = self._require_plan(plan_id)
        approved_by = self._require_text(approved_by, "approved_by")

        if plan["status"] != "pending_approval":
            raise ValueError("Plan is not awaiting approval")

        plan["approved_by"] = approved_by
        plan["status"] = "ready"

        return deepcopy(plan)

    def update_action(
        self,
        plan_id: str,
        action_id: str,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Track an action's reported outcome without executing it."""
        plan = self._require_plan(plan_id)

        if plan["status"] not in {"ready", "in_progress"}:
            raise ValueError(
                "Plan must be approved and active before updating actions"
            )

        if status not in self.VALID_ACTION_STATUSES:
            raise ValueError(f"Invalid action status: {status}")

        if note is not None and not isinstance(note, str):
            raise ValueError("note must be a string")

        actions = plan["actions"]
        action_index = next(
            (
                index
                for index, action in enumerate(actions)
                if action["action_id"] == action_id
            ),
            None,
        )

        if action_index is None:
            raise ValueError(f"Action not found in plan: {action_id}")

        if any(
            action["status"] not in {"completed", "skipped"}
            for action in actions[:action_index]
        ):
            raise ValueError("Previous actions must be completed or skipped")

        action = actions[action_index]

        if action["status"] in self.VALID_ACTION_STATUSES:
            raise ValueError("Action is already in a final state")

        action["status"] = status
        action["note"] = note

        if status == "failed":
            plan["status"] = "failed"
        elif all(
            item["status"] in {"completed", "skipped"}
            for item in actions
        ):
            plan["status"] = "completed"
        else:
            plan["status"] = "in_progress"

        return deepcopy(plan)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        """Return the current remediation plan."""
        return deepcopy(self._require_plan(plan_id))
