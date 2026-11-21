"""PostgreSQL persistence helpers for anomaly detection episode state."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from aiops_platform.database.models import AnomalyDetectionState


STATE_UNIQUE_CONSTRAINT = "uq_anomaly_detection_state_metric_service"


def lock_or_create_detection_state(
    db: Session,
    metric_name: str,
    service_name: str,
) -> AnomalyDetectionState:
    """Create a state row if needed and lock it for an idempotent update."""
    statement = postgres_insert(AnomalyDetectionState).values(
        metric_name=metric_name,
        service_name=service_name,
    )
    statement = statement.on_conflict_do_nothing(
        constraint=STATE_UNIQUE_CONSTRAINT
    )
    db.execute(statement)

    state = db.scalar(
        select(AnomalyDetectionState)
        .where(
            AnomalyDetectionState.metric_name == metric_name,
            AnomalyDetectionState.service_name == service_name,
        )
        .with_for_update()
    )
    if state is None:
        raise RuntimeError(
            "Unable to create or retrieve anomaly detection state for "
            f"{metric_name}/{service_name}"
        )
    return state
