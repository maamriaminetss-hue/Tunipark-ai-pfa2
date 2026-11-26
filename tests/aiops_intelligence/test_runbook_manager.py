
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "platform" / "aiops_intelligence" / "runbooks"),
)

from runbook_manager import RunbookManager


@pytest.fixture
def manager():
    manager = RunbookManager()

    manager.register_runbook({
        "runbook_id": "RB-001",
        "title": "Database latency investigation",
        "description": "Investigate database performance issues.",
        "incident_types": ["high_latency", "database_failure"],
        "services": ["database", "api-service"],
        "risk_level": "medium",
        "steps": [
            {
                "step_id": "STEP-01",
                "title": "Inspect database metrics",
                "description": "Review database latency and resource metrics.",
            },
            {
                "step_id": "STEP-02",
                "title": "Review database logs",
                "description": "Inspect recent database error logs.",
            },
        ],
    })

    manager.register_runbook({
        "runbook_id": "RB-002",
        "title": "Critical database recovery",
        "description": "Controlled recovery procedure.",
        "incident_types": ["database_failure"],
        "services": ["database"],
        "risk_level": "critical",
        "steps": [
            {
                "step_id": "STEP-01",
                "title": "Request approval",
                "description": "Obtain approval before proceeding.",
                "requires_approval": True,
            },
            {
                "step_id": "STEP-02",
                "title": "Review recovery plan",
                "description": "Review the proposed recovery plan.",
            },
        ],
    })

    return manager


def test_register_and_get_runbook(manager):
    runbook = manager.get_runbook("RB-001")

    assert runbook["title"] == "Database latency investigation"
    assert runbook["risk_level"] == "medium"
    assert len(runbook["steps"]) == 2


def test_duplicate_runbook_is_rejected(manager):
    with pytest.raises(ValueError, match="already exists"):
        manager.register_runbook({
            "runbook_id": "RB-001",
            "title": "Duplicate",
            "description": "Duplicate procedure",
            "incident_types": ["database_failure"],
            "risk_level": "low",
            "steps": [
                {
                    "step_id": "STEP-01",
                    "title": "Check",
                    "description": "Check the service.",
                }
            ],
        })


def test_invalid_risk_level_is_rejected(manager):
    with pytest.raises(ValueError, match="Invalid risk_level"):
        manager.register_runbook({
            "runbook_id": "RB-003",
            "title": "Invalid runbook",
            "description": "Invalid risk example",
            "incident_types": ["database_failure"],
            "risk_level": "extreme",
            "steps": [
                {
                    "step_id": "STEP-01",
                    "title": "Check",
                    "description": "Check the service.",
                }
            ],
        })


def test_runbook_requires_at_least_one_step(manager):
    with pytest.raises(ValueError, match="at least one step"):
        manager.register_runbook({
            "runbook_id": "RB-004",
            "title": "Empty runbook",
            "description": "No steps",
            "incident_types": ["database_failure"],
            "risk_level": "low",
            "steps": [],
        })


def test_duplicate_step_ids_are_rejected(manager):
    with pytest.raises(ValueError, match="Duplicate step_id"):
        manager.register_runbook({
            "runbook_id": "RB-005",
            "title": "Invalid steps",
            "description": "Duplicate step IDs",
            "incident_types": ["database_failure"],
            "risk_level": "low",
            "steps": [
                {
                    "step_id": "STEP-01",
                    "title": "First",
                    "description": "First step",
                },
                {
                    "step_id": "STEP-01",
                    "title": "Second",
                    "description": "Second step",
                },
            ],
        })


def test_filter_runbooks_by_incident_type(manager):
    results = manager.list_runbooks(
        incident_type="database_failure"
    )

    assert len(results) == 2


def test_filter_runbooks_by_service(manager):
    results = manager.list_runbooks(service_name="api-service")

    assert len(results) == 1
    assert results[0]["runbook_id"] == "RB-001"


def test_recommend_runbooks(manager):
    recommendations = manager.recommend_runbooks({
        "incident_type": "database_failure",
        "affected_services": ["database"],
    })

    assert len(recommendations) == 2
    assert recommendations[0]["match_score"] == 1.0
    assert recommendations[0]["runbook"]["runbook_id"] == "RB-002"


def test_no_recommendation_for_unrelated_incident(manager):
    recommendations = manager.recommend_runbooks({
        "incident_type": "network_failure",
        "affected_services": ["frontend"],
    })

    assert recommendations == []


def test_high_risk_execution_requires_approval(manager):
    execution = manager.create_execution("RB-002", "INC-001")

    assert execution["status"] == "pending_approval"
    assert execution["approved_by"] is None


def test_high_risk_execution_can_be_approved(manager):
    execution = manager.create_execution("RB-002", "INC-002")

    approved = manager.approve_execution(
        execution["execution_id"],
        "operations-lead",
    )

    assert approved["status"] == "ready"
    assert approved["approved_by"] == "operations-lead"


def test_approval_is_required_before_tracking(manager):
    execution = manager.create_execution("RB-002", "INC-003")

    with pytest.raises(ValueError, match="approved and active"):
        manager.update_step(
            execution["execution_id"],
            "STEP-01",
            "completed",
        )


def test_execution_steps_follow_declared_order(manager):
    execution = manager.create_execution("RB-001", "INC-004")
    execution_id = execution["execution_id"]

    with pytest.raises(ValueError, match="Previous steps"):
        manager.update_step(
            execution_id,
            "STEP-02",
            "completed",
        )

    manager.update_step(execution_id, "STEP-01", "completed")
    updated = manager.update_step(
        execution_id,
        "STEP-02",
        "completed",
    )

    assert updated["status"] == "completed"


def test_failed_step_marks_execution_failed(manager):
    execution = manager.create_execution("RB-001", "INC-005")

    updated = manager.update_step(
        execution["execution_id"],
        "STEP-01",
        "failed",
        note="Investigation could not be completed.",
    )

    assert updated["status"] == "failed"


def test_unknown_runbook_is_rejected(manager):
    with pytest.raises(ValueError, match="Unknown runbook"):
        manager.get_runbook("RB-999")


def test_unknown_execution_is_rejected(manager):
    with pytest.raises(ValueError, match="Unknown execution"):
        manager.get_execution("UNKNOWN")


def test_returned_runbook_does_not_modify_catalog(manager):
    runbook = manager.get_runbook("RB-001")
    runbook["title"] = "Modified externally"

    assert (
        manager.get_runbook("RB-001")["title"]
        == "Database latency investigation"
    )
