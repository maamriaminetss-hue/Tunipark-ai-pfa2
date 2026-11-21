"""FastAPI routes for service topology."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from aiops_platform.intelligence.topology.jaeger_reader import (
    JaegerClientError,
    JaegerDependencyReader,
)

router = APIRouter(tags=["Topology"])

reader = JaegerDependencyReader()


@router.get(
    "/topology",
    summary="Get service dependency topology",
    description="Retrieve the service dependency graph from Jaeger traces.",
)
def get_topology(
    lookback_ms: int = Query(
        default=3_600_000,
        gt=0,
        description="Observation window in milliseconds.",
    ),
    end_ts: int | None = Query(
        default=None,
        gt=0,
        description="End timestamp in Unix milliseconds.",
    ),
) -> dict[str, Any]:
    """Return the current service dependency topology."""
    try:
        return reader.fetch_topology(
            lookback_ms=lookback_ms,
            end_ts=end_ts,
        )

    except JaegerClientError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Jaeger service unavailable: {exc}",
        ) from exc