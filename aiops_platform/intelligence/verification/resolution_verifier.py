"""Verify remediation outcomes from post-action metric observations.

This module is deliberately read-only: it evaluates observations supplied by
an existing metrics reader and never executes corrective actions.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


class ResolutionVerifier:
    """Evaluate whether a service has recovered after a remediation."""

    VALID_OPERATORS = {"lte", "lt", "gte", "gt"}

    def __init__(
        self,
        required_consecutive_samples: int = 3,
        max_sample_age_seconds: float = 180.0,
    ) -> None:
        if (
            isinstance(required_consecutive_samples, bool)
            or not isinstance(required_consecutive_samples, int)
            or required_consecutive_samples < 1
        ):
            raise ValueError("required_consecutive_samples must be a positive integer")
        if (
            isinstance(max_sample_age_seconds, bool)
            or not isinstance(max_sample_age_seconds, (int, float))
            or not math.isfinite(float(max_sample_age_seconds))
            or max_sample_age_seconds <= 0
        ):
            raise ValueError("max_sample_age_seconds must be a positive finite number")
        self.required_consecutive_samples = required_consecutive_samples
        self.max_sample_age_seconds = float(max_sample_age_seconds)

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _parse_timestamp(value: Any, field: str) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Invalid {field}: {value!r}") from exc
        else:
            raise ValueError(f"Invalid {field}: {value!r}")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _normalize_checks(cls, checks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(checks, (str, bytes)) or not isinstance(checks, Sequence) or not checks:
            raise ValueError("checks must be a non-empty sequence")
        normalized = []
        seen_metrics = set()
        for index, check in enumerate(checks):
            if not isinstance(check, Mapping):
                raise ValueError(f"Check {index} must be a mapping")
            metric_name = cls._require_text(check.get("metric_name"), "metric_name")
            if metric_name in seen_metrics:
                raise ValueError(f"Duplicate metric check: {metric_name}")
            seen_metrics.add(metric_name)
            operator = check.get("operator")
            if not isinstance(operator, str) or operator not in cls.VALID_OPERATORS:
                raise ValueError(
                    f"Invalid operator for {metric_name}; expected one of "
                    f"{sorted(cls.VALID_OPERATORS)}"
                )
            threshold = check.get("threshold")
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(float(threshold))
            ):
                raise ValueError(f"threshold for {metric_name} must be finite and numeric")
            normalized.append({
                "metric_name": metric_name,
                "operator": operator,
                "threshold": float(threshold),
            })
        return normalized

    @staticmethod
    def _to_records(observations: Any) -> list[Mapping[str, Any]]:
        if hasattr(observations, "to_dict") and not isinstance(observations, Mapping):
            try:
                records = observations.to_dict(orient="records")
            except TypeError:
                records = observations.to_dict("records")
        else:
            records = observations
        if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
            raise ValueError("observations must be an iterable of mappings or a DataFrame")
        result = list(records)
        if any(not isinstance(item, Mapping) for item in result):
            raise ValueError("each observation must be a mapping")
        return result

    @staticmethod
    def _matches(value: float, operator: str, threshold: float) -> bool:
        if operator == "lte":
            return value <= threshold
        if operator == "lt":
            return value < threshold
        if operator == "gte":
            return value >= threshold
        return value > threshold

    def verify(
        self,
        service_name: str,
        observations: Any,
        checks: Sequence[Mapping[str, Any]],
        remediation_completed_at: datetime | str,
        verified_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        service_name = self._require_text(service_name, "service_name")
        completed_at = self._parse_timestamp(remediation_completed_at, "remediation_completed_at")
        checked_at = self._parse_timestamp(
            verified_at if verified_at is not None else datetime.now(timezone.utc),
            "verified_at",
        )
        if checked_at < completed_at:
            raise ValueError("verified_at cannot be earlier than remediation_completed_at")

        normalized_checks = self._normalize_checks(checks)
        records = self._to_records(observations)
        warnings: list[str] = []
        by_metric: dict[str, dict[datetime, float]] = {
            check["metric_name"]: {} for check in normalized_checks
        }

        for index, row in enumerate(records):
            metric_name = row.get("metric_name")
            if row.get("service_name") != service_name or not isinstance(metric_name, str) or metric_name not in by_metric:
                continue
            try:
                timestamp = self._parse_timestamp(row.get("timestamp"), "observation timestamp")
                raw_value = row.get("value")
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or not math.isfinite(float(raw_value))
                ):
                    raise ValueError("observation value must be finite and numeric")
            except ValueError as exc:
                warnings.append(f"Observation {index} ignored: {exc}")
                continue
            if timestamp < completed_at:
                continue
            if timestamp > checked_at:
                warnings.append(f"Observation {index} is newer than verified_at and was ignored")
                continue
            if timestamp in by_metric[metric_name]:
                warnings.append(
                    f"Duplicate timestamp for {service_name}/{metric_name}; last value retained"
                )
            by_metric[metric_name][timestamp] = float(raw_value)

        metric_reports = []
        required = self.required_consecutive_samples
        for check in normalized_checks:
            metric_name = check["metric_name"]
            points = sorted(by_metric[metric_name].items(), key=lambda item: item[0])
            latest_timestamp = points[-1][0] if points else None
            latest_age = (
                (checked_at - latest_timestamp).total_seconds()
                if latest_timestamp is not None else None
            )
            selected: list[tuple[datetime, float]] = []
            if not points:
                metric_status, reason = "inconclusive", "no_post_remediation_observations"
            elif latest_age is not None and latest_age > self.max_sample_age_seconds:
                metric_status, reason = "inconclusive", "latest_observation_is_stale"
                selected = points[-required:]
            elif len(points) < required:
                metric_status, reason = "inconclusive", "insufficient_consecutive_observations"
                selected = points
            else:
                selected = points[-required:]
                selected_ages = [(checked_at - timestamp).total_seconds() for timestamp, _ in selected]
                if any(age > self.max_sample_age_seconds for age in selected_ages):
                    metric_status, reason = "inconclusive", "selected_observation_is_stale"
                else:
                    outcomes = [
                        self._matches(value, check["operator"], check["threshold"])
                        for _, value in selected
                    ]
                    if all(outcomes):
                        metric_status, reason = "resolved", "required_consecutive_samples_within_threshold"
                    elif not any(outcomes):
                        metric_status, reason = "failed", "required_consecutive_samples_outside_threshold"
                    else:
                        metric_status, reason = "inconclusive", "metric_state_is_not_stable"

            metric_reports.append({
                "metric_name": metric_name,
                "status": metric_status,
                "reason": reason,
                "operator": check["operator"],
                "threshold": check["threshold"],
                "required_consecutive_samples": required,
                "samples_considered": len(selected),
                "values": [value for _, value in selected],
                "latest_timestamp": (
                    latest_timestamp.isoformat().replace("+00:00", "Z")
                    if latest_timestamp is not None else None
                ),
                "latest_age_seconds": latest_age,
            })

        statuses = [item["status"] for item in metric_reports]
        if any(status == "failed" for status in statuses):
            overall_status = "failed"
        elif statuses and all(status == "resolved" for status in statuses):
            overall_status = "resolved"
        else:
            overall_status = "inconclusive"
        return {
            "service_name": service_name,
            "status": overall_status,
            "remediation_completed_at": completed_at.isoformat().replace("+00:00", "Z"),
            "verified_at": checked_at.isoformat().replace("+00:00", "Z"),
            "required_consecutive_samples": required,
            "metrics": metric_reports,
            "warnings": warnings,
            "read_only": True,
        }
