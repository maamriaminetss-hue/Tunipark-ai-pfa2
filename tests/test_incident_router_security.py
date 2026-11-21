from uuid import uuid4

import pytest
from fastapi import HTTPException

from aiops_platform.api.routers.incidents import modify_incident
from aiops_platform.api.schemas import IncidentUpdate


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_terminal_statuses_cannot_be_set_directly(status):
    with pytest.raises(HTTPException) as exc_info:
        modify_incident(
            incident_id=uuid4(),
            payload=IncidentUpdate(status=status),
            db=None,
        )

    assert exc_info.value.status_code == 409
    assert "workflow" in exc_info.value.detail.lower()