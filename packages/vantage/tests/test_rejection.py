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


@pytest.mark.req("RQ-42")
def test_missing_field_is_422_naming_the_field(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    report = _well_formed_report()
    del report["run"]["started_at"]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_report"
    assert "run.started_at" in body["fields"]
    assert store.count_executions() == 0


@pytest.mark.req("RQ-42")
def test_non_json_body_is_400(client: TestClient, store: InMemoryExecutionStore) -> None:
    response = client.post(
        "/api/v1/runs",
        content=b"{not json at all",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_json"
    assert store.count_executions() == 0


@pytest.mark.req("RQ-42")
def test_oversized_body_is_413(client: TestClient, store: InMemoryExecutionStore) -> None:
    from vantage.service.errors import MAX_REPORT_BYTES

    oversized_report = _well_formed_report()
    oversized_report["run"]["interrupt_reason"] = "x" * (MAX_REPORT_BYTES + 1)

    response = client.post("/api/v1/runs", json=oversized_report)

    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert store.count_executions() == 0


@pytest.mark.req("RQ-42")
def test_wrong_content_type_is_415(client: TestClient, store: InMemoryExecutionStore) -> None:
    import json

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(_well_formed_report()).encode(),
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 415
    body = response.json()
    assert body["error"] == "unsupported_media_type"
    assert store.count_executions() == 0


@pytest.mark.req("RQ-42")
def test_absent_content_type_is_415(client: TestClient, store: InMemoryExecutionStore) -> None:
    import json

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(_well_formed_report()).encode(),
    )

    assert response.status_code == 415
    assert store.count_executions() == 0
