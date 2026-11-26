
"""Service dependency topology for the AIOps Intelligence module."""

from collections import deque
from typing import Any


class ServiceTopology:
    """Manage services and their dependency relationships.

    An edge A -> B means that service A depends on service B.
    """

    def __init__(self) -> None:
        self._services: dict[str, dict[str, Any]] = {}
        self._dependencies: dict[str, set[str]] = {}

    def add_service(
        self,
        service_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a service with optional metadata."""
        if not isinstance(service_name, str) or not service_name.strip():
            raise ValueError("Service name must be a non-empty string")

        service_name = service_name.strip()

        if service_name in self._services:
            raise ValueError(f"Service already exists: {service_name}")

        self._services[service_name] = dict(metadata or {})
        self._dependencies[service_name] = set()

    def _require_service(self, service_name: str) -> None:
        if service_name not in self._services:
            raise ValueError(f"Unknown service: {service_name}")

    def _has_path(self, start: str, target: str) -> bool:
        """Check whether target is reachable from start."""
        visited = set()
        queue = deque([start])

        while queue:
            current = queue.popleft()

            if current == target:
                return True

            if current in visited:
                continue

            visited.add(current)
            queue.extend(self._dependencies[current] - visited)

        return False

    def add_dependency(self, service: str, depends_on: str) -> None:
        """Declare that service depends on another service."""
        self._require_service(service)
        self._require_service(depends_on)

        if service == depends_on:
            raise ValueError("A service cannot depend on itself")

        if self._has_path(depends_on, service):
            raise ValueError(
                f"Dependency would create a cycle: {service} -> {depends_on}"
            )

        self._dependencies[service].add(depends_on)

    def get_services(self) -> list[str]:
        """Return all registered service names."""
        return sorted(self._services)

    def get_dependencies(self, service: str) -> list[str]:
        """Return the direct dependencies of a service."""
        self._require_service(service)
        return sorted(self._dependencies[service])

    def get_dependents(self, service: str) -> list[str]:
        """Return services that directly depend on this service."""
        self._require_service(service)

        return sorted(
            name
            for name, dependencies in self._dependencies.items()
            if service in dependencies
        )

    def get_affected_services(self, failed_service: str) -> list[str]:
        """Return all transitive dependents of a failed service."""
        self._require_service(failed_service)

        affected = set()
        queue = deque([failed_service])

        while queue:
            current = queue.popleft()

            for dependent in self.get_dependents(current):
                if dependent not in affected:
                    affected.add(dependent)
                    queue.append(dependent)

        return sorted(affected)

    def to_dict(self) -> dict[str, Any]:
        """Export the topology in a JSON-compatible structure."""
        return {
            "services": {
                name: self._services[name]
                for name in sorted(self._services)
            },
            "dependencies": {
                name: sorted(self._dependencies[name])
                for name in sorted(self._dependencies)
            },
        }
