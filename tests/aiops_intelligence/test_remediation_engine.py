
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "platform" / "aiops_intelligence" / "remediation"),
)

from remediation_engine import RemediationEngine


@pytest.fixture
def engine():
    engine = RemediationEngine()

    engine.register_action({
        "action_id": "ACT-001",
        "title": "Inspect database performance",
        "description": "Review database metrics and logs.",
        "incident_types": ["database_failure", "high_latency"],
        "services": ["database", "api-service"],
        "risk_level": "low",
    })

    engine.register_action({
        "action_id": "ACT-002",
        "title": "Request database recovery approval",
        "description": "Prepare a recovery plan for human review.",
        "incident_types": ["database_failure"],
        "services": ["database"],
        "risk_level": "critical",
        "requires_approval": True,
    })

    engine.register_action({
        "action_id": "ACT-003",
        "title": "Review API configuration",
        "description": "Review API configuration and recent changes.",
        "incident_types": ["configuration_error"],
        "services": ["api-service"],
        "risk_level": "medium",
    })

    return engine


@pytest.fixture
def database_incident():
    return {
        "incident_id": "INC-001",
        "incident_type": "database_failure",
        "affected_services": ["database"],
    }


def test_register_and_get_action(engine):
    action = engine.get_action("ACT-001")

    assert action["title"] == "Inspect database performance"
    assert action["risk_level"] == "low"


def test_duplicate_action_is_rejected(engine):
    with pytest.raises(ValueError, match="already exists"):
        engine.register_action({
            "action_id": "ACT-001",
            "title": "Duplicate action",
            "description": "Duplicate",
            "incident_types": ["database_failure"],
            "risk_level": "low",
        })


def test_invalid_risk_level_is_rejected(engine):
    with pytest.raises(ValueError, match="Invalid risk_level"):
        engine.register_action({
            "action_id": "ACT-004",
            "title": "Invalid action",
            "description": "Invalid risk",
            "incident_types": ["database_failure"],
            "risk_level": "extreme",
        })


def test_invalid_approval_flag_is_rejected(engine):
    with pytest.raises(ValueError, match="requires_approval"):
        engine.register_action({
            "action_id": "ACT-005",
            "title": "Invalid approval",
            "description": "Invalid approval flag",
            "incident_types": ["database_failure"],
            "risk_level": "low",
            "requires_approval": "yes",
        })


def test_filter_actions_by_incident_type(engine):
    results = engine.list_actions(
        incident_type="database_failure"
    )

    assert len(results) == 2


def test_filter_actions_by_service(engine):
    results = engine.list_actions(service_name="api-service")

    assert len(results) == 2


def test_recommend_actions(engine, database_incident):
    recommendations = engine.recommend_actions(database_incident)

    assert len(recommendations) == 2
    assert recommendations[0]["match_score"] == 1.0


def test_unrelated_incident_has_no_recommendations(engine):
    recommendations = engine.recommend_actions({
        "incident_type": "network_failure",
        "affected_services": ["frontend"],
    })

    assert recommendations == []


def test_create_plan_from_recommendations(engine, database_incident):
    plan = engine.create_plan(database_incident)

    assert plan["incident_id"] == "INC-001"
    assert len(plan["actions"]) == 2
    assert plan["status"] == "pending_approval"


def test_critical_action_requires_approval(engine, database_incident):
    plan = engine.create_plan(
        database_incident,
        action_ids=["ACT-002"],
    )

    assert plan["status"] == "pending_approval"
    assert plan["approved_by"] is None


def test_low_risk_plan_is_ready(engine):
    plan = engine.create_plan(
        {
            "incident_id": "INC-002",
            "incident_type": "database_failure",
            "affected_services": ["database"],
        },
        action_ids=["ACT-001"],
    )

    assert plan["status"] == "ready"


def test_approve_plan(engine, database_incident):
    plan = engine.create_plan(
        database_incident,
        action_ids=["ACT-002"],
    )

    approved = engine.approve_plan(
        plan["plan_id"],
        "operations-lead",
    )

    assert approved["status"] == "ready"
    assert approved["approved_by"] == "operations-lead"


def test_approval_required_before_action_tracking(
    engine,
    database_incident,
):
    plan = engine.create_plan(
        database_incident,
        action_ids=["ACT-002"],
    )

    with pytest.raises(ValueError, match="approved and active"):
        engine.update_action(
            plan["plan_id"],
            "ACT-002",
            "completed",
        )



def test_actions_must_follow_plan_order(engine, database_incident):
    plan = engine.create_plan(
        database_incident,
        action_ids=["ACT-001", "ACT-002"],
    )

    # The critical action requires approval for the entire plan.
    engine.approve_plan(
        plan["plan_id"],
        "operations-lead",
    )

    # The second action cannot be updated before the first one.
    with pytest.raises(ValueError, match="Previous actions"):
        engine.update_action(
            plan["plan_id"],
            "ACT-002",
            "completed",
        )



def test_completed_actions_complete_plan(engine):
    plan = engine.create_plan(
        {
            "incident_id": "INC-003",
            "incident_type": "database_failure",
            "affected_services": ["database"],
        },
        action_ids=["ACT-001"],
    )

    updated = engine.update_action(
        plan["plan_id"],
        "ACT-001",
        "completed",
        note="Review completed.",
    )

    assert updated["status"] == "completed"
    assert updated["actions"][0]["status"] == "completed"


def test_failed_action_fails_plan(engine):
    plan = engine.create_plan(
        {
            "incident_id": "INC-004",
            "incident_type": "database_failure",
            "affected_services": ["database"],
        },
        action_ids=["ACT-001"],
    )

    updated = engine.update_action(
        plan["plan_id"],
        "ACT-001",
        "failed",
        note="Action could not be completed.",
    )

    assert updated["status"] == "failed"


def test_unknown_action_is_rejected(engine):
    with pytest.raises(ValueError, match="Unknown action"):
        engine.get_action("ACT-999")


def test_unknown_plan_is_rejected(engine):
    with pytest.raises(ValueError, match="Unknown remediation plan"):
        engine.get_plan("UNKNOWN")


def test_plan_copy_does_not_modify_internal_state(
    engine,
    database_incident,
):
    plan = engine.create_plan(
        database_incident,
        action_ids=["ACT-001"],
    )

    plan["actions"][0]["title"] = "Modified externally"

    stored = engine.get_plan(plan["plan_id"])

    assert stored["actions"][0]["title"] == "Inspect database performance"
