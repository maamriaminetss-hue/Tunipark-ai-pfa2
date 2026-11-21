from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from aiops_platform.database.database import engine
from aiops_platform.api.routers import (
    anomaly_detection,
    audit_logs,
    events,
    incident_events,
    incidents,
    rca,
    service_discovery,
    services,
    topology,
)


app = FastAPI(
    title="Enterprise AI Platform API",
    description="API de gestion et de supervision AIOps.",
    version="0.3.0",
)


# Register API routers
app.include_router(audit_logs.router)
app.include_router(services.router)
app.include_router(incidents.router)
app.include_router(events.router)
app.include_router(incident_events.router)
app.include_router(topology.router)
app.include_router(rca.router)
app.include_router(service_discovery.router)
app.include_router(anomaly_detection.router)


# Health checks
@app.get("/", tags=["Health"])
def root():
    return {
        "application": "Enterprise AI Platform",
        "status": "running",
    }


@app.get("/health", tags=["Health"])
def health_check():
    return {
        "status": "ok",
        "message": "API is running",
    }


@app.get("/health/database", tags=["Health"])
def database_health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "ok",
            "database": "connected",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable",
        ) from exc