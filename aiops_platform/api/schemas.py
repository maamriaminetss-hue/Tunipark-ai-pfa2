
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ==================================================
# SERVICES
# ==================================================

class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    environment: str = Field(default="development", min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Le nom du service est obligatoire.")

        return value


class ServiceUpdate(BaseModel):
    description: str | None = None
    environment: str = Field(min_length=1)


class ServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    environment: str
    created_at: datetime


# ==================================================
# INCIDENTS
# ==================================================

Severity = Literal["low", "medium", "high", "critical"]

IncidentStatus = Literal[
    "open",
    "investigating",
    "resolved",
    "closed",
]


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    severity: Severity
    service_id: UUID | None = None


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    severity: Severity | None = None
    status: IncidentStatus | None = None


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    severity: str
    status: str
    service_id: UUID | None
    started_at: datetime
    resolved_at: datetime | None
    created_at: datetime
# ==================================================
# EVENTS
# ==================================================

class EventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    severity: str = Field(default="info", min_length=1, max_length=30)

    service_id: UUID | None = None
    source: str | None = None
    message: str | None = None

    metric_name: str | None = None
    metric_value: float | None = None

    labels: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID | None
    event_type: str
    source: str | None
    message: str | None
    severity: str
    metric_name: str | None
    metric_value: float | None
    labels: dict[str, Any]
    occurred_at: datetime
    created_at: datetime


# ==================================================
# INCIDENT - EVENT ASSOCIATION
# ==================================================

class IncidentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    event_id: UUID





# ==================================================
# AUDIT LOGS
# ==================================================

class AuditLogCreate(BaseModel):
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: UUID | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor: str
    action: str
    resource_type: str
    resource_id: UUID | None
    details: dict[str, Any]
    created_at: datetime