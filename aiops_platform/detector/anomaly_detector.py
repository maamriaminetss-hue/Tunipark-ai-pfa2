"""Détection d'anomalies sur les métriques exportées par Prometheus."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from aiops_observability.telemetry import observe
import numpy as np
import pandas as pd


OUTPUT_COLUMNS = ["method", "expected", "score", "is_anomaly", "reason"]
MIN_SCALE = 1e-6


def _validate(
    method, window, min_history, threshold, alpha,
    fixed_lower, fixed_upper
):
    if method not in {"fixed", "mad", "ewma"}:
        raise ValueError("Méthode inconnue : choisir fixed, mad ou ewma.")
    if window < 2:
        raise ValueError("window doit être au moins égal à 2.")
    if min_history < 2:
        raise ValueError("min_history doit être au moins égal à 2.")
    if threshold <= 0:
        raise ValueError("threshold doit être positif.")
    if not 0 < alpha < 1:
        raise ValueError("alpha doit être compris entre 0 et 1.")
    if (
        fixed_lower is not None
        and fixed_upper is not None
        and fixed_lower >= fixed_upper
    ):
        raise ValueError("La limite basse doit être inférieure à la limite haute.")
    if method == "fixed" and fixed_lower is None and fixed_upper is None:
        raise ValueError(
            "La méthode fixed nécessite --fixed-lower ou --fixed-upper."
        )


def _ewma(values, alpha):
    estimate = float(values[0])
    for value in values[1:]:
        estimate = alpha * float(value) + (1 - alpha) * estimate
    return estimate

@observe("anomaly_detection")
def detect_anomalies(
    data: pd.DataFrame,
    method: str = "mad",
    *,
    window: int = 12,
    min_history: int = 12,
    threshold: float = 3.5,
    alpha: float = 0.3,
    fixed_lower: float | None = None,
    fixed_upper: float | None = None,
) -> pd.DataFrame:
    """Analyse chaque série indépendamment, sans utiliser les valeurs futures.

    Colonnes obligatoires : timestamp et value.
    Toutes les autres colonnes identifient une série, par exemple la devise.
    """
    
    _validate(
        method, window, min_history, threshold, alpha,
        fixed_lower, fixed_upper
    )

    missing = {"timestamp", "value"} - set(data.columns)
    if missing:
        raise ValueError(
            f"Colonnes obligatoires manquantes : {', '.join(sorted(missing))}"
        )

    frame = data.copy()
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], errors="coerce", utc=True
    )
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "value"])
    frame = frame[np.isfinite(frame["value"])]

    labels = [
        column for column in frame.columns
        if column not in {"timestamp", "value"}
    ]

    if frame.empty:
        return pd.DataFrame(columns=[*data.columns, *OUTPUT_COLUMNS])

    if labels:
        grouper = labels[0] if len(labels) == 1 else labels
        groups = frame.groupby(grouper, sort=False, dropna=False)
    else:
        groups = [(None, frame)]

    records = []

    for _, group in groups:
        group = group.sort_values("timestamp", kind="stable")
        values = group["value"].to_numpy(dtype=float)

        for index, (_, row) in enumerate(group.iterrows()):
            value = float(values[index])
            expected = np.nan
            score = np.nan
            anomaly = False
            reason = "insufficient_history"

            if method == "fixed":
                expected = np.nan
                score = 0.0
                reason = "within_fixed_limits"

                if fixed_lower is not None and value < fixed_lower:
                    anomaly = True
                    score = fixed_lower - value
                    reason = "below_fixed_lower"
                elif fixed_upper is not None and value > fixed_upper:
                    anomaly = True
                    score = value - fixed_upper
                    reason = "above_fixed_upper"

            else:
                start = max(0, index - window)
                history = values[start:index]

                if len(history) >= min_history:
                    if method == "mad":
                        expected = float(np.median(history))
                        mad = float(np.median(np.abs(history - expected)))
                        deviation = abs(value - expected)

                        scale = max(1.4826 * mad, MIN_SCALE)
                        score = deviation / scale
                        anomaly = score > threshold

                        if mad == 0.0:
                            reason = (
                                "deviation_from_constant_history"
                                if anomaly else "constant_history"
                            )
                        else:
                            reason = (
                                "deviation_above_mad_limit"
                                if anomaly else "within_mad_limit"
                            )

                    elif method == "ewma":
                        expected = _ewma(history, alpha)
                        scale = max(
                            float(np.std(history, ddof=1)),
                            MIN_SCALE
                        )
                        score = abs(value - expected) / scale
                        anomaly = score > threshold
                        reason = (
                            "deviation_above_ewma_limit"
                            if anomaly else "within_ewma_limit"
                        )
            record = row.to_dict()
            record.update({
                "method": method,
                "expected": expected,
                "score": score,
                "is_anomaly": bool(anomaly),
                "reason": reason,
            })
            records.append(record)

    result = pd.DataFrame(records)
    return result.sort_values(
        "timestamp", kind="stable"
    ).reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(
        description="Détecter des anomalies dans un CSV Prometheus."
    )
    parser.add_argument(
        "--input", default="data/metrics/payment_rate.csv"
    )
    parser.add_argument(
        "--output", default="data/metrics/anomaly_results.csv"
    )
    parser.add_argument(
        "--method", choices=["fixed", "mad", "ewma", "all"], default="mad"
    )
    parser.add_argument("--window", type=int, default=12)
    parser.add_argument("--min-history", type=int, default=12)
    parser.add_argument("--threshold", type=float, default=3.5)
    parser.add_argument("--alpha", type=float, default=0.3)
    parser.add_argument("--fixed-lower", type=float, default=None)
    parser.add_argument("--fixed-upper", type=float, default=None)
    args = parser.parse_args()

    try:
        data = pd.read_csv(args.input)

        if args.method == "all":
            methods = ["mad", "ewma"]
            if (
                args.fixed_lower is not None
                or args.fixed_upper is not None
            ):
                methods.insert(0, "fixed")
            else:
                print(
                    "INFO : méthode fixed ignorée ; "
                    "indique une limite pour l'inclure.",
                    file=sys.stderr,
                )
        else:
            methods = [args.method]

        results = [
            detect_anomalies(
                data,
                method,
                window=args.window,
                min_history=args.min_history,
                threshold=args.threshold,
                alpha=args.alpha,
                fixed_lower=args.fixed_lower,
                fixed_upper=args.fixed_upper,
            )
            for method in methods
        ]

        output = pd.concat(results, ignore_index=True)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        output.to_csv(path, index=False)

        print(f"Échantillons lus : {len(data)}")
        print(f"Méthodes exécutées : {', '.join(methods)}")
        for method in methods:
            subset = output[output["method"] == method]
            print(
                f"{method} : "
                f"{int(subset['is_anomaly'].sum())} anomalie(s) signalée(s)"
            )
        print(f"Résultats enregistrés dans : {path}")
        print(output.tail(10).to_string(index=False))
        return 0

    except (OSError, pd.errors.ParserError, ValueError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
