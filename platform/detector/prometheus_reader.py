"""Read Prometheus time-series data and export it to CSV."""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


DEFAULT_QUERY = (
    'sum by (demo_payment_currency) '
    '(rate(demo_payment_transactions_total{service_name="payment"}[5m]))'
)


def fetch_range(
    prometheus_url: str,
    query: str,
    minutes: int = 15,
    step: str = "30s",
    timeout: int = 10,
) -> pd.DataFrame:
    """Fetch a Prometheus range query and return its samples as a DataFrame."""
    if minutes <= 0:
        raise ValueError("minutes must be greater than zero")

    end = datetime.now(timezone.utc).timestamp()
    start = end - minutes * 60

    response = requests.get(
        f"{prometheus_url.rstrip('/')}/api/v1/query_range",
        params={
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        },
        timeout=timeout,
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(
            f"Prometheus query failed: {payload.get('error', 'unknown error')}"
        )

    rows = []
    for series in payload.get("data", {}).get("result", []):
        labels = series.get("metric", {})
        for timestamp, value in series.get("values", []):
            rows.append(
                {
                    **labels,
                    "timestamp": pd.to_datetime(
                        float(timestamp), unit="s", utc=True
                    ),
                    "value": float(value),
                }
            )

    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export payment metrics from Prometheus to CSV."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
        help="Prometheus base URL",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="PromQL query to execute",
    )
    parser.add_argument(
        "--minutes", type=int, default=15,
        help="Look-back window in minutes",
    )
    parser.add_argument(
        "--step", default="30s",
        help="Prometheus sampling interval, for example 30s",
    )
    parser.add_argument(
        "--output", default="data/metrics/payment_rate.csv",
        help="CSV output path",
    )
    args = parser.parse_args()

    try:
        df = fetch_range(args.url, args.query, args.minutes, args.step)
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if df.empty:
        print("Aucune donnée retournée pour cette requête.")
        print("Vérifie la période, la requête PromQL et les données disponibles.")
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df.to_csv(output, index=False)

    print(f"Prometheus : {args.url}")
    print(f"Échantillons récupérés : {len(df)}")
    print(f"Colonnes : {', '.join(df.columns)}")
    print(f"Fichier CSV : {output}")
    print("\nDerniers échantillons :")
    print(df.tail(10).to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
