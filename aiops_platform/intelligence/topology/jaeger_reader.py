"""Read service dependencies from the Jaeger HTTP API."""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import requests

from . import ServiceTopology


class JaegerClientError(RuntimeError):
    """Raised when Jaeger cannot provide valid dependency data."""


class JaegerDependencyReader:
    """Fetch Jaeger services and construct a ServiceTopology graph."""

    def __init__(
        self,
        api_base_url: str | None = None,
        timeout_seconds: float = 5.0,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        base_url = (
            api_base_url
            if api_base_url is not None
            else os.getenv(
                "JAEGER_API_BASE_URL",
                "http://localhost:16686/jaeger/ui/api",
            )
        )

        self.api_base_url = base_url.strip().rstrip("/")

        if not self.api_base_url.startswith(("http://", "https://")):
            raise ValueError("Jaeger API URL must use HTTP or HTTPS")

        if timeout_seconds <= 0:
            raise ValueError("Timeout must be greater than zero")

        self.timeout_seconds = timeout_seconds
        self._http_get = http_get or requests.get

    def _get_json(
        self,
        endpoint: str,
        params: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Perform an HTTP GET and validate the JSON response."""
        url = f"{self.api_base_url}/{endpoint.lstrip('/')}"

        try:
            response = self._http_get(
                url,
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()

        except (requests.RequestException, ValueError) as exc:
            raise JaegerClientError(
                f"Unable to retrieve valid data from Jaeger: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise JaegerClientError(
                f"Invalid JSON structure returned by {endpoint}"
            )

        return payload

    def fetch_services(self) -> list[str]:
        """Return a sorted, unique snapshot from Jaeger's services API."""
        payload = self._get_json("v3/services")
        raw_services = payload.get("services")

        if not isinstance(raw_services, list):
            raise JaegerClientError(
                "Jaeger services response must contain a list named 'services'"
            )

        names = {
            item.strip()
            for item in raw_services
            if isinstance(item, str) and item.strip()
        }
        return sorted(names)
    def fetch_topology(
        self,
        lookback_ms: int = 3_600_000,
        end_ts: int | None = None,
    ) -> dict[str, Any]:
        """Fetch dependencies and build a directed service graph.

        Jaeger parent -> child means caller -> callee.
        In ServiceTopology, this is represented as:
        caller depends on callee.
        """
        if (
            isinstance(lookback_ms, bool)
            or not isinstance(lookback_ms, int)
            or lookback_ms <= 0
        ):
            raise ValueError("lookback_ms must be a positive integer")

        effective_end_ts = (
            int(time.time() * 1000) if end_ts is None else end_ts
        )

        if (
            isinstance(effective_end_ts, bool)
            or not isinstance(effective_end_ts, int)
            or effective_end_ts <= 0
        ):
            raise ValueError("end_ts must be a positive integer")

        services_payload = self._get_json("v3/services")
        dependencies_payload = self._get_json(
            "dependencies",
            params={
                "endTs": effective_end_ts,
                "lookback": lookback_ms,
            },
        )

        raw_services = services_payload.get("services")
        raw_dependencies = dependencies_payload.get("data")

        if not isinstance(raw_services, list):
            raise JaegerClientError(
                "Jaeger services response must contain a list named 'services'"
            )

        if not isinstance(raw_dependencies, list):
            raise JaegerClientError(
                "Jaeger dependencies response must contain a list named 'data'"
            )

        warnings: list[str] = []
        rejected_edges: list[dict[str, Any]] = []

        service_names: set[str] = set()

        for item in raw_services:
            if isinstance(item, str) and item.strip():
                service_names.add(item.strip())
            else:
                warnings.append("An invalid service name was ignored")

        # Aggregate repeated relations between the same two services.
        edge_counts: dict[tuple[str, str], int] = {}

        for index, item in enumerate(raw_dependencies):
            if not isinstance(item, dict):
                warnings.append(
                    f"Dependency entry {index} is not a JSON object"
                )
                continue

            parent = item.get("parent")
            child = item.get("child")
            call_count = item.get("callCount")

            if (
                not isinstance(parent, str)
                or not parent.strip()
                or not isinstance(child, str)
                or not child.strip()
            ):
                warnings.append(
                    f"Dependency entry {index} has invalid service names"
                )
                continue

            if (
                isinstance(call_count, bool)
                or not isinstance(call_count, int)
                or call_count < 0
            ):
                warnings.append(
                    f"Dependency entry {index} has an invalid callCount"
                )
                continue

            parent = parent.strip()
            child = child.strip()

            if parent == child:
                rejected_edges.append(
                    {
                        "parent": parent,
                        "child": child,
                        "call_count": call_count,
                        "reason": "self_call_ignored",
                    }
                )
                continue

            service_names.update((parent, child))

            edge = (parent, child)
            edge_counts[edge] = edge_counts.get(edge, 0) + call_count

        # A dependency may refer to a service absent from /v3/services.
        listed_services = {
            item.strip()
            for item in raw_services
            if isinstance(item, str) and item.strip()
        }

        for name in sorted(service_names - listed_services):
            warnings.append(
                f"Service '{name}' was discovered in dependencies "
                "but not in the services list"
            )

        topology = ServiceTopology()

        for service_name in sorted(service_names):
            topology.add_service(
                service_name,
                metadata={"source": "jaeger"},
            )

        accepted_edges: list[dict[str, Any]] = []

        for (parent, child), call_count in sorted(edge_counts.items()):
            try:
                # A -> B means A depends on B.
                topology.add_dependency(parent, child)

            except ValueError as exc:
                rejected_edges.append(
                    {
                        "parent": parent,
                        "child": child,
                        "call_count": call_count,
                        "reason": str(exc),
                    }
                )
                continue

            accepted_edges.append(
                {
                    "parent": parent,
                    "child": child,
                    "call_count": call_count,
                }
            )

        return {
            "source": "jaeger",
            "window": {
                "end_ts": effective_end_ts,
                "lookback_ms": lookback_ms,
            },
            "topology": topology.to_dict(),
            "edges": accepted_edges,
            "rejected_edges": rejected_edges,
            "warnings": warnings,
        }