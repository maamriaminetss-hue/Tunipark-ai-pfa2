
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
)
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db

from aiops_platform.api.schemas import (
    IncidentEventRead,
    EventRead,
    IncidentRead,
)

from aiops_platform.database.models import (
    Incident,
    Event,
    IncidentEvent,
)

from aiops_platform.database.incident_event_crud import (
    link_event_to_incident,
    get_incident_events,
    get_event_incidents,
    unlink_event_from_incident,
)

from aiops_platform.database.audit_crud import add_audit_log


router = APIRouter(
    tags=["Incident/Event Correlation"],
)

DBSession = Annotated[Session, Depends(get_db)]


# ==================================================
# PUT : ASSOCIER UN EVENT A UN INCIDENT
# ==================================================

@router.put(
    "/incidents/{incident_id}/events/{event_id}",
    response_model=IncidentEventRead,
)
def associate_event(
    incident_id: UUID,
    event_id: UUID,
    db: DBSession,
):

    existing_link = db.get(
        IncidentEvent,
        (incident_id, event_id),
    )

    try:
        link = link_event_to_incident(
            db=db,
            incident_id=incident_id,
            event_id=event_id,
            commit=False,
        )

        # Ne pas créer un doublon d'audit
        if existing_link is None:

            add_audit_log(
                db=db,
                actor="api_system",
                action="incident_event.linked",
                resource_type="incident_event",
                resource_id=incident_id,
                details={
                    "incident_id": str(incident_id),
                    "event_id": str(event_id),
                },
            )

            db.commit()
            db.refresh(link)

        return link

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Association annulée : impossible d'enregistrer l'audit.",
        ) from exc


# ==================================================
# GET : EVENTS D'UN INCIDENT
# ==================================================

@router.get(
    "/incidents/{incident_id}/events",
    response_model=list[EventRead],
)
def read_incident_events(
    incident_id: UUID,
    db: DBSession,
):

    if db.get(Incident, incident_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Incident introuvable.",
        )

    return get_incident_events(db, incident_id)


# ==================================================
# GET : INCIDENTS D'UN EVENT
# ==================================================

@router.get(
    "/events/{event_id}/incidents",
    response_model=list[IncidentRead],
)
def read_event_incidents(
    event_id: UUID,
    db: DBSession,
):

    if db.get(Event, event_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Événement introuvable.",
        )

    return get_event_incidents(db, event_id)


# ==================================================
# DELETE : SUPPRIMER UNE ASSOCIATION + AUDIT
# ==================================================

@router.delete(
    "/incidents/{incident_id}/events/{event_id}",
    status_code=204,
)
def remove_event_association(
    incident_id: UUID,
    event_id: UUID,
    db: DBSession,
):

    link = db.get(
        IncidentEvent,
        (incident_id, event_id),
    )

    if link is None:
        raise HTTPException(
            status_code=404,
            detail="Association introuvable.",
        )

    try:
        unlink_event_from_incident(
            db=db,
            incident_id=incident_id,
            event_id=event_id,
            commit=False,
        )

        add_audit_log(
            db=db,
            actor="api_system",
            action="incident_event.unlinked",
            resource_type="incident_event",
            resource_id=incident_id,
            details={
                "incident_id": str(incident_id),
                "event_id": str(event_id),
            },
        )

        db.commit()

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Suppression annulée : impossible d'enregistrer l'audit.",
        ) from exc

    return Response(status_code=204)
