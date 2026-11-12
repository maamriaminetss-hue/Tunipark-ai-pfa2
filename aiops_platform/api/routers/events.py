from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db
from aiops_platform.api.schemas import EventCreate, EventRead

from aiops_platform.database.models import Service
from aiops_platform.database.event_crud import (
    create_event,
    get_event_by_id,
    get_events,
)


router = APIRouter(
    prefix="/events",
    tags=["Events"],
)

DBSession = Annotated[Session, Depends(get_db)]


# CREATE : recevoir et enregistrer un événement
@router.post("", response_model=EventRead, status_code=201)
def create_new_event(
    payload: EventCreate,
    db: DBSession,
):

    # Vérifier que le service associé existe
    if payload.service_id is not None:
        service = db.get(Service, payload.service_id)

        if service is None:
            raise HTTPException(
                status_code=404,
                detail="Service associé introuvable.",
            )

    return create_event(
        db=db,
        event_type=payload.event_type,
        severity=payload.severity,
        service_id=payload.service_id,
        source=payload.source,
        message=payload.message,
        metric_name=payload.metric_name,
        metric_value=payload.metric_value,
        labels=payload.labels,
        occurred_at=payload.occurred_at,
    )


# READ : récupérer tous les événements avec filtres
@router.get("", response_model=list[EventRead])
def list_events(
    db: DBSession,
    service_id: UUID | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):

    return get_events(
        db=db,
        service_id=service_id,
        event_type=event_type,
        severity=severity,
        limit=limit,
    )


# READ : récupérer un événement par UUID
@router.get("/{event_id}", response_model=EventRead)
def read_event(
    event_id: UUID,
    db: DBSession,
):

    event = get_event_by_id(db, event_id)

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Événement introuvable.",
        )

    return event