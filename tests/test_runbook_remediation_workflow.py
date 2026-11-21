import pytest

from aiops_platform.intelligence.workflows import RunbookRemediationWorkflow


@pytest.fixture
def workflow():
    instance = RunbookRemediationWorkflow()
    instance.register_runbook({
        "runbook_id": "RB-DB-LOW",
        "title": "Inspect database latency",
        "description": "Collect database health and latency evidence.",
        "incident_types": ["database_failure", "high_latency"],
        "services": ["database", "api-service"],
        "risk_level": "low",
        "steps": [
            {
                "step_id": "STEP-1",
                "title": "Inspect metrics",
                "description": "Review database latency and saturation.",
            },
            {
                "step_id": "STEP-2",
                "title": "Inspect logs",
                "description": "Review recent database errors.",
            },
        ],
    })
    instance.register_runbook({
        "runbook_id": "RB-DB-CRITICAL",
        "title": "Prepare database recovery",
        "description": "Prepare a controlled database recovery plan.",
        "incident_types": ["database_failure"],
        "services": ["database"],
        "risk_level": "critical",
        "steps": [
            {
                "step_id": "STEP-1",
                "title": "Obtain approval",
                "description": "Obtain approval before recovery.",
                "requires_approval": True,
            },
        ],
    })
    return instance


@pytest.fixture
def incident():
    return {
        "incident_id": "INC-001",
        "incident_type": "database_failure",
        "affected_services": ["database"],
    }


def test_runbook_catalog_is_source_for_recommendations(workflow, incident):
    recommendations = workflow.recommend_runbooks(incident)
    assert [item["runbook"]["runbook_id"] for item in recommendations] == [
        "RB-DB-CRITICAL",
        "RB-DB-LOW",
    ]


def test_plan_contains_canonical_runbook_snapshot(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-LOW"])
    action = plan["actions"][0]

    assert action["runbook_id"] == "RB-DB-LOW"
    assert action["runbook"]["title"] == "Inspect database latency"
    assert len(action["runbook"]["steps"]) == 2
    assert action["status"] == "pending"
    assert plan["remediation_completed_at"] is None


def test_critical_runbook_requires_approval(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-CRITICAL"])
    assert plan["status"] == "pending_approval"
    assert plan["approved_by"] is None


def test_step_approval_requirement_is_projected(workflow, incident):
    workflow.register_runbook({
        "runbook_id": "RB-DB-MEDIUM-APPROVAL",
        "title": "Review failover configuration",
        "description": "Review a controlled failover configuration.",
        "incident_types": ["database_failure"],
        "services": ["database"],
        "risk_level": "medium",
        "steps": [{
            "step_id": "STEP-1",
            "title": "Approve review",
            "description": "Obtain operator approval.",
            "requires_approval": True,
        }],
    })
    plan = workflow.create_plan(incident, ["RB-DB-MEDIUM-APPROVAL"])
    assert plan["status"] == "pending_approval"


def test_approval_enables_plan_tracking(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-CRITICAL"])
    approved = workflow.approve_plan(plan["plan_id"], "operations-lead")

    assert approved["status"] == "ready"
    assert approved["approved_by"] == "operations-lead"


def test_unapproved_plan_cannot_be_updated(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-CRITICAL"])
    with pytest.raises(ValueError, match="approved and active"):
        workflow.update_action(
            plan["plan_id"], "RB-DB-CRITICAL", "completed"
        )


def test_plan_actions_follow_selected_order(workflow, incident):
    plan = workflow.create_plan(
        incident,
        ["RB-DB-LOW", "RB-DB-CRITICAL"],
    )
    workflow.approve_plan(plan["plan_id"], "operations-lead")

    with pytest.raises(ValueError, match="Previous actions"):
        workflow.update_action(
            plan["plan_id"], "RB-DB-CRITICAL", "completed"
        )


def test_completed_runbook_completes_single_action_plan(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-LOW"])
    updated = workflow.update_action(
        plan["plan_id"],
        "RB-DB-LOW",
        "completed",
        note="Manual procedure reported complete.",
    )

    assert updated["status"] == "completed"
    assert updated["actions"][0]["status"] == "completed"
    assert updated["remediation_completed_at"] is not None
    assert updated["remediation_completed_at"].endswith("+00:00")


def test_failed_runbook_fails_plan(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-LOW"])
    updated = workflow.update_action(
        plan["plan_id"], "RB-DB-LOW", "failed", note="Procedure failed."
    )
    assert updated["status"] == "failed"


def test_irrelevant_runbook_cannot_be_forced_into_plan(workflow):
    unrelated = {
        "incident_id": "INC-002",
        "incident_type": "network_failure",
        "affected_services": ["frontend"],
    }
    with pytest.raises(ValueError, match="not recommended"):
        workflow.create_plan(unrelated, ["RB-DB-LOW"])


def test_incident_without_matching_runbook_has_no_plan(workflow):
    unrelated = {
        "incident_id": "INC-003",
        "incident_type": "network_failure",
        "affected_services": ["frontend"],
    }
    with pytest.raises(ValueError, match="No runbooks match"):
        workflow.create_plan(unrelated)


def test_duplicate_selected_runbook_is_rejected(workflow, incident):
    with pytest.raises(ValueError, match="Duplicate runbook_id"):
        workflow.create_plan(
            incident,
            ["RB-DB-LOW", "RB-DB-LOW"],
        )


def test_plan_returns_defensive_runbook_snapshot(workflow, incident):
    plan = workflow.create_plan(incident, ["RB-DB-LOW"])
    plan["actions"][0]["runbook"]["title"] = "External mutation"

    stored = workflow.get_plan(plan["plan_id"])
    assert stored["actions"][0]["runbook"]["title"] == "Inspect database latency"
    assert workflow.get_runbook("RB-DB-LOW")["title"] == "Inspect database latency"


def test_approve_unknown_plan_is_rejected(workflow):
    with pytest.raises(ValueError, match="Unknown remediation plan"):
        workflow.approve_plan("UNKNOWN", "operator")


def test_duplicate_runbook_registration_is_rejected(workflow):
    with pytest.raises(ValueError, match="already exists"):
        workflow.register_runbook({
            "runbook_id": "RB-DB-LOW",
            "title": "Duplicate",
            "description": "Duplicate runbook.",
            "incident_types": ["database_failure"],
            "services": ["database"],
            "risk_level": "low",
            "steps": [{
                "step_id": "STEP-1",
                "title": "Check",
                "description": "Check the database.",
            }],
        })
