
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "platform" / "aiops_intelligence" / "rca"),
)

sys.path.insert(
    0,
    str(ROOT / "platform" / "aiops_intelligence" / "topology"),
)

from service_topology import ServiceTopology
from root_cause_analyzer import RootCauseAnalyzer


@pytest.fixture
def analyzer():
    topology = ServiceTopology()

    topology.add_service("frontend")
    topology.add_service("api-service")
    topology.add_service("database")
    topology.add_service("notification-service")

    topology.add_dependency("frontend", "api-service")
    topology.add_dependency("api-service", "database")
    topology.add_dependency("notification-service", "database")

    return RootCauseAnalyzer(topology)


@pytest.fixture
def sample_incident():
    return {
        "incident_id": "INC-001",
        "events": [
            {
                "event_id": "EVT-001",
                "service_name": "database",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 0.95,
                "reason": "High database latency",
            },
            {
                "event_id": "EVT-002",
                "service_name": "api-service",
                "occurred_at": "2026-11-13T10:02:00",
                "severity": 0.75,
                "reason": "API response time increased",
            },
            {
                "event_id": "EVT-003",
                "service_name": "frontend",
                "occurred_at": "2026-11-13T10:04:00",
                "severity": 0.60,
                "reason": "Frontend timeout",
            },
        ],
    }


def test_analyze_incident(analyzer, sample_incident):
    report = analyzer.analyze(sample_incident)

    assert report["incident_id"] == "INC-001"
    assert report["analysis_status"] == "completed"
    assert report["events_analyzed"] == 3


def test_database_is_top_candidate(analyzer, sample_incident):
    report = analyzer.analyze(sample_incident)

    assert report["top_candidate"] == "database"


def test_candidates_are_ranked(analyzer, sample_incident):
    report = analyzer.analyze(sample_incident)

    ranks = [candidate["rank"] for candidate in report["candidates"]]

    assert ranks == [1, 2, 3]


def test_database_has_downstream_evidence(analyzer, sample_incident):
    report = analyzer.analyze(sample_incident)

    database = report["candidates"][0]

    assert database["service_name"] == "database"
    assert database["affected_services"] == [
        "api-service",
        "frontend",
    ]


def test_empty_events_are_rejected(analyzer):
    with pytest.raises(ValueError, match="non-empty events"):
        analyzer.analyze({"incident_id": "INC-002", "events": []})


def test_unknown_service_is_rejected(analyzer):
    incident = {
        "incident_id": "INC-003",
        "events": [
            {
                "service_name": "unknown-service",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 0.8,
            }
        ],
    }

    with pytest.raises(ValueError, match="Unknown service"):
        analyzer.analyze(incident)


def test_invalid_severity_is_rejected(analyzer):
    incident = {
        "incident_id": "INC-004",
        "events": [
            {
                "service_name": "database",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 1.5,
            }
        ],
    }

    with pytest.raises(ValueError, match="between 0 and 1"):
        analyzer.analyze(incident)


def test_invalid_timestamp_is_rejected(analyzer):
    incident = {
        "incident_id": "INC-005",
        "events": [
            {
                "service_name": "database",
                "occurred_at": "invalid-date",
                "severity": 0.8,
            }
        ],
    }

    with pytest.raises(ValueError, match="Invalid event timestamp"):
        analyzer.analyze(incident)


def test_single_service_incident(analyzer):
    incident = {
        "incident_id": "INC-006",
        "events": [
            {
                "service_name": "database",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 0.9,
            }
        ],
    }

    report = analyzer.analyze(incident)

    assert report["top_candidate"] == "database"
    assert len(report["candidates"]) == 1
def test_simultaneous_events_have_equal_temporal_scores(analyzer):
    incident = {
        "incident_id": "INC-007",
        "events": [
            {
                "service_name": "database",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 0.9,
            },
            {
                "service_name": "api-service",
                "occurred_at": "2026-11-13T10:00:00",
                "severity": 0.7,
            },
        ],
    }

    report = analyzer.analyze(incident)

    scores = {
        candidate["service_name"]: candidate["temporal_score"]
        for candidate in report["candidates"]
    }

    assert scores["database"] == 0.5
    assert scores["api-service"] == 0.5