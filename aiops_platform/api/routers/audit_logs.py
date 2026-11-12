
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db
from aiops_platform.api.schemas import AuditLogCreate, AuditLogRead

from aiops_platform.database.audit_crud import (
    create_audit_log,
    get_audit_log,
    get_audit_logs,
)


router = APIRouter(
    prefix="/audit-logs",
    tags=["Audit Logs"],
)

DBSession = Annotated[Session, Depends(get_db)]


# CREATE : enregistrer une entrée d'audit
@router.post("", response_model=AuditLogRead, status_code=201)
def create_new_audit_log(
    payload: AuditLogCreate,
    db: DBSession,
):
    try:
        return create_audit_log(
            db=db,
            actor=payload.actor,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            details=payload.details,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


# READ : consulter les journaux avec filtres
@router.get("", response_model=list[AuditLogRead])
def list_audit_logs(
    db: DBSession,
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    return get_audit_logs(
        db=db,
        actor=actor,
        action=action,
        resource_type=resource_type,
        limit=limit,
    )


# READ : consulter un journal par UUID
@router.get("/{log_id}", response_model=AuditLogRead)
def read_audit_log(
    log_id: UUID,
    db: DBSession,
):
    log = get_audit_log(db, log_id)

    if log is None:
        raise HTTPException(
            status_code=404,
            detail="Journal d'audit introuvable.",
        )

    return log
