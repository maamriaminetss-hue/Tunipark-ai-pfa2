"""Discover service names exposed by Prometheus label values."""

from __future__ import annotations

import os
from typing import Any, Callable

import requests


class PrometheusServiceDiscoveryError(RuntimeError):
    """Raised when Prometheus cannot provide a valid service snapshot."""


class PrometheusServiceDiscoveryReader:
    """Read the values of Prometheus' ``service_name`` label."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 5.0,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        effective_url = (
            base_url
            if base_url is not None
            else os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        )
        self.base_url = effective_url.strip().rstrip("/")

        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Prometheus URL must use HTTP or HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("Timeout must be greater than zero")

        self.timeout_seconds = timeout_seconds
        self._http_get = http_get or requests.get

    def fetch_services(self) -> list[str]:
        """Return a sorted, unique snapshot of ``service_name`` label values.

        An empty list is a valid snapshot. Callers must not interpret it as
        proof that previously observed services have disappeared.
        """
        url = f"{self.base_url}/api/v1/label/service_name/values"

        try:
            response = self._http_get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PrometheusServiceDiscoveryError(
                f"Unable to retrieve a valid service snapshot from Prometheus: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise PrometheusServiceDiscoveryError(
                "Prometheus response must be a JSON object"
            )

        if payload.get("status") != "success":
            reason = payload.get("error", "unknown Prometheus API error")
            raise PrometheusServiceDiscoveryError(
                f"Prometheus service discovery failed: {reason}"
            )

        raw_services = payload.get("data")
        if not isinstance(raw_services, list):
            raise PrometheusServiceDiscoveryError(
                "Prometheus response must contain a list named 'data'"
            )

        service_names = {
            item.strip()
            for item in raw_services
            if isinstance(item, str) and item.strip()
        }
        return sorted(service_names)
