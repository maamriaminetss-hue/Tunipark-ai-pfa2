
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db

from aiops_platform.api.schemas import (
    IncidentCreate,
    IncidentRead,
    IncidentUpdate,
)

from aiops_platform.database.models import Service

from aiops_platform.database.incident_crud import (
    create_incident,
    get_incident_by_id,
    get_incidents,
    update_incident,
)

from aiops_platform.database.audit_crud import add_audit_log


router = APIRouter(
    prefix="/incidents",
    tags=["Incidents"],
)

DBSession = Annotated[Session, Depends(get_db)]


# ==================================================
# CREATE INCIDENT + AUTOMATIC AUDIT
# ==================================================

@router.post(
    "",
    response_model=IncidentRead,
    status_code=201,
)
def create_new_incident(
    payload: IncidentCreate,
    db: DBSession,
):

    if payload.service_id is not None:
        service = db.get(Service, payload.service_id)

        if service is None:
            raise HTTPException(
                status_code=404,
                detail="Service associé introuvable.",
            )

    try:
        incident = create_incident(
            db=db,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            service_id=payload.service_id,
            commit=False,
        )

        add_audit_log(
            db=db,
            actor="api_system",
            action="incident.created",
            resource_type="incident",
            resource_id=incident.id,
            details={
                "title": incident.title,
                "severity": incident.severity,
                "service_id": (
                    str(incident.service_id)
                    if incident.service_id is not None
                    else None
                ),
            },
        )

        # Incident et audit enregistrés ensemble
        db.commit()
        db.refresh(incident)

        return incident

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Conflit lors de la création de l'incident.",
        ) from exc

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Création annulée : impossible d'enregistrer l'audit.",
        ) from exc


# ==================================================
# READ ALL INCIDENTS
# ==================================================

@router.get(
    "",
    response_model=list[IncidentRead],
)
def list_incidents(
    db: DBSession,
    incident_status: Annotated[
        str | None,
        Query(alias="status"),
    ] = None,
    severity: str | None = None,
    service_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):

    try:
        return get_incidents(
            db=db,
            status=incident_status,
            severity=severity,
            service_id=service_id,
            limit=limit,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


# ==================================================
# READ ONE INCIDENT
# ==================================================

@router.get(
    "/{incident_id}",
    response_model=IncidentRead,
)
def read_incident(
    incident_id: UUID,
    db: DBSession,
):

    incident = get_incident_by_id(db, incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident introuvable.",
        )

    return incident


# ==================================================
# UPDATE INCIDENT + AUTOMATIC AUDIT
# ==================================================

@router.patch(
    "/{incident_id}",
    response_model=IncidentRead,
)
def modify_incident(
    incident_id: UUID,
    payload: IncidentUpdate,
    db: DBSession,
):

    updates = payload.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="Aucune modification fournie.",
        )

    existing = get_incident_by_id(db, incident_id)

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Incident introuvable.",
        )

    # Sauvegarder les anciennes valeurs
    old_values = {
        field: getattr(existing, field)
        for field in updates
    }

    try:
        # Modifier sans commit immédiat
        incident = update_incident(
            db=db,
            incident_id=incident_id,
            commit=False,
            **updates,
        )

        if incident is None:
            raise ValueError("Incident introuvable.")

        # Comparer les anciennes et nouvelles valeurs
        changes = {
            field: {
                "from": old_values[field],
                "to": getattr(incident, field),
            }
            for field in updates
        }

        action = (
            "incident.status_changed"
            if "status" in updates
            else "incident.updated"
        )

        # Ajouter l'audit à la même transaction
        add_audit_log(
            db=db,
            actor="api_system",
            action=action,
            resource_type="incident",
            resource_id=incident_id,
            details={
                "changes": changes,
            },
        )

        db.commit()
        db.refresh(incident)

        return incident

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Modification annulée : impossible d'enregistrer l'audit.",
        ) from exc
