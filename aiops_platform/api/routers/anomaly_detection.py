"""Protected, manually triggered anomaly-detection cycle."""

import os
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db
from aiops_platform.intelligence.monitoring.anomaly_detection_service import (
    AnomalyDetectionService,
)


router = APIRouter(
    prefix="/anomaly-detection",
    tags=["Anomaly Detection"],
)

DBSession = Annotated[Session, Depends(get_db)]
detection_service = AnomalyDetectionService()


def require_detection_token(
    x_anomaly_detection_token: str | None = Header(
        default=None,
        alias="X-Anomaly-Detection-Token",
    ),
) -> None:
    expected = os.getenv("ANOMALY_DETECTION_RUN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Anomaly detection trigger is disabled: configure "
                "ANOMALY_DETECTION_RUN_TOKEN."
            ),
        )
    if not x_anomaly_detection_token or not secrets.compare_digest(
        x_anomaly_detection_token,
        expected,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid anomaly detection token.",
        )


@router.post(
    "/run",
    summary="Run one Prometheus anomaly-detection cycle",
    description=(
        "Fetch service metrics, apply MAD/EWMA and configurable fixed limits, "
        "and create deduplicated metric_anomaly events after confirmation. "
        "This endpoint does not create incidents or execute remediation."
    ),
)
def run_anomaly_detection(
    db: DBSession,
    _authorized: Annotated[None, Depends(require_detection_token)],
) -> dict[str, Any]:
    return detection_service.run_once(db)
