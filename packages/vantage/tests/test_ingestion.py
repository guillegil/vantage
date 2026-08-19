"""RQ-41: session report ingestion -- happy path and idempotent replay.

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, per design.md's ordering note: B does not depend on
A2 (schema/SQLite adapter, PR2-PR5). Wiring the real `SqliteExecutionStore`
in here would reintroduce a dependency this slice is explicitly free of.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore


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


@pytest.fixture
def sqlite_store(tmp_path: Path) -> Iterator[SqliteExecutionStore]:
    """The real SQLite adapter (tasks 5.9/5.10): the D20 `MAX` monotonicity
    guard is a lexicographic comparison of stored TEXT, so only this adapter
    -- not `InMemoryExecutionStore`, which compares real `datetime` objects
    and is already correct -- can reproduce the un-normalized-timestamp bug
    (Engram observation 62)."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    yield adapter
    adapter.close()


@pytest.fixture
def sqlite_client(sqlite_store: SqliteExecutionStore) -> TestClient:
    return TestClient(create_app(sqlite_store))


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
@pytest.mark.parametrize("path", ["/runs", "/api/runs"])
def test_unversioned_path_is_refused(client: TestClient, path: str) -> None:
    response = client.post(path, json=_well_formed_report())

    assert response.status_code == 404


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-9")
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


