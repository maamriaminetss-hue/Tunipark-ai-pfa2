import pytest

from aiops_platform.intelligence.rca import (
    RootCauseAnalyzer,
    bootstrap_ci,
    evaluate_methods,
    rank_last_changed,
    rank_most_anomalous,
    rank_with_topology,
    rank_without_topology,
    top_k_hit,
)
from aiops_platform.intelligence.topology import ServiceTopology


def make_topology():
    graph = ServiceTopology()
    for name in ("frontend", "catalog", "database"):
        graph.add_service(name)
    graph.add_dependency("frontend", "catalog")
    graph.add_dependency("catalog", "database")
    return graph


def event(service, timestamp, score):
    return {
        "occurred_at": timestamp,
        "dimensions": {"service_name": service},
        "methods": ["mad"],
        "reasons": ["x"],
        "scores": {"mad": score},
    }


# The frontend has the strongest score and the earliest onset (a symptom),
# but the database is the real origin.
EVENTS = [
    event("frontend", "2026-11-13T10:00:00Z", 30.0),
    event("catalog", "2026-11-13T10:00:20Z", 8.0),
    event("database", "2026-11-13T10:00:40Z", 6.0),
]


def test_most_anomalous_uses_the_strongest_score():
    assert rank_most_anomalous(EVENTS) == ["frontend", "catalog", "database"]


def test_without_topology_uses_the_earliest_onset():
    assert rank_without_topology(EVENTS) == ["frontend", "catalog", "database"]


def test_with_topology_finds_the_dependency_even_when_symptom_is_first():
    analyzer = RootCauseAnalyzer(make_topology())
    assert rank_with_topology(analyzer, EVENTS)[0] == "database"


def test_last_changed_ranks_most_recent_change_before_the_incident():
    changes = [
        {"service_name": "catalog", "changed_at": "2026-11-13T09:00:00Z"},
        {"service_name": "database", "changed_at": "2026-11-13T09:50:00Z"},
        {"service_name": "frontend", "changed_at": "2026-11-13T10:30:00Z"},  # after
        {"service_name": "database", "changed_at": "2026-11-13T08:00:00Z"},  # older
    ]
    assert rank_last_changed(EVENTS, changes) == ["database", "catalog"]
    assert rank_last_changed(EVENTS, []) == []
    assert rank_last_changed([], changes) == []


def test_top_k_hit():
    assert top_k_hit(["a", "b", "c"], "b", 1) is False
    assert top_k_hit(["a", "b", "c"], "b", 2) is True
    with pytest.raises(ValueError):
        top_k_hit(["a"], "a", 0)


def test_bootstrap_ci_is_reproducible_and_bounded():
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    first = bootstrap_ci(values, n_bootstrap=500, seed=7)
    second = bootstrap_ci(values, n_bootstrap=500, seed=7)
    assert first == second
    assert 0.0 <= first[0] <= first[1] <= 1.0
    with pytest.raises(ValueError):
        bootstrap_ci([])


def build_cases():
    cases = []
    truth = {}
    for index in range(6):
        case_id = f"case-{index}"
        cases.append({"case_id": case_id, "events": EVENTS, "changes": []})
        truth[case_id] = "database"
    return cases, truth


def test_evaluate_methods_reports_accuracy_intervals_and_paired_differences():
    analyzer = RootCauseAnalyzer(make_topology())
    cases, truth = build_cases()

    report = evaluate_methods(
        cases,
        truth,
        {
            "with_topology": lambda inputs: rank_with_topology(analyzer, inputs["events"]),
            "most_anomalous": lambda inputs: rank_most_anomalous(inputs["events"]),
        },
        reference_method="with_topology",
        n_bootstrap=200,
        seed=1,
    )

    topology = report["methods"]["with_topology"]
    baseline = report["methods"]["most_anomalous"]
    assert topology["top1"]["accuracy"] == 1.0
    assert baseline["top1"]["accuracy"] == 0.0
    assert baseline["top3"]["accuracy"] == 1.0
    assert topology["top1"]["n"] == 6
    assert report["paired_differences"]["most_anomalous"]["top1"][
        "difference_in_accuracy"
    ] == 1.0
    assert report["seed"] == 1


def test_methods_never_receive_the_ground_truth():
    cases, truth = build_cases()
    seen = []

    def spy(inputs):
        seen.append(inputs)
        return ["database"]

    evaluate_methods(cases, truth, {"spy": spy}, n_bootstrap=10)

    assert all(set(inputs) == {"events", "changes"} for inputs in seen)
    assert "database" not in repr([inputs.get("case_id") for inputs in seen])


def test_evaluate_methods_rejects_missing_or_duplicate_ground_truth():
    cases, truth = build_cases()
    with pytest.raises(ValueError, match="ground truth missing"):
        evaluate_methods(cases, {}, {"m": lambda inputs: []}, n_bootstrap=10)

    duplicated = cases + [cases[0]]
    with pytest.raises(ValueError, match="unique"):
        evaluate_methods(duplicated, truth, {"m": lambda inputs: []}, n_bootstrap=10)

    with pytest.raises(ValueError, match="reference_method"):
        evaluate_methods(
            cases, truth, {"m": lambda inputs: []},
            reference_method="x", n_bootstrap=10,
        )
