from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aiops_platform.api.dependencies import get_db
from aiops_platform.api.schemas import ServiceCreate, ServiceRead, ServiceUpdate

from aiops_platform.database.service_crud import (
    create_service,
    get_service_by_name,
    get_services,
    update_service,
    delete_service,
)


router = APIRouter(
    prefix="/services",
    tags=["Services"],
)

DBSession = Annotated[Session, Depends(get_db)]


# CREATE
@router.post(
    "",
    response_model=ServiceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_new_service(payload: ServiceCreate, db: DBSession):

    existing = get_service_by_name(db, payload.name)

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Ce service existe déjà.",
        )

    try:
        return create_service(
            db=db,
            name=payload.name,
            description=payload.description,
            environment=payload.environment,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Conflit : ce service existe déjà.",
        ) from exc


# READ ALL
@router.get("", response_model=list[ServiceRead])
def list_services(db: DBSession):
    return get_services(db)


# READ ONE
@router.get("/{name}", response_model=ServiceRead)
def read_service(name: str, db: DBSession):

    service = get_service_by_name(db, name)

    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Service introuvable.",
        )

    return service


# UPDATE
@router.put("/{name}", response_model=ServiceRead)
def modify_service(
    name: str,
    payload: ServiceUpdate,
    db: DBSession,
):

    service = update_service(
        db=db,
        name=name,
        description=payload.description,
        environment=payload.environment,
    )

    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Service introuvable.",
        )

    return service


# DELETE
@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_service(name: str, db: DBSession):

    try:
        deleted = delete_service(db, name)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Service introuvable.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)