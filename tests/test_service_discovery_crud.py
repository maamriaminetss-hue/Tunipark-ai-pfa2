from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql

from aiops_platform.database.service_discovery_crud import (
    upsert_service_discoveries,
)


class FakeSession:
    def __init__(self):
        self.statement = None
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        self.statement = statement
        return Mock(rowcount=2)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_upsert_normalizes_names_and_uses_postgresql_conflict_handling():
    db = FakeSession()

    written = upsert_service_discoveries(
        db,
        "jaeger",
        ["checkout", " frontend ", "checkout", "", None],
    )

    assert written == 2
    assert db.commits == 1
    assert db.rollbacks == 0

    compiled = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT uq_service_discoveries_source_name" in compiled
    assert "last_seen_at" in compiled
    assert "last_sync_at" in compiled


def test_upsert_with_empty_snapshot_does_not_write_or_commit():
    db = FakeSession()

    assert upsert_service_discoveries(db, "prometheus", []) == 0
    assert db.statement is None
    assert db.commits == 0


def test_upsert_rejects_unknown_source():
    db = FakeSession()

    with pytest.raises(ValueError, match="Unsupported discovery source"):
        upsert_service_discoveries(db, "unknown", ["checkout"])

    assert db.statement is None


def test_upsert_rolls_back_on_database_error():
    db = FakeSession()
    db.execute = Mock(side_effect=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        upsert_service_discoveries(db, "jaeger", ["checkout"])

    assert db.commits == 0
    assert db.rollbacks == 1
