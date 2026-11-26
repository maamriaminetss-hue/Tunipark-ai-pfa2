
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any


class ResolutionVerifier:
    """Verify whether an incident meets its post-remediation criteria.

    This component evaluates supplied observations only. It does not query
    monitoring systems or execute remediation actions.
    """

    VALID_OPERATORS = {"<", "<=", ">", ">=", "==", "!="}

    def verify(
        self,
        incident_id: str,
        after_metrics: dict[str, float],
        criteria: list[dict[str, Any]],
        active_incident: bool | None = None,
        before_metrics: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Evaluate resolution criteria and return a verification report.

        Each criterion must contain:
            - metric: metric name
            - operator: one of <, <=, >, >=, ==, !=
            - threshold: numeric threshold

        active_incident:
            True  -> incident is still active.
            False -> incident is no longer active.
            None  -> incident activity is unknown.
        """
        if not isinstance(incident_id, str) or not incident_id.strip():
            raise ValueError("incident_id must be a non-empty string")

        if not isinstance(after_metrics, dict):
            raise ValueError("after_metrics must be a dictionary")

        if not isinstance(criteria, list) or not criteria:
            raise ValueError("At least one resolution criterion is required")

        if active_incident not in (True, False, None):
            raise ValueError("active_incident must be True, False, or None")

        self._validate_metrics(after_metrics, "after_metrics")

        if before_metrics is not None:
            if not isinstance(before_metrics, dict):
                raise ValueError("before_metrics must be a dictionary")
            self._validate_metrics(before_metrics, "before_metrics")

        checks = []
        seen_metrics = set()

        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict):
                raise ValueError(
                    f"Criterion at index {index} must be a dictionary"
                )

            metric = criterion.get("metric")
            operator = criterion.get("operator")
            threshold = criterion.get("threshold")

            if not isinstance(metric, str) or not metric.strip():
                raise ValueError(
                    f"Criterion at index {index} has an invalid metric"
                )

            if metric in seen_metrics:
                raise ValueError(f"Duplicate criterion for metric: {metric}")

            seen_metrics.add(metric)

            if operator not in self.VALID_OPERATORS:
                raise ValueError(
                    f"Unsupported operator for metric {metric}: {operator}"
                )

            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not isfinite(threshold)
            ):
                raise ValueError(
                    f"Threshold for metric {metric} must be a finite number"
                )

            if metric not in after_metrics:
                checks.append({
                    "metric": metric,
                    "operator": operator,
                    "threshold": threshold,
                    "observed": None,
                    "status": "missing",
                    "passed": None,
                })
                continue

            observed = after_metrics[metric]
            passed = self._compare(observed, operator, threshold)

            check = {
                "metric": metric,
                "operator": operator,
                "threshold": threshold,
                "observed": observed,
                "status": "passed" if passed else "failed",
                "passed": passed,
            }

            if before_metrics is not None and metric in before_metrics:
                check["before"] = before_metrics[metric]

            checks.append(check)

        failed_checks = [
            check for check in checks if check["status"] == "failed"
        ]
        missing_checks = [
            check for check in checks if check["status"] == "missing"
        ]

        if active_incident is True or failed_checks:
            status = "unresolved"
            summary = "One or more resolution conditions are not satisfied."
        elif missing_checks or active_incident is None:
            status = "inconclusive"
            summary = "There is not enough evidence to verify resolution."
        else:
            status = "verified"
            summary = "All resolution conditions are satisfied."

        return {
            "incident_id": incident_id,
            "status": status,
            "summary": summary,
            "active_incident": active_incident,
            "checks": checks,
            "total_checks": len(checks),
            "passed_checks": sum(
                check["status"] == "passed" for check in checks
            ),
            "failed_checks": len(failed_checks),
            "missing_checks": len(missing_checks),
        }

    @staticmethod
    def _validate_metrics(metrics: dict[str, float], field: str) -> None:
        for name, value in metrics.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{field} contains an invalid metric name")

            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(
                    f"Metric '{name}' in {field} must be a finite number"
                )

    @staticmethod
    def _compare(
        observed: float,
        operator: str,
        threshold: float,
    ) -> bool:
        operations = {
            "<": lambda: observed < threshold,
            "<=": lambda: observed <= threshold,
            ">": lambda: observed > threshold,
            ">=": lambda: observed >= threshold,
            "==": lambda: observed == threshold,
            "!=": lambda: observed != threshold,
        }
        return operations[operator]()
