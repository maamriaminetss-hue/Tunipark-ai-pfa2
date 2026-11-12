
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ============================================================
# BASE COMMUNE
# ============================================================

class Base(DeclarativeBase):
    pass


# ============================================================
# 1. SERVICE
# Table : app.services
# ============================================================

class Service(Base):
    __tablename__ = "services"
    __table_args__ = {"schema": "app"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    environment: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'development'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    incidents: Mapped[list[Incident]] = relationship(
        back_populates="service"
    )

    events: Mapped[list[Event]] = relationship(
        back_populates="service"
    )


# ============================================================
# 2. INCIDENT
# Table : app.incidents
# ============================================================

class Incident(Base):
    __tablename__ = "incidents"

    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="incidents_severity_check",
        ),
        CheckConstraint(
            "status IN ('open', 'investigating', 'resolved', 'closed')",
            name="incidents_status_check",
        ),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    title: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    severity: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'open'"),
    )

    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.services.id"),
        nullable=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    service: Mapped[Service | None] = relationship(
        back_populates="incidents"
    )

    event_links: Mapped[list[IncidentEvent]] = relationship(
        back_populates="incident",
        passive_deletes="all",
    )


# ============================================================
# 3. EVENT
# Table : app.events
# ============================================================

class Event(Base):
    __tablename__ = "events"
    __table_args__ = {"schema": "app"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.services.id"),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    source: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    severity: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'info'"),
    )

    metric_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    metric_value: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    labels: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    service: Mapped[Service | None] = relationship(
        back_populates="events"
    )

    incident_links: Mapped[list[IncidentEvent]] = relationship(
        back_populates="event",
        passive_deletes="all",
    )


# ============================================================
# 4. INCIDENT EVENT
# Table : app.incident_events
# ============================================================

class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = {"schema": "app"}

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.incidents.id", ondelete="CASCADE"),
        primary_key=True,
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app.events.id", ondelete="CASCADE"),
        primary_key=True,
    )

    incident: Mapped[Incident] = relationship(
        back_populates="event_links"
    )

    event: Mapped[Event] = relationship(
        back_populates="incident_links"
    )


# ============================================================
# 5. AUDIT LOG
# Table : app.audit_logs
# ============================================================

class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "app"}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    actor: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
