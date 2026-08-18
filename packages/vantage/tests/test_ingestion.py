"""RQ-41: session report ingestion -- happy path and idempotent replay.

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, per design.md's ordering note: B does not depend on
A2 (schema/SQLite adapter, PR2-PR5). Wiring the real `SqliteExecutionStore`
in here would reintroduce a dependency this slice is explicitly free of.
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


def _result_entry(node_id: str, **overrides: Any) -> dict[str, Any]:
    """One well-formed `results[]` entry (design.md D15 interface example).

    `overrides` lets a test carry an extra, undeclared key (task 4.4) or
    override a single field (task 4.5's `param_id`/`duration`) without
    repeating every other field.
    """
    entry: dict[str, Any] = {
        "node_id": node_id,
        "file_path": node_id.split("::", 1)[0],
        "class_name": None,
        "function_name": node_id.rsplit("::", 1)[-1],
        "param_id": None,
        "outcome": "passed",
        "duration": 0.0031,
        "started_at": "2026-08-18T09:14:02.481930+00:00",
        "finished_at": "2026-08-18T09:14:02.485012+00:00",
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "setup_duration": 0.0008,
        "call_duration": 0.0019,
        "teardown_duration": 0.0004,
        "worker_id": None,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


@pytest.mark.req("RQ-41")
def test_well_formed_report_is_stored_and_acknowledged(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    response = client.post("/api/v1/runs", json=_well_formed_report("a" * 32))

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] == "a" * 32
    assert body["status"] == "created"
    assert store.count_executions() == 1
    assert store.get_execution("a" * 32) is not None


@pytest.mark.req("RQ-41")
def test_retried_report_is_idempotent(client: TestClient, store: InMemoryExecutionStore) -> None:
    report = _well_formed_report("b" * 32)

    first = client.post("/api/v1/runs", json=report)
    second = client.post("/api/v1/runs", json=report)

    assert first.status_code == 201
    assert second.status_code == 200
    body = second.json()
    assert body["run_id"] == "b" * 32
    assert body["status"] == "duplicate"
    assert store.count_executions() == 1


@pytest.mark.req("RQ-41")
@pytest.mark.parametrize("path", ["/runs", "/api/runs"])
def test_unversioned_path_is_refused(client: TestClient, path: str) -> None:
    response = client.post(path, json=_well_formed_report())

    assert response.status_code == 404


@pytest.mark.req("RQ-41")
def test_report_carrying_results_stores_them_with_the_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """RQ-41.1: a report carrying N results stores N result rows with the run.

    Also proves the results survive the round trip, not just their count --
    two entries with different outcomes read back with the outcomes they
    were sent with.
    """
    report = _well_formed_report("c" * 32)
    report["results"] = [
        _result_entry("packages/vantage/tests/test_a.py::test_one"),
        _result_entry("packages/vantage/tests/test_a.py::test_two", outcome="failed"),
    ]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.count_executions() == 1
    assert store.count_results() == 2
    stored = {result.identity.node_id: result for result in store.get_results("c" * 32)}
    assert stored["packages/vantage/tests/test_a.py::test_one"].outcome == "passed"
    assert stored["packages/vantage/tests/test_a.py::test_two"].outcome == "failed"


@pytest.mark.req("RQ-41")
@pytest.mark.parametrize("results_value", [None, []], ids=["null", "empty-list"])
def test_report_with_null_or_empty_results_section_writes_no_result_rows(
    client: TestClient, store: InMemoryExecutionStore, results_value: list[Any] | None
) -> None:
    """RQ-41.1, D15: `results: null` and `results: []` both record the run
    and write zero result rows -- the supported plugin/server skew case, not
    an error.
    """
    run_id = "d" * 32 if results_value is None else "d" * 31 + "e"
    report = _well_formed_report(run_id)
    report["results"] = results_value

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.count_executions() == 1
    assert store.count_results() == 0


@pytest.mark.req("RQ-41")
@pytest.mark.parametrize("results_value", [None, []], ids=["null", "empty-list"])
def test_session_report_accepts_a_null_or_empty_results_section(
    results_value: list[Any] | None,
) -> None:
    """The schema-level RED for the same scenario: `SessionReport.results`
    must exist and round-trip the sent value, rather than being silently
    dropped by the envelope's `extra="ignore"` (which is what would happen
    today, before `results` is a declared field)."""
    from vantage.service.schemas import SessionReport

    payload = SessionReport.model_validate({**_well_formed_report(), "results": results_value})

    assert payload.results == results_value


@pytest.mark.req("RQ-41")
def test_replayed_report_with_results_does_not_duplicate_them(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """RQ-41.2, D19 layer 3: replaying an already-stored report leaves the
    same result rows in place and is acknowledged, never rejected."""
    report = _well_formed_report("f" * 32)
    report["results"] = [_result_entry("packages/vantage/tests/test_b.py::test_one")]

    first = client.post("/api/v1/runs", json=report)
    second = client.post("/api/v1/runs", json=report)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert store.count_results() == 1


@pytest.mark.req("RQ-41")
def test_unknown_result_key_is_tolerated_and_named_deduplicated_in_ignored(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """D15: an unknown key on a *result* is tolerated (`extra="allow"`), and
    its **name** is reported back deduplicated as `results[].<name>` -- one
    entry for two results carrying the same unknown key, never a per-index
    path."""
    report = _well_formed_report("1" + "a" * 31)
    report["results"] = [
        _result_entry("packages/vantage/tests/test_c.py::test_one", tags=["slow"]),
        _result_entry("packages/vantage/tests/test_c.py::test_two", tags=["slow"]),
    ]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    body = response.json()
    assert body["ignored"] == ["results[].tags"]
    assert store.count_results() == 2


@pytest.mark.req("RQ-9")
def test_result_report_param_id_and_duration_survive_the_pydantic_hop() -> None:
    """The Pydantic hop of the four-hop `""`-vs-`None` guard (design.md D18):
    `param_id: ""` and `param_id: null` on the wire must arrive as distinct
    Python values. No `min_length=1`, no falsy-to-`None` coercion -- the same
    rule applies to a duration of `0.0`, which must survive as `0.0`, not be
    coerced to `None`."""
    from vantage.service.schemas import ResultReport

    empty_param = ResultReport.model_validate(
        _result_entry("packages/vantage/tests/test_result.py::test_x[]", param_id="")
    )
    absent_param = ResultReport.model_validate(
        _result_entry("packages/vantage/tests/test_result.py::test_y", param_id=None)
    )
    zero_duration = ResultReport.model_validate(
        _result_entry("packages/vantage/tests/test_result.py::test_z", duration=0.0)
    )

    assert empty_param.param_id == ""
    assert absent_param.param_id is None
    assert empty_param.param_id != absent_param.param_id
    assert zero_duration.duration == 0.0
