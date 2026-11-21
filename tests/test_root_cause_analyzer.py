import pytest

from aiops_platform.intelligence.rca import RootCauseAnalyzer
from aiops_platform.intelligence.topology import ServiceTopology


def make_topology():
    graph = ServiceTopology()
    for name in ("frontend", "recommendation", "catalog", "database", "payment"):
        graph.add_service(name)
    graph.add_dependency("frontend", "recommendation")
    graph.add_dependency("recommendation", "catalog")
    graph.add_dependency("catalog", "database")
    graph.add_dependency("payment", "database")
    return graph


def event(service, timestamp, *, method="mad", reason="deviation_above_mad_limit", score=5.0):
    return {
        "event_id": f"{service}-{timestamp}",
        "occurred_at": timestamp,
        "dimensions": {"service_name": service, "metric_name": "request_rate"},
        "methods": [method],
        "reasons": [reason],
        "scores": {method: score},
    }


def by_name(result, service):
    return next(
        item for item in result["ranked_candidates"]
        if item["service_name"] == service
    )


def names(result):
    return [item["service_name"] for item in result["ranked_candidates"]]


# --- Scenario A: clean propagation chain -------------------------------

def test_clean_chain_ranks_the_deepest_dependency_first():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("database", "2026-11-13T10:00:00Z"),
        event("catalog", "2026-11-13T10:01:00Z"),
        event("recommendation", "2026-11-13T10:02:00Z", method="ewma"),
        event("frontend", "2026-11-13T10:03:00Z"),
    ])

    assert result["status"] == "ranked"
    assert result["summary"]["events_used"] == 4
    assert names(result) == ["database", "catalog", "recommendation", "frontend"]

    database = by_name(result, "database")
    # 3 of its 4 dependents are anomalous ("payment" is not): 20 + 30 + 20 + 20.
    assert database["score"] == 90
    assert database["confidence_level"] == "high"
    assert database["margin_to_nearest_competitor"] == 30
    assert database["evidence"]["coverage_ratio"] == 0.75
    assert database["likely_propagated"] is False
    assert [
        item["service_name"] for item in database["supporting_affected_services"]
    ] == ["catalog", "recommendation", "frontend"]


def test_services_with_an_anomalous_dependency_are_marked_as_propagated():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("database", "2026-11-13T10:00:00Z"),
        event("catalog", "2026-11-13T10:01:00Z"),
        event("recommendation", "2026-11-13T10:02:00Z"),
        event("frontend", "2026-11-13T10:03:00Z"),
    ])

    catalog = by_name(result, "catalog")
    assert catalog["likely_propagated"] is True
    assert catalog["score"] == 60
    assert catalog["confidence_level"] == "low"
    assert [item["service_name"] for item in catalog["anomalous_upstream_services"]] == [
        "database"
    ]
    assert catalog["anomalous_upstream_services"][0]["delay_seconds"] < 0

    # A second anomalous dependency lowers the cap by 5 points.
    assert by_name(result, "recommendation")["score"] == 55


def test_candidate_without_anomalous_dependents_has_low_score():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("payment", "2026-11-13T10:00:00Z"),
    ])

    candidate = result["ranked_candidates"][0]
    assert candidate["service_name"] == "payment"
    assert candidate["score"] == 40
    assert candidate["confidence_level"] == "low"
    assert candidate["supporting_affected_services"] == []


# --- Scenario B: everything in the same sample ------------------------

def test_simultaneous_anomalies_are_resolved_by_topology_not_by_name():
    timestamp = "2026-11-13T10:00:00Z"
    result = RootCauseAnalyzer(make_topology()).analyze([
        event(service, timestamp)
        for service in ("database", "catalog", "recommendation", "frontend")
    ])

    # "catalog" comes before "database" alphabetically: it must not win.
    assert names(result)[0] == "database"
    database = by_name(result, "database")
    assert {
        item["relation"] for item in database["supporting_affected_services"]
    } == {"concurrent"}
    assert by_name(result, "catalog")["likely_propagated"] is True


# --- Scenario C: the symptom is detected first -------------------------

def test_symptom_detected_slightly_before_the_cause_is_still_resolved():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("frontend", "2026-11-13T10:00:00Z"),
        event("database", "2026-11-13T10:00:30Z"),
    ])

    assert names(result)[0] == "database"
    assert by_name(result, "frontend")["likely_propagated"] is True


def test_symptom_detected_minutes_before_the_cause_is_resolved_by_dependency():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("frontend", "2026-11-13T10:00:00Z"),
        event("database", "2026-11-13T10:03:00Z"),
    ])

    assert names(result)[0] == "database"
    database = by_name(result, "database")
    assert database["supporting_affected_services"] == []
    assert [
        item["service_name"] for item in database["earlier_affected_services"]
    ] == ["frontend"]


# --- Windows -------------------------------------------------------------

