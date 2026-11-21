"""One-pass, deduplicated Prometheus-to-PostgreSQL anomaly detection cycle.

The cycle is intentionally invoked explicitly by an API route or an external
scheduler. It never executes remediation commands and never creates incidents;
it writes metric_anomaly events only after consecutive-sample confirmation.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aiops_platform.database.anomaly_detection_crud import (
    lock_or_create_detection_state,
)
from aiops_platform.database.event_crud import create_event
from aiops_platform.database.models import Service
from aiops_platform.detector.anomaly_detector import detect_anomalies
from aiops_platform.intelligence.monitoring.prometheus_anomaly_reader import (
    PrometheusAnomalyReader,
)
from aiops_platform.intelligence.monitoring.prometheus_freshness_reader import (
    PrometheusMetricFreshnessReader,
)


# Initial guardrails for the OpenTelemetry Demo. These are configurable
# operational starting points, not empirically calibrated SLOs.
DEFAULT_FIXED_UPPER_LIMITS: dict[str, float] = {
    "http_5xx_ratio": 0.05,
    "http_p95_latency_seconds": 1.0,
    "cpu_busy_ratio": 0.90,
    "memory_used_ratio": 0.90,
}

# Minimum absolute change required for a MAD-only candidate to be treated as
# operationally meaningful. These are provisional noise floors (ratio units
# for ratios, seconds for latency), not SLOs. Fixed guardrails remain separate.
DEFAULT_MAD_MINIMUM_ABSOLUTE_DEVIATIONS: dict[str, float] = {
    "http_5xx_ratio": 0.01,
    "http_p95_latency_seconds": 0.01,
    "cpu_busy_ratio": 0.05,
    "memory_used_ratio": 0.05,
}


class AnomalyDetectionService:
    """Evaluate Prometheus history and emit one event per anomaly episode."""

    def __init__(
        self,
        reader: Any | None = None,
        freshness_reader: Any | None = None,
        *,
        history_minutes: int = 60,
        step: str = "1m",
        window: int = 30,
        min_history: int = 12,
        mad_threshold: float = 3.5,
        ewma_threshold: float = 3.5,
        ewma_alpha: float = 0.3,
        confirmation_samples: int = 2,
        recovery_samples: int = 2,
        max_sample_age_seconds: float = 180.0,
        fixed_upper_limits: dict[str, float] | None = None,
        mad_minimum_absolute_deviations: dict[str, float] | None = None,
    ) -> None:
        if history_minutes <= 0:
            raise ValueError("history_minutes must be positive")
        if window < 2 or min_history < 2:
            raise ValueError("window and min_history must be at least 2")
        if confirmation_samples < 1 or recovery_samples < 1:
            raise ValueError("confirmation and recovery samples must be positive")
        if max_sample_age_seconds <= 0 or not math.isfinite(max_sample_age_seconds):
            raise ValueError("max_sample_age_seconds must be positive and finite")

        self.reader = reader if reader is not None else PrometheusAnomalyReader()
        self.freshness_reader = (
            freshness_reader
            if freshness_reader is not None
            else PrometheusMetricFreshnessReader(
                prometheus_url=getattr(self.reader, "prometheus_url", None)
            )
        )
        self.history_minutes = history_minutes
        self.step = step
        self.window = window
        self.min_history = min_history
        self.mad_threshold = mad_threshold
        self.ewma_threshold = ewma_threshold
        self.ewma_alpha = ewma_alpha
        self.confirmation_samples = confirmation_samples
        self.recovery_samples = recovery_samples
        self.max_sample_age_seconds = float(max_sample_age_seconds)

        if fixed_upper_limits is None:
            self.fixed_upper_limits = dict(DEFAULT_FIXED_UPPER_LIMITS)
            for metric_name in list(self.fixed_upper_limits):
                env_name = (
                    "ANOMALY_LIMIT_"
                    + metric_name.upper().replace("_", "_")
                )
                raw = os.getenv(env_name)
                if raw is not None:
                    try:
                        limit = float(raw)
                    except ValueError as exc:
                        raise ValueError(f"{env_name} must be numeric") from exc
                    if not math.isfinite(limit):
                        raise ValueError(f"{env_name} must be finite")
                    self.fixed_upper_limits[metric_name] = limit
        else:
            self.fixed_upper_limits = dict(fixed_upper_limits)

        for metric_name, limit in self.fixed_upper_limits.items():
            if not isinstance(metric_name, str) or not metric_name.strip():
                raise ValueError("fixed limit metric names must be non-empty")
            if isinstance(limit, bool) or not isinstance(limit, (int, float)):
                raise ValueError(f"fixed limit for {metric_name} must be numeric")
            if not math.isfinite(float(limit)):
                raise ValueError(f"fixed limit for {metric_name} must be finite")

        if mad_minimum_absolute_deviations is None:
            self.mad_minimum_absolute_deviations = dict(
                DEFAULT_MAD_MINIMUM_ABSOLUTE_DEVIATIONS
            )
            for metric_name in list(self.mad_minimum_absolute_deviations):
                env_name = (
                    "ANOMALY_MAD_MIN_DEVIATION_"
                    + metric_name.upper()
                )
                raw = os.getenv(env_name)
                if raw is not None:
                    try:
                        minimum = float(raw)
                    except ValueError as exc:
                        raise ValueError(f"{env_name} must be numeric") from exc
                    self.mad_minimum_absolute_deviations[metric_name] = minimum
        else:
            self.mad_minimum_absolute_deviations = dict(
                mad_minimum_absolute_deviations
            )

        for metric_name, minimum in self.mad_minimum_absolute_deviations.items():
            if not isinstance(metric_name, str) or not metric_name.strip():
                raise ValueError("MAD minimum-deviation metric names must be non-empty")
            if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                raise ValueError(
                    f"MAD minimum deviation for {metric_name} must be numeric"
                )
            if not math.isfinite(float(minimum)) or minimum < 0:
                raise ValueError(
                    f"MAD minimum deviation for {metric_name} must be finite and non-negative"
                )

    @staticmethod
    def _as_utc_datetime(value: Any) -> datetime:
        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
        if not isinstance(value, datetime):
            raise ValueError("Prometheus sample timestamp must be a datetime")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _finite_or_none(value: Any) -> float | None:
        try:
            converted = float(value)
        except (TypeError, ValueError):
            return None
        return converted if math.isfinite(converted) else None

    def analyze_metric_frame(
        self,
        metric_name: str,
        frame: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        """Return per-service anomaly decisions without touching the database."""
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("frame must be a pandas DataFrame")
        if frame.empty:
            return []

        results: dict[tuple[str, datetime], dict[str, Any]] = {}
        methods = [
            ("mad", self.mad_threshold, None),
            ("ewma", self.ewma_threshold, None),
        ]
        fixed_limit = self.fixed_upper_limits.get(metric_name)
        if fixed_limit is not None:
            methods.append(("fixed", 0.0, fixed_limit))

        for method, threshold, fixed_upper in methods:
            detected = detect_anomalies(
                frame,
                method=method,
                window=self.window,
                min_history=self.min_history,
                threshold=threshold if method != "fixed" else 1.0,
                alpha=self.ewma_alpha,
                fixed_upper=fixed_upper,
            )
            for row in detected.to_dict(orient="records"):
                service_name = row.get("service_name")
                if not isinstance(service_name, str) or not service_name.strip():
                    continue
                timestamp = self._as_utc_datetime(row.get("timestamp"))
                key = (service_name.strip(), timestamp)
                item = results.setdefault(
                    key,
                    {
                        "timestamp": timestamp,
                        "value": float(row["value"]),
                        "metric_name": metric_name,
                        "service_name": service_name.strip(),
                        "service_namespace": row.get("service_namespace"),
                        "methods": [],
                        "scores": {},
                        "expected": {},
                        "reasons": {},
                    },
                )
                score = self._finite_or_none(row.get("score"))
                expected = self._finite_or_none(row.get("expected"))
                reason = str(row.get("reason", "unknown"))
                is_anomaly = bool(row.get("is_anomaly"))

                # MAD is scale-relative. If historical MAD is zero or tiny,
                # a practically negligible absolute change can receive an
                # enormous score. Apply a metric-specific absolute floor only
                # to MAD candidates; EWMA and fixed guardrails remain intact.
                if method == "mad" and is_anomaly:
                    minimum_deviation = self.mad_minimum_absolute_deviations.get(
                        metric_name, 0.0
                    )
                    absolute_deviation = (
                        abs(float(row["value"]) - expected)
                        if expected is not None
                        else 0.0
                    )
                    if absolute_deviation < minimum_deviation:
                        is_anomaly = False
                        reason = "below_metric_minimum_absolute_deviation"

                item["scores"][method] = score
                item["expected"][method] = expected
                item["reasons"][method] = reason
                if is_anomaly:
                    item["methods"].append(method)

        output = []
        for item in results.values():
            item["methods"] = sorted(set(item["methods"]))
            item["is_anomaly"] = bool(item["methods"])
            item["fixed_upper_limit"] = fixed_limit
            output.append(item)

        return sorted(
            output,
            key=lambda item: (item["service_name"], item["timestamp"]),
        )

    def _event_severity(self, metric_name: str, value: float) -> str:
        if metric_name == "http_5xx_ratio" and value >= 0.20:
            return "high"
        if metric_name == "http_p95_latency_seconds" and value >= 2.0:
            return "high"
        if metric_name in {"cpu_busy_ratio", "memory_used_ratio"} and value >= 0.98:
            return "high"
        return "warning"

    def _create_anomaly_event(
        self,
        db: Session,
        observation: dict[str, Any],
    ) -> Any:
        service_id = db.scalar(
            select(Service.id).where(Service.name == observation["service_name"])
        )
        metric_name = observation["metric_name"]
        value = float(observation["value"])
        methods = observation["methods"]
        message = (
            f"Anomaly confirmed for service {observation['service_name']}: "
            f"{metric_name}={value:.6g}; methods={','.join(methods)}."
        )
        return create_event(
            db=db,
            event_type="metric_anomaly",
            severity=self._event_severity(metric_name, value),
            service_id=service_id,
            source="prometheus",
            message=message,
            metric_name=metric_name,
            metric_value=value,
            labels={
                "service_name": observation["service_name"],
                "service_namespace": observation.get("service_namespace"),
                "detector_methods": methods,
                "scores": observation["scores"],
                "expected": observation["expected"],
                "reasons": observation["reasons"],
                "fixed_upper_limit": observation.get("fixed_upper_limit"),
                "confirmation_samples": self.confirmation_samples,
            },
            occurred_at=observation["timestamp"],
            commit=False,
        )

    def run_once(self, db: Session, *, end: datetime | None = None) -> dict[str, Any]:
        """Fetch metrics, evaluate latest new samples, and persist events/state.

        Each event is emitted only on transition into an anomaly episode after
        consecutive confirmation. A configurable number of normal samples is
        required before the same metric/service can trigger a new episode.
        """
        end_at = self._as_utc_datetime(end or datetime.now(timezone.utc))
        metric_results: dict[str, dict[str, Any]] = {}
        total_processed = 0
        total_events = 0
        total_stale = 0
        succeeded = 0

        for metric_name in self.reader.queries:
            try:
                frame = self.reader.fetch_metric(
                    metric_name,
                    minutes=self.history_minutes,
                    step=self.step,
                    end=end_at,
                )
                ages = self.freshness_reader.fetch_sample_ages(
                    metric_name,
                    end=end_at,
                )
            except Exception as exc:
                metric_results[metric_name] = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                }
                continue

            succeeded += 1
            if frame.empty:
                metric_results[metric_name] = {
                    "status": "no_data",
                    "services_seen": 0,
                    "samples_seen": 0,
                    "samples_processed": 0,
                    "events_created": 0,
                    "stale_services": 0,
                }
                continue

            analyzed = self.analyze_metric_frame(metric_name, frame)
            by_service: dict[str, list[dict[str, Any]]] = {}
            for item in analyzed:
                by_service.setdefault(item["service_name"], []).append(item)

            metric_processed = 0
            metric_events = 0
            metric_stale = 0
            for service_name, observations in by_service.items():
                sample_age = ages.get(service_name)
                if sample_age is None or sample_age > self.max_sample_age_seconds:
                    metric_stale += 1
                    continue

                state = lock_or_create_detection_state(
                    db,
                    metric_name=metric_name,
                    service_name=service_name,
                )
                observations.sort(key=lambda item: item["timestamp"])

                if state.last_processed_at is None:
                    new_observations = observations[-self.confirmation_samples :]
                else:
                    last_processed = self._as_utc_datetime(state.last_processed_at)
                    new_observations = [
                        item for item in observations
                        if item["timestamp"] > last_processed
                    ]

                for observation in new_observations:
                    if observation["is_anomaly"]:
                        state.anomaly_streak += 1
                        state.recovery_streak = 0
                        if (
                            not state.is_active
                            and state.anomaly_streak >= self.confirmation_samples
                        ):
                            event = self._create_anomaly_event(db, observation)
                            state.is_active = True
                            state.last_event_id = event.id
                            metric_events += 1
                    else:
                        state.anomaly_streak = 0
                        if state.is_active:
                            state.recovery_streak += 1
                            if state.recovery_streak >= self.recovery_samples:
                                state.is_active = False
                                state.recovery_streak = 0
                        else:
                            state.recovery_streak = 0

                    state.last_processed_at = observation["timestamp"]
                    metric_processed += 1

            total_processed += metric_processed
            total_events += metric_events
            total_stale += metric_stale

            metric_results[metric_name] = {
                "status": "ok",
                "services_seen": len(by_service),
                "samples_seen": len(frame),
                "samples_processed": metric_processed,
                "events_created": metric_events,
                "stale_services": metric_stale,
            }

        try:
            db.commit()
        except Exception:
            db.rollback()
            raise

        total_metrics = len(self.reader.queries)
        if succeeded == 0:
            status = "error"
        elif succeeded < total_metrics:
            status = "partial"
        elif total_processed == 0:
            status = "no_new_samples"
        else:
            status = "ok"

        return {
            "status": status,
            "started_at": end_at.isoformat(),
            "metrics": metric_results,
            "samples_processed": total_processed,
            "stale_service_series_skipped": total_stale,
            "events_created": total_events,
            "policy": {
                "methods": ["mad", "ewma", "fixed_guardrail"],
                "confirmation_samples": self.confirmation_samples,
                "recovery_samples": self.recovery_samples,
                "min_history": self.min_history,
                "history_minutes": self.history_minutes,
                "max_sample_age_seconds": self.max_sample_age_seconds,
                "fixed_upper_limits": self.fixed_upper_limits,
                "mad_minimum_absolute_deviations": (
                    self.mad_minimum_absolute_deviations
                ),
            },
            "note": (
                "This is a single detection cycle. It creates metric events only; "
                "it does not create incidents or execute remediation."
            ),
        }
