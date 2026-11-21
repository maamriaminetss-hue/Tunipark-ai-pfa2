from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aiops_platform.database.models import Incident, Service
from aiops_platform.intelligence.verification.prometheus_resolution_reader import (
    PrometheusReaderError,
)
from aiops_platform.intelligence.workflows import RunbookRemediationWorkflow


class FakeDB:
    def __init__(self, incident, service):
        self.incident = incident
        self.service = service
        self.audit_logs = []
        self.commits = 0
        self.rollbacks = 0

    def get(self, model, identifier):
        if model is Incident:
            return self.incident if identifier == self.incident.id else None
        if model is Service:
            return self.service if identifier == self.service.id else None
        raise AssertionError(f"Unexpected model: {model}")

    def add(self, item):
        self.audit_logs.append(item)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def refresh(self, item):
        pass

    def rollback(self):
        self.rollbacks += 1


class StaticReader:
    def __init__(self, observations=None, error=None):
        self.observations = observations or []
        self.error = error
        self.calls = []

    def fetch_observations(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.observations


def make_completed_workflow():
    incident_id = uuid4()
    workflow = RunbookRemediationWorkflow()
    workflow.register_runbook({
        "runbook_id": "RB-FRONTEND-LOW",
        "title": "Check frontend recovery",
        "description": "Review frontend service health.",
        "incident_types": ["high_latency"],
        "services": ["frontend"],
        "risk_level": "low",
        "steps": [{
            "step_id": "STEP-1",
            "title": "Review metrics",
            "description": "Review post-remediation service metrics.",
        }],
    })
    plan = workflow.create_plan({
        "incident_id": str(incident_id),
        "incident_type": "high_latency",
        "affected_services": ["frontend"],
    }, ["RB-FRONTEND-LOW"])
    completed_plan = workflow.update_action(
        plan["plan_id"],
        "RB-FRONTEND-LOW",
        "completed",
        note="Manual action reported complete; no command executed by this test.",
    )

    service = SimpleNamespace(id=uuid4(), name="frontend")
    incident = SimpleNamespace(
        id=incident_id,
        service_id=service.id,
        status="open",
        resolved_at=None,
    )
    return workflow, completed_plan, FakeDB(incident, service)


def make_observations(completed_at, values):
    start = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    return [
        {
            "service_name": "frontend",
            "metric_name": "http_p95_latency_seconds",
            "timestamp": (
                start + timedelta(seconds=offset)
            ).isoformat().replace("+00:00", "Z"),
            "value": value,
            "labels": {"service_name": "frontend"},
        }
        for offset, value in enumerate(values, start=1)
    ]


CHECKS = [{
    "metric_name": "http_p95_latency_seconds",
    "operator": "lte",
    "threshold": 2.0,
}]


def test_resolution_verification_resolves_incident_and_audits_report():
    workflow, plan, db = make_completed_workflow()
    observations = make_observations(
        plan["remediation_completed_at"],
        [1.4, 1.3, 1.2],
    )
    reader = StaticReader(observations)
    completed_at = datetime.fromisoformat(
        plan["remediation_completed_at"].replace("Z", "+00:00")
    )
    verified_at = completed_at + timedelta(seconds=10)

    result = workflow.verify_resolution(
        plan["plan_id"],
        db,
        CHECKS,
        "test-operator",
        prometheus_reader=reader,
        verified_at=verified_at,
    )

    assert result["status"] == "resolved"
    assert result["incident_status"] == "resolved"
    assert db.incident.status == "resolved"
    assert db.incident.resolved_at is not None
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(db.audit_logs) == 1

    audit = db.audit_logs[0]
    assert audit.action == "incident.resolution_verification"
    assert audit.resource_id == db.incident.id
    assert audit.details["plan_id"] == plan["plan_id"]
    assert audit.details["verification_report"]["status"] == "resolved"
    assert audit.details["incident_status_before"] == "open"
    assert audit.details["incident_status_after"] == "resolved"

    assert len(reader.calls) == 1
    assert reader.calls[0]["service_name"] == "frontend"
    assert reader.calls[0]["start"] == plan["remediation_completed_at"]
    assert set(reader.calls[0]["queries"]) == {
        "http_p95_latency_seconds"
    }


@pytest.mark.parametrize(
    ("values", "expected_status"),
    [
        ([3.0, 3.1, 3.2], "failed"),
        ([], "inconclusive"),
    ],
)
def test_failed_or_inconclusive_verification_keeps_incident_open(
    values,
    expected_status,
):
    workflow, plan, db = make_completed_workflow()
    reader = StaticReader(
        make_observations(plan["remediation_completed_at"], values)
    )
    completed_at = datetime.fromisoformat(
        plan["remediation_completed_at"].replace("Z", "+00:00")
    )

    result = workflow.verify_resolution(
        plan["plan_id"],
        db,
        CHECKS,
        "test-operator",
        prometheus_reader=reader,
        verified_at=completed_at + timedelta(seconds=10),
    )

    assert result["status"] == expected_status
    assert result["incident_status"] == "open"
    assert db.incident.status == "open"
    assert db.incident.resolved_at is None
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(db.audit_logs) == 1
    assert (
        db.audit_logs[0].details["verification_report"]["status"]
        == expected_status
    )


def test_prometheus_failure_is_audited_as_inconclusive():
    workflow, plan, db = make_completed_workflow()
    reader = StaticReader(
        error=PrometheusReaderError("Prometheus is unavailable")
    )
    completed_at = datetime.fromisoformat(
        plan["remediation_completed_at"].replace("Z", "+00:00")
    )

    result = workflow.verify_resolution(
        plan["plan_id"],
        db,
        CHECKS,
        "test-operator",
        prometheus_reader=reader,
        verified_at=completed_at + timedelta(seconds=10),
    )

    assert result["status"] == "inconclusive"
    assert result["incident_status"] == "open"
    assert any(
        "prometheus_query_failed" in warning
        for warning in result["warnings"]
    )
    assert db.commits == 1
    assert len(db.audit_logs) == 1


def test_verification_rejects_plan_that_is_not_completed():
    workflow, plan, db = make_completed_workflow()
    # A fresh workflow with the same catalogue but an uncompleted plan.
    pending = workflow.create_plan({
        "incident_id": str(db.incident.id),
        "incident_type": "high_latency",
        "affected_services": ["frontend"],
    }, ["RB-FRONTEND-LOW"])

    with pytest.raises(ValueError, match="must be completed"):
        workflow.verify_resolution(
            pending["plan_id"],
            db,
            CHECKS,
            "test-operator",
            prometheus_reader=StaticReader(),
        )

    assert db.commits == 0
    assert db.audit_logs == []
