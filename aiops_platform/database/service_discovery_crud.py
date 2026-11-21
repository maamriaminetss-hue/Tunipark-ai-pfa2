"""Persistence operations for source-specific service observations."""

from collections.abc import Iterable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from aiops_platform.database.models import ServiceDiscovery


ALLOWED_DISCOVERY_SOURCES = frozenset({"jaeger", "prometheus"})


def upsert_service_discoveries(
    db: Session,
    source: str,
    service_names: Iterable[str],
) -> int:
    """Insert or refresh observations for one successfully queried source.

    The function deliberately never deletes rows or changes ``app.services``.
    Each source is committed independently by the caller, so a failure from
    another discovery source cannot invalidate this source's snapshot.
    """
    if source not in ALLOWED_DISCOVERY_SOURCES:
        raise ValueError(
            f"Unsupported discovery source: {source!r}. "
            f"Expected one of {sorted(ALLOWED_DISCOVERY_SOURCES)}."
        )

    normalized_names = sorted(
        {
            name.strip()
            for name in service_names
            if isinstance(name, str) and name.strip()
        }
    )
    if not normalized_names:
        return 0

    rows = [
        {
            "source": source,
            "service_name": service_name,
        }
        for service_name in normalized_names
    ]

    statement = postgres_insert(ServiceDiscovery).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_service_discoveries_source_name",
        set_={
            "last_seen_at": func.now(),
            "last_sync_at": func.now(),
        },
    )

    try:
        result = db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise

    rowcount = getattr(result, "rowcount", None)
    return int(rowcount) if rowcount is not None and rowcount >= 0 else len(rows)
