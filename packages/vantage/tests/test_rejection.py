"""RQ-42: malformed session report rejection.

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, same pattern as `test_ingestion.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore


def _well_formed_report(run_id: str = "a" * 32) -> dict[str, Any]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-08-15T09:14:02.481930+00:00",
            "finished_at": "2026-08-15T09:14:47.002118+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }


@pytest.fixture
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


@pytest.mark.req("RQ-42")
def test_422_response_never_echoes_input_or_pydantic_types(client: TestClient) -> None:
    """FastAPI's default handler mirrors the client's own value back in an
    ``"input"`` key, and can carry pydantic's internal error ``"type"``
    string and a ``"url"`` pointing at versioned pydantic docs (design.md
    D5). A report can legitimately carry a filesystem path, node id, or
    environment string; the field that fails validation is exactly the
    field whose value would be echoed -- that is what RQ-42.4 exists to
    stop.
    """
    report = _well_formed_report()
    report["run"]["started_at"] = "NOT-A-DATE"

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    body_text = response.text

    # The client's own submitted value must not be mirrored back, whether
    # under "input", inside "ctx", or anywhere else in the body.
    assert "NOT-A-DATE" not in body_text
    assert '"input"' not in body_text
    assert '"ctx"' not in body_text
    assert '"url"' not in body_text
    assert "ValidationError" not in body_text
    assert "Traceback" not in body_text
    assert "pydantic" not in body_text.lower()