def test_anomaly_outside_propagation_window_is_not_evidence():
    result = RootCauseAnalyzer(
        make_topology(),
        propagation_window_seconds=60,
        simultaneity_tolerance_seconds=0,
    ).analyze([
        event("database", "2026-11-13T10:00:00Z"),
        event("catalog", "2026-11-13T10:02:00Z"),
    ])

    database = by_name(result, "database")
    assert database["supporting_affected_services"] == []
    assert database["score"] == 40
    # Equal scores: the larger blast radius wins, not the alphabetical order.
    assert names(result)[0] == "database"


def test_analyze_incidents_separates_unrelated_anomalies():
    analyzer = RootCauseAnalyzer(make_topology())
    events = [
        event("catalog", "2026-11-13T07:00:00Z"),
        event("database", "2026-11-13T10:00:00Z"),
        event("catalog", "2026-11-13T10:01:00Z"),
    ]

    mixed = analyzer.analyze(events)
    assert any("analyze_incidents" in warning for warning in mixed["warnings"])

    report = analyzer.analyze_incidents(events)
    assert report["incident_count"] == 2
    first, second = report["incidents"]
    assert names(first["report"]) == ["catalog"]
    assert names(second["report"])[0] == "database"
    assert second["start_time"] == "2026-11-13T10:00:00Z"


def test_analyze_incidents_without_events():
    report = RootCauseAnalyzer(make_topology()).analyze_incidents([])
    assert report["status"] == "insufficient_data"
    assert report["incident_count"] == 0


# --- Input handling --------------------------------------------------------

def test_events_with_missing_or_unknown_service_are_skipped_with_warnings():
    result = RootCauseAnalyzer(make_topology()).analyze([
        {"occurred_at": "2026-11-13T10:00:00Z", "dimensions": {}},
        event("not-in-topology", "2026-11-13T10:01:00Z"),
    ])

    assert result["status"] == "insufficient_data"
    assert result["summary"]["events_used"] == 0
    assert len(result["warnings"]) == 2


def test_invalid_timestamp_is_skipped_with_warning():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("database", "not-a-timestamp")
    ])

    assert result["status"] == "insufficient_data"
    assert "invalid occurred_at" in result["warnings"][0]


def test_naive_timestamps_are_treated_as_utc():
    result = RootCauseAnalyzer(make_topology()).analyze([
        event("database", "2026-11-13T10:00:00"),
    ])

    assert result["ranked_candidates"][0]["first_anomaly_at"] == (
        "2026-11-13T10:00:00Z"
    )


def test_result_is_deterministic():
    analyzer = RootCauseAnalyzer(make_topology())
    events = [
        event("database", "2026-11-13T10:00:00Z"),
        event("catalog", "2026-11-13T10:00:00Z"),
    ]
    assert analyzer.analyze(events) == analyzer.analyze(list(reversed(events)))


# --- Topology loading -------------------------------------------------------

def test_accepts_serialized_service_topology_and_full_jaeger_response():
    serialized = make_topology().to_dict()
    events = [event("database", "2026-11-13T10:00:00Z")]

    first = RootCauseAnalyzer(serialized).analyze(events)
    second = RootCauseAnalyzer({"source": "jaeger", "topology": serialized}).analyze(events)

    assert first["ranked_candidates"] == second["ranked_candidates"]


def test_cycles_and_self_dependencies_are_skipped_with_warnings():
    payload = {
        "services": {"a": {}, "b": {}, "c": {}},
        "dependencies": {"a": ["b", "a"], "b": ["c"], "c": ["a"]},
    }
    analyzer = RootCauseAnalyzer(payload)

    assert len(analyzer.topology_warnings) >= 2

    result = analyzer.analyze([event("c", "2026-11-13T10:00:00Z")])
    assert result["status"] == "ranked"
    assert any("ignored" in warning for warning in result["warnings"])


def test_rejects_malformed_topology_mapping():
    with pytest.raises(ValueError, match="services and dependencies"):
        RootCauseAnalyzer({"services": {"database": {}}, "dependencies": []})


def test_rejects_invalid_input_and_configuration():
    with pytest.raises(TypeError, match="sequence"):
        RootCauseAnalyzer(make_topology()).analyze("not events")

    with pytest.raises(ValueError, match="positive and finite"):
        RootCauseAnalyzer(make_topology(), propagation_window_seconds=0)

    with pytest.raises(ValueError, match="non-negative and finite"):
        RootCauseAnalyzer(make_topology(), simultaneity_tolerance_seconds=-1)

    with pytest.raises(ValueError, match="must not exceed"):
        RootCauseAnalyzer(
            make_topology(),
            propagation_window_seconds=30,
            simultaneity_tolerance_seconds=60,
        )

    with pytest.raises(ValueError, match="service_field"):
        RootCauseAnalyzer(make_topology(), service_field=" ")
