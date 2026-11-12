import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiops_platform.database.models import Event


# CREATE : enregistrer un événement
def create_event(
    db: Session,
    event_type: str,
    severity: str = "info",
    service_id: uuid.UUID | None = None,
    source: str | None = None,
    message: str | None = None,
    metric_name: str | None = None,
    metric_value: float | None = None,
    labels: dict | None = None,
    occurred_at: datetime | None = None,
) -> Event:

    if not event_type.strip():
        raise ValueError("Le type d'événement est obligatoire.")

    event_data = {
        "event_type": event_type,
        "severity": severity,
        "service_id": service_id,
        "source": source,
        "message": message,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "labels": labels if labels is not None else {},
    }

    if occurred_at is not None:
        event_data["occurred_at"] = occurred_at

    event = Event(**event_data)

    db.add(event)

    try:
        db.commit()
        db.refresh(event)
    except Exception:
        db.rollback()
        raise

    return event


# READ : récupérer un événement par son identifiant
def get_event_by_id(
    db: Session,
    event_id: uuid.UUID,
) -> Event | None:

    return db.get(Event, event_id)


# READ : récupérer les événements
def get_events(
    db: Session,
    service_id: uuid.UUID | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> list[Event]:

    if not 1 <= limit <= 500:
        raise ValueError("La limite doit être comprise entre 1 et 500.")

    statement = select(Event)

    if service_id is not None:
        statement = statement.where(Event.service_id == service_id)

    if event_type is not None:
        statement = statement.where(Event.event_type == event_type)

    if severity is not None:
        statement = statement.where(Event.severity == severity)

    statement = statement.order_by(
        Event.occurred_at.desc()
    ).limit(limit)

    return list(db.scalars(statement).all())


# DELETE : supprimer un événement (maintenance / rétention)
def delete_event(
    db: Session,
    event_id: uuid.UUID,
) -> bool:

    event = db.get(Event, event_id)

    if event is None:
        return False

    db.delete(event)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return True