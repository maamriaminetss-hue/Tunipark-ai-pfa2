"""Top-k evaluation of root-cause methods with bootstrap confidence intervals.

The ground truth is passed separately from the cases. Each method only
receives ``{"events": ..., "changes": ...}``; it never sees the true root
cause, the case identifier or the scenario label (see EVALUATION.md).

No performance figure is invented here: every number is computed from the
cases given by the caller.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any


Method = Callable[[Mapping[str, Any]], Sequence[str]]


def top_k_hit(ranking: Sequence[str], truth: str, k: int) -> bool:
    """True when the true root cause is among the first k ranked services."""
    if k < 1:
        raise ValueError("k must be at least 1")
    return truth in list(ranking)[:k]


def bootstrap_ci(
    values: Sequence[float],
    *,
    n_bootstrap: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval of the mean."""
    if not values:
        raise ValueError("values must not be empty")
    if n_bootstrap < 1:
        raise ValueError("n_bootstrap must be at least 1")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")

    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(n_bootstrap)
    )
    lower_index = int(((1 - confidence) / 2) * (n_bootstrap - 1))
    upper_index = int((1 - (1 - confidence) / 2) * (n_bootstrap - 1))
    return means[lower_index], means[upper_index]


def _paired_bootstrap_ci(
    differences: Sequence[float],
    *,
    n_bootstrap: int,
    seed: int,
) -> tuple[float, float]:
    return bootstrap_ci(differences, n_bootstrap=n_bootstrap, seed=seed)


def evaluate_methods(
    cases: Sequence[Mapping[str, Any]],
    ground_truth: Mapping[str, str],
    methods: Mapping[str, Method],
    *,
    ks: Sequence[int] = (1, 3),
    reference_method: str | None = None,
    n_bootstrap: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """Compute Top-k accuracy per method, with bootstrap intervals.

    ``cases``: list of ``{"case_id": str, "events": [...], "changes": [...]}``.
    ``ground_truth``: ``{case_id: true_root_cause_service}``, kept separate.
    ``methods``: ``{name: callable(inputs) -> ranked service names}``.
    ``reference_method``: when given, every other method is compared with it
    using the paired difference in accuracy (an effect size) and its interval.
    """
    if not cases:
        raise ValueError("cases must not be empty")
    if not methods:
        raise ValueError("methods must not be empty")
    if reference_method is not None and reference_method not in methods:
        raise ValueError("reference_method must be one of the methods")

    case_ids: list[str] = []
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every case needs a non-empty case_id")
        case_ids.append(case_id)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("case_id values must be unique")
    missing = [case_id for case_id in case_ids if case_id not in ground_truth]
    if missing:
        raise ValueError(f"ground truth missing for: {', '.join(missing)}")

    hits: dict[str, dict[int, list[float]]] = {
        name: {k: [] for k in ks} for name in methods
    }

    for case in cases:
        # Structural separation: methods only get the observable inputs.
        inputs = {
            "events": list(case.get("events") or []),
            "changes": list(case.get("changes") or []),
        }
        truth = ground_truth[case["case_id"]]
        for name, method in methods.items():
            ranking = list(method(inputs))
            for k in ks:
                hits[name][k].append(1.0 if top_k_hit(ranking, truth, k) else 0.0)

    result_methods: dict[str, Any] = {}
    for name in methods:
        per_k: dict[str, Any] = {}
        for k in ks:
            values = hits[name][k]
            low, high = bootstrap_ci(values, n_bootstrap=n_bootstrap, seed=seed)
            per_k[f"top{k}"] = {
                "hits": int(sum(values)),
                "n": len(values),
                "accuracy": sum(values) / len(values),
                "ci95": [low, high],
            }
        result_methods[name] = per_k

    paired: dict[str, Any] = {}
    if reference_method is not None:
        for name in methods:
            if name == reference_method:
                continue
            per_k = {}
            for k in ks:
                diffs = [
                    a - b
                    for a, b in zip(hits[reference_method][k], hits[name][k])
                ]
                low, high = _paired_bootstrap_ci(
                    diffs, n_bootstrap=n_bootstrap, seed=seed
                )
                per_k[f"top{k}"] = {
                    "reference": reference_method,
                    "difference_in_accuracy": sum(diffs) / len(diffs),
                    "ci95": [low, high],
                }
            paired[name] = per_k

    return {
        "seed": seed,
        "n_cases": len(cases),
        "n_bootstrap": n_bootstrap,
        "ks": list(ks),
        "methods": result_methods,
        "paired_differences": paired,
        "note": (
            "Valeurs calculées uniquement à partir des cas fournis ; "
            "aucune performance n'est supposée."
        ),
    }
