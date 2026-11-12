
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiops_platform.database.models import AuditLog


# ==================================================
# CREATE WITHOUT COMMIT
# Utilisé par les transactions métier
# ==================================================

def add_audit_log(
    db: Session,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    details: dict | None = None,
) -> AuditLog:

    if not actor.strip():
        raise ValueError("L'acteur est obligatoire.")

    if not action.strip():
        raise ValueError("L'action est obligatoire.")

    if not resource_type.strip():
        raise ValueError("Le type de ressource est obligatoire.")

    log = AuditLog(
        actor=actor.strip(),
        action=action.strip(),
        resource_type=resource_type.strip(),
        resource_id=resource_id,
        details=details if details is not None else {},
    )

    db.add(log)
    db.flush()

    return log


# ==================================================
# CREATE WITH COMMIT
# Utilisé par l'API Audit Logs
# ==================================================

def create_audit_log(
    db: Session,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    details: dict | None = None,
) -> AuditLog:

    try:
        log = add_audit_log(
            db=db,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )

        db.commit()
        db.refresh(log)

    except Exception:
        db.rollback()
        raise

    return log


# ==================================================
# READ ONE
# ==================================================

def get_audit_log(
    db: Session,
    log_id: UUID,
) -> AuditLog | None:

    return db.get(AuditLog, log_id)


# ==================================================
# READ ALL WITH FILTERS
# ==================================================

def get_audit_logs(
    db: Session,
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
) -> list[AuditLog]:

    if not 1 <= limit <= 500:
        raise ValueError(
            "La limite doit être comprise entre 1 et 500."
        )

    statement = select(AuditLog)

    if actor is not None:
        statement = statement.where(
            AuditLog.actor == actor
        )

    if action is not None:
        statement = statement.where(
            AuditLog.action == action
        )

    if resource_type is not None:
        statement = statement.where(
            AuditLog.resource_type == resource_type
        )

    statement = statement.order_by(
        AuditLog.created_at.desc()
    ).limit(limit)

    return list(db.scalars(statement).all())
