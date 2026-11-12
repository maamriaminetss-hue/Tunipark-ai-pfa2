
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiops_platform.database.models import (
    Event,
    Incident,
    IncidentEvent,
)


# ==================================================
# CREATE : ASSOCIER UN EVENT A UN INCIDENT
# ==================================================

def link_event_to_incident(
    db: Session,
    incident_id: uuid.UUID,
    event_id: uuid.UUID,
    commit: bool = True,
) -> IncidentEvent:

    incident = db.get(Incident, incident_id)

    if incident is None:
        raise ValueError("Incident introuvable.")

    event = db.get(Event, event_id)

    if event is None:
        raise ValueError("Événement introuvable.")

    existing_link = db.get(
        IncidentEvent,
        (incident_id, event_id),
    )

    if existing_link is not None:
        return existing_link

    link = IncidentEvent(
        incident_id=incident_id,
        event_id=event_id,
    )

    db.add(link)

    try:
        db.flush()

        if commit:
            db.commit()
            db.refresh(link)

    except Exception:
        db.rollback()
        raise

    return link


# ==================================================
# READ : EVENTS D'UN INCIDENT
# ==================================================

def get_incident_events(
    db: Session,
    incident_id: uuid.UUID,
) -> list[Event]:

    statement = (
        select(Event)
        .join(
            IncidentEvent,
            IncidentEvent.event_id == Event.id,
        )
        .where(
            IncidentEvent.incident_id == incident_id
        )
        .order_by(Event.occurred_at.desc())
    )

    return list(db.scalars(statement).all())


# ==================================================
# READ : INCIDENTS D'UN EVENT
# ==================================================

def get_event_incidents(
    db: Session,
    event_id: uuid.UUID,
) -> list[Incident]:

    statement = (
        select(Incident)
        .join(
            IncidentEvent,
            IncidentEvent.incident_id == Incident.id,
        )
        .where(
            IncidentEvent.event_id == event_id
        )
        .order_by(Incident.started_at.desc())
    )

    return list(db.scalars(statement).all())


# ==================================================
# DELETE : SUPPRIMER UNE ASSOCIATION
# ==================================================

def unlink_event_from_incident(
    db: Session,
    incident_id: uuid.UUID,
    event_id: uuid.UUID,
    commit: bool = True,
) -> bool:

    link = db.get(
        IncidentEvent,
        (incident_id, event_id),
    )

    if link is None:
        return False

    db.delete(link)

    try:
        db.flush()

        if commit:
            db.commit()

    except Exception:
        db.rollback()
        raise

    return True
