"""Protected API endpoint for synchronizing service observations."""

from __future__ import annotations

import os
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db
from aiops_platform.database.service_discovery_crud import (
    upsert_service_discoveries,
)
from aiops_platform.intelligence.discovery.prometheus_service_reader import (
    PrometheusServiceDiscoveryReader,
)
from aiops_platform.intelligence.topology.jaeger_reader import (
    JaegerClientError,
    JaegerDependencyReader,
)


router = APIRouter(
    prefix="/service-discovery",
    tags=["Service Discovery"],
)

DBSession = Annotated[Session, Depends(get_db)]

# These readers are module-level like the existing topology reader, and can
# be replaced in tests without changing the application dependency graph.
jaeger_reader = JaegerDependencyReader()
prometheus_reader = PrometheusServiceDiscoveryReader()


def require_sync_token(
    x_service_discovery_token: str | None = Header(
        default=None,
        alias="X-Service-Discovery-Token",
    ),
) -> None:
    """Require a dedicated token; synchronization is disabled by default."""
    expected_token = os.getenv("SERVICE_DISCOVERY_SYNC_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service discovery synchronization is disabled: configure SERVICE_DISCOVERY_SYNC_TOKEN.",
        )

    if not x_service_discovery_token or not secrets.compare_digest(
        x_service_discovery_token,
        expected_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service discovery token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "/sync",
    summary="Synchronize discovered services",
    description=(
        "Store source-specific observations from Jaeger and Prometheus. "
        "This does not create, rename, or delete business services."
    ),
)
def synchronize_service_discoveries(
    db: DBSession,
    _authorized: Annotated[None, Depends(require_sync_token)],
) -> dict[str, Any]:
    """Fetch and persist each source independently; retain partial success."""
    source_readers = (
        ("jaeger", jaeger_reader.fetch_services),
        ("prometheus", prometheus_reader.fetch_services),
    )

    source_results: dict[str, dict[str, Any]] = {}
    successful_sources = 0

    for source, fetch_services in source_readers:
        try:
            names = fetch_services()
            records_written = upsert_service_discoveries(
                db=db,
                source=source,
                service_names=names,
            )
            successful_sources += 1
            source_results[source] = {
                "status": "ok",
                "observed_services": len(names),
                "records_written": records_written,
            }
        except Exception as exc:
            # A failed source is not treated as an empty snapshot. The CRUD
            # function rolls back write failures; this also clears any
            # residual failed transaction before the next source is attempted.
            db.rollback()
            source_results[source] = {
                "status": "error",
                "error_type": type(exc).__name__,
            }

    if successful_sources == 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "All service discovery sources failed.",
                "sources": source_results,
            },
        )

    return {
        "status": "ok" if successful_sources == len(source_readers) else "partial",
        "sources": source_results,
        "note": (
            "last_seen_at records when a source snapshot was observed; "
            "it is not proof that a service is currently healthy or active."
        ),
    }
