
from sqlalchemy import select
from sqlalchemy.orm import Session

from aiops_platform.database.models import Service


# CREATE : créer un service
def create_service(
    db: Session,
    name: str,
    description: str | None = None,
    environment: str = "development",
) -> Service:

    # Vérifier si le service existe déjà
    existing_service = db.scalar(
        select(Service).where(Service.name == name)
    )

    if existing_service:
        return existing_service

    # Construire le nouvel objet Service
    service = Service(
        name=name,
        description=description,
        environment=environment,
    )

    # Ajouter le service à la session
    db.add(service)

    # Enregistrer dans PostgreSQL
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Récupérer les valeurs générées par PostgreSQL
    db.refresh(service)

    return service


# READ : rechercher un service par son nom
def get_service_by_name(
    db: Session,
    name: str,
) -> Service | None:

    statement = select(Service).where(Service.name == name)

    return db.scalar(statement)


# READ : récupérer tous les services
def get_services(db: Session) -> list[Service]:

    statement = select(Service).order_by(Service.created_at.desc())

    return list(db.scalars(statement).all())
# UPDATE : modifier un service existant
def update_service(
    db: Session,
    name: str,
    description: str | None,
    environment: str,
) -> Service | None:

    # Rechercher le service
    service = db.scalar(
        select(Service).where(Service.name == name)
    )

    # Vérifier qu'il existe
    if service is None:
        return None

    # Modifier ses informations
    service.description = description
    service.environment = environment

    # Enregistrer les modifications
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Actualiser l'objet depuis PostgreSQL
    db.refresh(service)

    return service

from sqlalchemy.exc import IntegrityError


# DELETE : supprimer un service
def delete_service(
    db: Session,
    name: str,
) -> bool:

    # Rechercher le service
    service = db.scalar(
        select(Service).where(Service.name == name)
    )

    # Vérifier qu'il existe
    if service is None:
        return False

    # Protéger les services utilisés par des incidents ou événements
    if service.incidents or service.events:
        raise ValueError(
            "Impossible de supprimer ce service : "
            "il est encore associé à des incidents ou événements."
        )

    # Supprimer le service
    db.delete(service)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "Suppression refusée : le service est encore référencé."
        ) from exc
    except Exception:
        db.rollback()
        raise

    return True
