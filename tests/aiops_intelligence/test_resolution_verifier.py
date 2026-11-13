
import sys
from pathlib import Path

import pytest

# Add the verification module directory to Python's import path.
ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(
        ROOT
        / "platform"
        / "aiops_intelligence"
        / "verification"
    ),
)

from resolution_verifier import ResolutionVerifier


@pytest.fixture
def verifier():
    return ResolutionVerifier()


@pytest.fixture
def criteria():
    return [
        {"metric": "cpu_usage", "operator": "<=", "threshold": 70},
        {"metric": "error_rate", "operator": "<=", "threshold": 1},
        {"metric": "response_time", "operator": "<=", "threshold": 300},
    ]


@pytest.fixture
def healthy_metrics():
    return {
        "cpu_usage": 55,
        "error_rate": 0.2,
        "response_time": 180,
    }


def test_all_criteria_pass(verifier, criteria, healthy_metrics):
    report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
        active_incident=False,
    )

    assert report["status"] == "verified"
    assert report["total_checks"] == 3
    assert report["passed_checks"] == 3
    assert report["failed_checks"] == 0


def test_failed_metric_means_unresolved(
    verifier, criteria, healthy_metrics
):
    healthy_metrics["cpu_usage"] = 95

    report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
        active_incident=False,
    )

    assert report["status"] == "unresolved"
    assert report["failed_checks"] == 1


def test_active_incident_means_unresolved(
    verifier, criteria, healthy_metrics
):
    report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
        active_incident=True,
    )

    assert report["status"] == "unresolved"


def test_unknown_incident_state_is_inconclusive(
    verifier, criteria, healthy_metrics
):
    report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
    )

    assert report["status"] == "inconclusive"


def test_missing_metric_is_inconclusive(verifier, criteria):
    metrics = {
        "cpu_usage": 55,
        "error_rate": 0.2,
    }

    report = verifier.verify(
        "INC-001",
        metrics,
        criteria,
        active_incident=False,
    )

    assert report["status"] == "inconclusive"
    assert report["missing_checks"] == 1


def test_failure_takes_priority_over_missing_data(
    verifier, criteria
):
    metrics = {
        "cpu_usage": 95,
    }

    report = verifier.verify(
        "INC-001",
        metrics,
        criteria,
        active_incident=False,
    )

    assert report["status"] == "unresolved"
    assert report["failed_checks"] == 1
    assert report["missing_checks"] == 2


def test_before_metrics_are_included(
    verifier, criteria, healthy_metrics
):
    before = {
        "cpu_usage": 95,
        "error_rate": 8,
        "response_time": 900,
    }

    report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
        active_incident=False,
        before_metrics=before,
    )

    assert report["status"] == "verified"
    assert report["checks"][0]["before"] == 95


@pytest.mark.parametrize(
    ("operator", "observed", "threshold", "expected"),
    [
        ("<", 5, 10, True),
        ("<=", 10, 10, True),
        (">", 15, 10, True),
        (">=", 10, 10, True),
        ("==", 10, 10, True),
        ("!=", 5, 10, True),
        ("<", 15, 10, False),
    ],
)
def test_comparison_operators(
    verifier, operator, observed, threshold, expected
):
    report = verifier.verify(
        "INC-001",
        {"metric": observed},
        [
            {
                "metric": "metric",
                "operator": operator,
                "threshold": threshold,
            }
        ],
        active_incident=False,
    )

    assert report["checks"][0]["passed"] is expected


def test_empty_incident_id_is_rejected(verifier, criteria):
    with pytest.raises(ValueError, match="incident_id"):
        verifier.verify("", {}, criteria)


def test_empty_criteria_are_rejected(verifier):
    with pytest.raises(ValueError, match="criterion"):
        verifier.verify("INC-001", {}, [])


def test_invalid_operator_is_rejected(verifier):
    with pytest.raises(ValueError, match="Unsupported operator"):
        verifier.verify(
            "INC-001",
            {"cpu_usage": 50},
            [
                {
                    "metric": "cpu_usage",
                    "operator": "~",
                    "threshold": 70,
                }
            ],
            active_incident=False,
        )


def test_non_numeric_metric_is_rejected(verifier, criteria):
    with pytest.raises(ValueError, match="finite number"):
        verifier.verify(
            "INC-001",
            {"cpu_usage": "high"},
            criteria,
            active_incident=False,
        )


def test_boolean_metric_is_rejected(verifier):
    with pytest.raises(ValueError, match="finite number"):
        verifier.verify(
            "INC-001",
            {"cpu_usage": True},
            [
                {
                    "metric": "cpu_usage",
                    "operator": "<=",
                    "threshold": 70,
                }
            ],
            active_incident=False,
        )


def test_duplicate_metric_criteria_are_rejected(verifier):
    duplicate_criteria = [
        {
            "metric": "cpu_usage",
            "operator": "<=",
            "threshold": 70,
        },
        {
            "metric": "cpu_usage",
            "operator": "<=",
            "threshold": 80,
        },
    ]

    with pytest.raises(ValueError, match="Duplicate criterion"):
        verifier.verify(
            "INC-001",
            {"cpu_usage": 50},
            duplicate_criteria,
            active_incident=False,
        )


def test_invalid_threshold_is_rejected(verifier):
    with pytest.raises(ValueError, match="finite number"):
        verifier.verify(
            "INC-001",
            {"cpu_usage": 50},
            [
                {
                    "metric": "cpu_usage",
                    "operator": "<=",
                    "threshold": float("nan"),
                }
            ],
            active_incident=False,
        )


def test_report_changes_do_not_affect_future_results(
    verifier, criteria, healthy_metrics
):
    first_report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
        active_incident=False,
    )

    first_report["checks"][0]["status"] = "modified"

    second_report = verifier.verify(
        "INC-001",
        healthy_metrics,
        criteria,
        active_incident=False,
    )

    assert second_report["checks"][0]["status"] == "passed"
