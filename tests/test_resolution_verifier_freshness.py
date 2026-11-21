from aiops_platform.intelligence.verification.resolution_verifier import ResolutionVerifier


def test_old_sample_inside_latest_three_makes_result_inconclusive():
    verifier = ResolutionVerifier(required_consecutive_samples=3, max_sample_age_seconds=180)
    observations = [
        {"service_name": "frontend", "metric_name": "latency", "timestamp": "2026-11-13T22:00:00Z", "value": 0.2},
        {"service_name": "frontend", "metric_name": "latency", "timestamp": "2026-11-13T22:04:00Z", "value": 0.2},
        {"service_name": "frontend", "metric_name": "latency", "timestamp": "2026-11-13T22:04:30Z", "value": 0.2},
    ]
    report = verifier.verify(
        "frontend", observations,
        [{"metric_name": "latency", "operator": "lte", "threshold": 0.5}],
        "2026-11-13T21:59:00Z",
        "2026-11-13T22:05:00Z",
    )
    assert report["status"] == "inconclusive"
    assert report["metrics"][0]["reason"] == "selected_observation_is_stale"


def test_three_fresh_samples_can_resolve():
    verifier = ResolutionVerifier(required_consecutive_samples=3, max_sample_age_seconds=180)
    observations = [
        {"service_name": "frontend", "metric_name": "latency", "timestamp": "2026-11-13T22:04:00Z", "value": 0.2},
        {"service_name": "frontend", "metric_name": "latency", "timestamp": "2026-11-13T22:04:30Z", "value": 0.3},
        {"service_name": "frontend", "metric_name": "latency", "timestamp": "2026-11-13T22:05:00Z", "value": 0.4},
    ]
    report = verifier.verify(
        "frontend", observations,
        [{"metric_name": "latency", "operator": "lte", "threshold": 0.5}],
        "2026-11-13T22:03:00Z",
        "2026-11-13T22:05:30Z",
    )
    assert report["status"] == "resolved"