@pytest.mark.req(id="RQ-13")
def test_an_older_run_with_a_non_utc_offset_does_not_roll_back_the_catalogue(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """Reproduces the Phase 3 finding (Engram observation 62): `last_seen_at`
    is TEXT and D20's `MAX` guard compares it lexicographically, which is
    only correct once every writer normalizes to the same UTC offset. Before
    the boundary normalizes, `'...T12:00:00+02:00'` (10:00 UTC, genuinely
    earlier) sorts AFTER `'...T11:00:00+00:00'` (11:00 UTC) as a string and
    rolls the catalogue forward -- exactly what D20 exists to prevent."""
    node_id = "packages/vantage/tests/test_utc.py::test_guard"

    first_report = _well_formed_report("7" * 32)
    first_report["run"]["started_at"] = "2026-08-18T11:00:00+00:00"  # 11:00 UTC
    first_report["results"] = [_result_entry(node_id)]
    sqlite_client.post("/api/v1/runs", json=first_report)

    second_report = _well_formed_report("8" * 32)
    second_report["run"]["started_at"] = "2026-08-18T12:00:00+02:00"  # 10:00 UTC, earlier
    second_report["results"] = [_result_entry(node_id)]
    sqlite_client.post("/api/v1/runs", json=second_report)

    entry = sqlite_store.get_catalogue_entry(node_id)
    assert entry is not None
    assert entry.last_seen_at == datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    assert entry.last_seen_run_id == "7" * 32


@pytest.mark.req(id="RQ-30")
def test_non_utc_and_naive_timestamps_normalize_to_one_utc_form(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """D-addendum (2026-08-18): one helper normalizes every timestamp before
    it reaches the store -- `run.started_at`/`finished_at` and a result's
    `started_at`/`finished_at` alike, proven in one test on purpose so the
    two are never allowed to diverge onto separate paths. An aware value
    converts to its UTC equivalent; a naive value is interpreted as UTC."""
    node_id = "packages/vantage/tests/test_utc.py::test_normalizes"
    report = _well_formed_report("9" * 32)
    report["run"]["started_at"] = "2026-08-18T13:00:00+02:00"  # 11:00 UTC
    report["run"]["finished_at"] = "2026-08-18T13:00:05"  # naive -- interpreted as UTC
    report["results"] = [
        _result_entry(
            node_id,
            started_at="2026-08-18T13:00:01+02:00",  # 11:00:01 UTC
            finished_at="2026-08-18T13:00:02",  # naive -- interpreted as UTC
        )
    ]

    response = sqlite_client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    raw_started_at = sqlite_store._conn.execute(
        "SELECT started_at FROM run WHERE id = ?", ("9" * 32,)
    ).fetchone()[0]
    assert raw_started_at == "2026-08-18T11:00:00+00:00"

    execution = sqlite_store.get_execution("9" * 32)
    assert execution is not None
    assert execution.started_at == datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    assert execution.finished_at == datetime(2026, 8, 18, 13, 0, 5, tzinfo=timezone.utc)

    [result] = sqlite_store.get_results("9" * 32)
    assert result.started_at == datetime(2026, 8, 18, 11, 0, 1, tzinfo=timezone.utc)
    assert result.finished_at == datetime(2026, 8, 18, 13, 0, 2, tzinfo=timezone.utc)


# --- Phase 4: heartbeat endpoint (design.md D33, task 4.2/4.3) --------------


@pytest.mark.req(id="RQ-44")
def test_heartbeat_advances_last_contact_for_an_accepted_start_write(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    report = _well_formed_report("2" + "a" * 31)
    report["run"]["finished_at"] = None
    report["run"]["exit_status"] = None
    client.post("/api/v1/runs", json=report)
    run_id = report["run"]["id"]
    before = store._last_contact[run_id]  # noqa: SLF001

    response = client.post(f"/api/v1/runs/{run_id}/heartbeat")

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "status": "acknowledged"}
    assert store._last_contact[run_id] > before  # noqa: SLF001


@pytest.mark.req(id="RQ-44")
def test_heartbeat_cannot_touch_finish_fields(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """design.md D33, task 4.3: a heartbeat for a run whose finish is already
    recorded leaves `finished_at`, `exit_status`, `interrupted` and
    `interrupt_reason` exactly as recorded -- the body is `{}` and read by
    nothing, so there is no field to smuggle a change through."""
    report = _well_formed_report("3" + "a" * 31)
    client.post("/api/v1/runs", json=report)
    run_id = report["run"]["id"]
    before = store.get_execution(run_id)
    assert before is not None

    response = client.post(f"/api/v1/runs/{run_id}/heartbeat")

    assert response.status_code == 200
    after = store.get_execution(run_id)
    assert after is not None
    assert after.finished_at == before.finished_at
    assert after.exit_status == before.exit_status
    assert after.interrupted == before.interrupted
    assert after.interrupt_reason == before.interrupt_reason


@pytest.mark.req(id="RQ-44")
def test_heartbeat_for_a_known_run_with_a_later_recorded_contact_is_200_not_404(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """W1: the 404 is resolved by `get_execution`, never by a zero-rowcount
    update -- the one case that distinguishes the two implementations is a
    *known* run whose stored `last_contact_at` is already ahead of this
    beat, so `touch_last_contact` itself returns `False`. Every other
    heartbeat test's incoming beat is strictly later than the stored
    contact, so `rowcount` would also be 1 there and could not catch a
    regression to the wrong implementation. Setting `_last_contact` directly
    into the future, rather than issuing two real heartbeats, is what
    guarantees the beat under test is provably earlier without a timing race.
    """
    report = _well_formed_report("4" + "a" * 31)
    report["run"]["finished_at"] = None
    report["run"]["exit_status"] = None
    client.post("/api/v1/runs", json=report)
    run_id = report["run"]["id"]
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    store._last_contact[run_id] = far_future  # noqa: SLF001

    response = client.post(f"/api/v1/runs/{run_id}/heartbeat")

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "status": "acknowledged"}
    # The monotonic guard rejected the earlier beat: the stored contact is
    # unchanged, yet the response is still 200 -- a rowcount-based 404 would
    # have answered 404 here.
    assert store._last_contact[run_id] == far_future  # noqa: SLF001


# --- Phase 4: `app.state.grace_period` (design.md D34, task 4.14) ----------


def test_create_app_defaults_grace_period_to_900_seconds() -> None:
    app = create_app(InMemoryExecutionStore())

    assert app.state.grace_period == 900.0


def test_create_app_exposes_the_configured_grace_period() -> None:
    app = create_app(InMemoryExecutionStore(), grace_period_seconds=123.0)

    assert app.state.grace_period == 123.0


# --- Capability advertisement (design decisions D38-D40, tasks 1.1/1.2) -----


def test_capabilities_endpoint_advertises_the_session_lifecycle(client: TestClient) -> None:
    """D38: the server advertises exactly one capability, not a version
    string -- `GET /api/v1/capabilities` answers `{"session_lifecycle":
    true}`, the one explicit positive answer a client's fail-closed check
    (D40) may treat as permission."""
    response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {"session_lifecycle": True}


def test_capabilities_endpoint_is_not_mounted_unversioned(client: TestClient) -> None:
    """Mounted under `/api/v1` and nowhere else -- the same absence rule
    `app.py`'s own docstring already states for the run routes (RQ-41's
    third criterion)."""
    response = client.get("/capabilities")

    assert response.status_code == 404
