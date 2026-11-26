
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "platform"
        / "aiops_intelligence"
        / "topology"
    ),
)

from service_topology import ServiceTopology


@pytest.fixture
def topology():
    graph = ServiceTopology()

    graph.add_service("frontend")
    graph.add_service("payment-api")
    graph.add_service("database")
    graph.add_service("notification-service")

    graph.add_dependency("frontend", "payment-api")
    graph.add_dependency("payment-api", "database")
    graph.add_dependency("notification-service", "database")

    return graph


def test_add_service():
    graph = ServiceTopology()
    graph.add_service("frontend")

    assert graph.get_services() == ["frontend"]


def test_add_duplicate_service():
    graph = ServiceTopology()
    graph.add_service("frontend")

    with pytest.raises(ValueError, match="already exists"):
        graph.add_service("frontend")


def test_get_dependencies(topology):
    assert topology.get_dependencies("frontend") == ["payment-api"]
    assert topology.get_dependencies("payment-api") == ["database"]


def test_get_dependents(topology):
    assert topology.get_dependents("database") == [
        "notification-service",
        "payment-api",
    ]


def test_get_transitive_affected_services(topology):
    assert topology.get_affected_services("database") == [
        "frontend",
        "notification-service",
        "payment-api",
    ]


def test_unknown_service_is_rejected():
    graph = ServiceTopology()

    with pytest.raises(ValueError, match="Unknown service"):
        graph.add_dependency("frontend", "database")


def test_self_dependency_is_rejected(topology):
    with pytest.raises(ValueError, match="itself"):
        topology.add_dependency("database", "database")


def test_circular_dependency_is_rejected(topology):
    with pytest.raises(ValueError, match="cycle"):
        topology.add_dependency("database", "frontend")


def test_export_topology(topology):
    result = topology.to_dict()

    assert "services" in result
    assert "dependencies" in result
    assert result["dependencies"]["frontend"] == ["payment-api"]
