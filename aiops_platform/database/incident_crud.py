
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiops_platform.database.models import Incident


VALID_SEVERITIES = {"low", "medium", "high", "critical"}

VALID_STATUSES = {
    "open",
    "investigating",
    "resolved",
    "closed",
}

_UNSET = object()


# ==================================================
# CREATE
# ==================================================


def create_incident(
    db: Session,
    title: str,
    severity: str,
    description: str | None = None,
    service_id: uuid.UUID | None = None,
    commit: bool = True,
    incident_id: uuid.UUID | None = None,
    started_at: datetime | None = None,
) -> Incident:

    if not title.strip():
        raise ValueError("Le titre est obligatoire.")

    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Gravité invalide : {severity}")

    incident_data = {
        "title": title,
        "description": description,
        "severity": severity,
        "service_id": service_id,
    }

    if incident_id is not None:
        incident_data["id"] = incident_id

    if started_at is not None:
        incident_data["started_at"] = started_at

    incident = Incident(**incident_data)
    db.add(incident)

    try:
        db.flush()

        if commit:
            db.commit()
            db.refresh(incident)

    except Exception:
        if commit:
            db.rollback()
        raise
    return incident

# ==================================================
# READ ONE
# ==================================================

def get_incident_by_id(
    db: Session,
    incident_id: uuid.UUID,
) -> Incident | None:

    return db.get(Incident, incident_id)


# ==================================================
# READ ALL WITH FILTERS
# ==================================================

def get_incidents(
    db: Session,
    status: str | None = None,
    severity: str | None = None,
    service_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[Incident]:

    if not 1 <= limit <= 500:
        raise ValueError(
            "La limite doit être comprise entre 1 et 500."
        )

    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Statut invalide : {status}")

    if severity is not None and severity not in VALID_SEVERITIES:
        raise ValueError(f"Gravité invalide : {severity}")

    statement = select(Incident)

    if status is not None:
        statement = statement.where(
            Incident.status == status
        )

    if severity is not None:
        statement = statement.where(
            Incident.severity == severity
        )

    if service_id is not None:
        statement = statement.where(
            Incident.service_id == service_id
        )

    statement = statement.order_by(
        Incident.started_at.desc()
    ).limit(limit)

    return list(db.scalars(statement).all())


# ==================================================
# UPDATE
# ==================================================

def update_incident(
    db: Session,
    incident_id: uuid.UUID,
    title: str | None = None,
    description: str | None | object = _UNSET,
    severity: str | None = None,
    status: str | None = None,
    commit: bool = True,
) -> Incident | None:

    incident = db.get(Incident, incident_id)

    if incident is None:
        return None

    if title is not None:
        if not title.strip():
            raise ValueError("Le titre ne peut pas être vide.")
        incident.title = title

    if description is not _UNSET:
        incident.description = description

    if severity is not None:
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"Gravité invalide : {severity}")
        incident.severity = severity

    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Statut invalide : {status}")

        incident.status = status

        if status == "resolved":
            incident.resolved_at = datetime.now(timezone.utc)

        elif status in {"open", "investigating"}:
            incident.resolved_at = None

    try:
        db.flush()

        if commit:
            db.commit()
            db.refresh(incident)

    except Exception:
        db.rollback()
        raise

    return incident


# ==================================================
# DELETE
# ==================================================

def delete_incident(
    db: Session,
    incident_id: uuid.UUID,
    commit: bool = True,
) -> bool:

    incident = db.get(Incident, incident_id)

    if incident is None:
        return False

    db.delete(incident)

    try:
        db.flush()

        if commit:
            db.commit()

    except Exception:
        db.rollback()
        raise

    return True
