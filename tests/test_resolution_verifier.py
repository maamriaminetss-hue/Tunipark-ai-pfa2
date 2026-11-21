from datetime import datetime, timedelta, timezone

import pytest

from aiops_platform.intelligence.verification import ResolutionVerifier


BASE = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


def observations(metric="request_latency", values=(0.2, 0.3, 0.25), service="payment"):
    return [
        {
            "service_name": service,
            "metric_name": metric,
            "timestamp": (BASE + timedelta(seconds=i * 30)).isoformat().replace("+00:00", "Z"),
            "value": value,
        }
        for i, value in enumerate(values)
    ]


def check(metric="request_latency", operator="lte", threshold=0.5):
    return {"metric_name": metric, "operator": operator, "threshold": threshold}


def test_resolved_after_required_consecutive_healthy_samples():
    verifier = ResolutionVerifier(required_consecutive_samples=3)
    report = verifier.verify(
        "payment", observations(), [check()], BASE,
        verified_at=BASE + timedelta(seconds=90),
    )
    assert report["status"] == "resolved"
    assert report["metrics"][0]["status"] == "resolved"


def test_failed_when_all_latest_samples_violate_threshold():
    verifier = ResolutionVerifier(required_consecutive_samples=3)
    report = verifier.verify(
        "payment", observations(values=(0.8, 0.9, 1.0)), [check()], BASE,
        verified_at=BASE + timedelta(seconds=90),
    )
    assert report["status"] == "failed"


def test_mixed_latest_samples_are_inconclusive():
    verifier = ResolutionVerifier(required_consecutive_samples=3)
    report = verifier.verify(
        "payment", observations(values=(0.2, 0.9, 0.2)), [check()], BASE,
        verified_at=BASE + timedelta(seconds=90),
    )
    assert report["status"] == "inconclusive"
    assert report["metrics"][0]["reason"] == "metric_state_is_not_stable"


def test_insufficient_samples_are_inconclusive():
    verifier = ResolutionVerifier(required_consecutive_samples=3)
    report = verifier.verify(
        "payment", observations(values=(0.2, 0.3)), [check()], BASE,
        verified_at=BASE + timedelta(seconds=60),
    )
    assert report["status"] == "inconclusive"
    assert report["metrics"][0]["reason"] == "insufficient_consecutive_observations"


def test_pre_remediation_samples_are_not_used():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = observations(values=(0.2, 0.2, 0.2))
    completed = BASE + timedelta(seconds=45)
    report = verifier.verify(
        "payment", rows, [check()], completed,
        verified_at=BASE + timedelta(seconds=90),
    )
    assert report["status"] == "inconclusive"
    assert report["metrics"][0]["samples_considered"] == 1


def test_stale_latest_sample_is_inconclusive():
    verifier = ResolutionVerifier(required_consecutive_samples=2, max_sample_age_seconds=30)
    report = verifier.verify(
        "payment", observations(values=(0.2, 0.2)), [check()], BASE,
        verified_at=BASE + timedelta(minutes=5),
    )
    assert report["status"] == "inconclusive"
    assert report["metrics"][0]["reason"] == "latest_observation_is_stale"


def test_other_services_are_ignored():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = observations(values=(0.9, 0.9), service="checkout")
    report = verifier.verify(
        "payment", rows, [check()], BASE,
        verified_at=BASE + timedelta(seconds=60),
    )
    assert report["status"] == "inconclusive"
    assert report["metrics"][0]["reason"] == "no_post_remediation_observations"


def test_invalid_observation_is_skipped_with_warning():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = observations(values=(0.2, 0.2))
    rows.append({
        "service_name": "payment", "metric_name": "request_latency",
        "timestamp": "not-a-date", "value": 0.1,
    })
    report = verifier.verify(
        "payment", rows, [check()], BASE,
        verified_at=BASE + timedelta(seconds=60),
    )
    assert report["status"] == "resolved"
    assert len(report["warnings"]) == 1


def test_naive_timestamps_are_treated_as_utc():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = [
        {"service_name": "payment", "metric_name": "request_latency",
         "timestamp": (BASE + timedelta(seconds=i * 30)).replace(tzinfo=None).isoformat(),
         "value": 0.2}
        for i in range(2)
    ]
    report = verifier.verify(
        "payment", rows, [check()], BASE.replace(tzinfo=None),
        verified_at=(BASE + timedelta(seconds=60)).replace(tzinfo=None),
    )
    assert report["status"] == "resolved"


def test_threshold_boundary_is_accepted_for_lte():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    report = verifier.verify(
        "payment", observations(values=(0.5, 0.5)), [check(threshold=0.5)], BASE,
        verified_at=BASE + timedelta(seconds=60),
    )
    assert report["status"] == "resolved"


def test_one_failed_required_metric_fails_overall_result():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = observations(values=(0.2, 0.2)) + observations(
        metric="error_rate", values=(0.1, 0.2)
    )
    checks = [check(), check(metric="error_rate", threshold=0.01)]
    report = verifier.verify(
        "payment", rows, checks, BASE,
        verified_at=BASE + timedelta(seconds=60),
    )
    assert report["status"] == "failed"
    assert [metric["status"] for metric in report["metrics"]] == ["resolved", "failed"]


def test_all_required_metrics_must_resolve():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = observations(values=(0.2, 0.2)) + observations(
        metric="error_rate", values=(0.001, 0.002)
    )
    checks = [check(), check(metric="error_rate", threshold=0.01)]
    report = verifier.verify(
        "payment", rows, checks, BASE,
        verified_at=BASE + timedelta(seconds=60),
    )
    assert report["status"] == "resolved"


def test_future_observations_are_ignored():
    verifier = ResolutionVerifier(required_consecutive_samples=2)
    rows = observations(values=(0.2, 0.2, 0.2))
    report = verifier.verify(
        "payment", rows, [check()], BASE,
        verified_at=BASE + timedelta(seconds=15),
    )
    assert report["status"] == "inconclusive"
    assert report["warnings"]


def test_duplicate_timestamps_do_not_count_as_extra_samples():
    verifier = ResolutionVerifier(required_consecutive_samples=3)
    rows = observations(values=(0.2, 0.2, 0.2))
    rows.append(dict(rows[-1]))
    report = verifier.verify(
        "payment", rows, [check()], BASE,
        verified_at=BASE + timedelta(seconds=90),
    )
    assert report["status"] == "resolved"
    assert report["metrics"][0]["samples_considered"] == 3
    assert any("Duplicate timestamp" in warning for warning in report["warnings"])


@pytest.mark.parametrize("kwargs", [
    {"required_consecutive_samples": 0},
    {"required_consecutive_samples": True},
    {"max_sample_age_seconds": 0},
    {"max_sample_age_seconds": float("inf")},
])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ResolutionVerifier(**kwargs)


def test_invalid_check_is_rejected():
    verifier = ResolutionVerifier()
    with pytest.raises(ValueError, match="Invalid operator"):
        verifier.verify("payment", [], [check(operator="approx")], BASE)


def test_verified_at_cannot_precede_remediation_completion():
    verifier = ResolutionVerifier()
    with pytest.raises(ValueError, match="earlier"):
        verifier.verify(
            "payment", [], [check()], BASE,
            verified_at=BASE - timedelta(seconds=1),
        )
