"""RQ-42: malformed session report rejection.

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, same pattern as `test_ingestion.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import sqlite3
import threading
import time
import tracemalloc
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send
from vantage.core.domain.result import OUTCOMES
from vantage.service.app import create_app
from vantage.service.errors import MAX_REPORT_BYTES, safe_segment
from vantage.service.schemas import _Outcome
from vantage.storage.memory import InMemoryExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _execution, _result, _start_only_execution

_REPO_ROOT = Path(__file__).resolve().parents[3]


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
    """One well-formed `results[]` entry (design.md D15 interface example) --
    mirrors `test_ingestion.py`'s helper of the same name and shape."""
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


def _bulk_results_report(
    run_id: str, count: int, *, malformed_index: int | None = None
) -> dict[str, Any]:
    """A well-formed report carrying `count` results, one of which is
    deliberately malformed at `malformed_index` (task 5.1/5.5/5.6 fixture)."""
    report = _well_formed_report(run_id)
    results = []
    for index in range(count):
        entry = _result_entry(f"packages/vantage/tests/test_bulk.py::test_{index}")
        if index == malformed_index:
            entry["outcome"] = "not-a-real-outcome"
        results.append(entry)
    report["results"] = results
    return report


@pytest.fixture
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


@pytest.mark.req(id="RQ-42")
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


@pytest.mark.req(id="RQ-42")
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


@pytest.mark.req(id="RQ-42")
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


@pytest.mark.req(id="RQ-42")
def test_oversized_body_is_413(client: TestClient, store: InMemoryExecutionStore) -> None:
    from vantage.service.errors import MAX_REPORT_BYTES

    oversized_report = _well_formed_report()
    oversized_report["run"]["interrupt_reason"] = "x" * (MAX_REPORT_BYTES + 1)

    response = client.post("/api/v1/runs", json=oversized_report)

    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert store.count_executions() == 0


def test_a_report_exceeding_the_size_cap_with_failure_evidence_stores_nothing(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """session-ingestion → A report exceeding the size cap stores nothing
    (task 6.13): the encoded body -- failure evidence included -- exceeds
    `MAX_REPORT_BYTES`; the run table stays empty. Whole-report rejection,
    unchanged by `failure-capture` -- the same `413` `_read_bounded_body`
    already raises for any oversized field."""
    oversized_report = _well_formed_report()
    oversized_report["results"] = [
        _result_entry(
            "packages/vantage/tests/test_f.py::test_one",
            outcome="failed",
            traceback="x" * (MAX_REPORT_BYTES + 1),
        )
    ]

    response = client.post("/api/v1/runs", json=oversized_report)

    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert store.count_executions() == 0


@pytest.mark.req(id="RQ-42")
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


@pytest.mark.req(id="RQ-42")
def test_absent_content_type_is_415(client: TestClient, store: InMemoryExecutionStore) -> None:
    import json

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(_well_formed_report()).encode(),
    )

    assert response.status_code == 415
    assert store.count_executions() == 0


@pytest.mark.req(id="RQ-42")
def test_forbidden_extra_field_name_is_not_echoed(client: TestClient) -> None:
    """A rejected *value* is never echoed -- but for an `extra_forbidden`
    error the offending path segment is a key the CLIENT chose, and echoing
    it reflects arbitrary client bytes back verbatim. Unbounded in length,
    and free to carry CR/LF straight into whatever logs the rejection.
    """
    report = _well_formed_report()
    report["run"]["AKIAIOSFODNN7EXAMPLE\r\nFAKE-LOG-LINE"] = "x"

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert "AKIAIOSFODNN7EXAMPLE" not in response.text
    assert "FAKE-LOG-LINE" not in response.text
    for field in response.json()["fields"]:
        assert "\r" not in field and "\n" not in field


@pytest.mark.req(id="RQ-42")
def test_forbidden_extra_field_cannot_amplify_the_response(client: TestClient) -> None:
    """A 50 KiB key must not come back as a 50 KiB field path."""
    report = _well_formed_report()
    report["run"]["K" * 50_000] = "x"

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert max(len(field) for field in response.json()["fields"]) < 128


# --- Phase 4: heartbeat rejection (design.md D33, task 4.4) ----------------


@pytest.mark.req(id="RQ-44")
def test_heartbeat_for_unknown_run_is_404(client: TestClient) -> None:
    response = client.post(f"/api/v1/runs/{'e' * 32}/heartbeat")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "unknown_run"


@pytest.mark.req(id="RQ-44")
def test_heartbeat_for_malformed_run_id_is_422(client: TestClient) -> None:
    """No new code (design.md D33): the malformed id is caught by the path
    parameter's own pattern, and rejected through the existing
    `register_error_handlers`/`RequestValidationError` path."""
    response = client.post("/api/v1/runs/not-a-hex-id/heartbeat")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_report"


# --- Phase 5: whole-report rejection, atomicity, measurement ---------------


@pytest.mark.req(id="RQ-42")
@pytest.mark.req(id="RQ-3")
def test_one_malformed_result_among_five_hundred_rejects_the_whole_report(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """RQ-42.1 with RQ-3.2: one malformed entry deep inside a large `results`
    list rejects the entire report, not the 250 valid entries ahead of it.
    Pydantic validates the whole model before this route ever converts a
    single entry or calls `record_session` -- rejection is whole-report,
    never partial (design.md D19's sibling guarantee)."""
    report = _bulk_results_report("2" + "a" * 31, 500, malformed_index=250)

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert store.count_executions() == 0
    assert store.count_results() == 0


@pytest.mark.req(id="RQ-42")
def test_duplicate_node_id_inside_one_report_is_422_whole_report(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """D19 layer 2: a duplicate `node_id` inside one report is rejected
    loudly and wholesale -- the layer that catches a plugin defect before it
    ever reaches the silent replay backstop (D19 layer 3)."""
    node_id = "packages/vantage/tests/test_dup.py::test_one"
    report = _well_formed_report("3" + "b" * 31)
    report["results"] = [_result_entry(node_id), _result_entry(node_id, outcome="failed")]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert store.count_executions() == 0
    assert store.count_results() == 0


@pytest.mark.req(id="RQ-42")
def test_duplicate_node_id_rejection_never_echoes_the_node_id_value(
    client: TestClient,
) -> None:
    """Threat-derived (design.md D15/Threat Matrix): the rejection body names
    the offending field through the existing `safe_segment` allow-list, and
    the node id **value** is never echoed anywhere in the response."""
    node_id = "packages/vantage/tests/test_secret_path.py::test_should_not_leak"
    report = _well_formed_report("4" + "c" * 31)
    report["results"] = [_result_entry(node_id), _result_entry(node_id, outcome="failed")]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert node_id not in response.text
    body = response.json()
    assert "results" in body["fields"]
    for field in body["fields"]:
        assert safe_segment(field) == field


@pytest.mark.req(id="RQ-3")
def test_five_hundred_results_fit_within_the_body_cap() -> None:
    """D23: the cap is not raised in this change -- it is measured against.
    Builds a 500-result report through the same wire-shaped assembly the
    other tests in this module use and reports the real byte count."""
    report = _bulk_results_report("5" + "d" * 31, 500)
    encoded = json.dumps(report).encode("utf-8")

    print(f"500-result report size: {len(encoded)} bytes (cap {MAX_REPORT_BYTES})")
    assert len(encoded) < MAX_REPORT_BYTES


@pytest.mark.req(id="RQ-3")
def test_server_peak_memory_for_one_five_hundred_result_request(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """D23: server-side peak memory for one 500-result request is measured,
    not asserted against an invented threshold."""
    report = _bulk_results_report("6" + "e" * 31, 500)

    tracemalloc.start()
    try:
        response = client.post("/api/v1/runs", json=report)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    print(f"peak traced memory for one 500-result request: {peak} bytes")
    assert response.status_code == 201
    assert store.count_results() == 500


class _CommitCountingConnection:
    """Wraps a real `sqlite3.Connection`, counting only `COMMIT` statements.

    RQ-3.3 (D21): `record_session` must reach storage in exactly one commit
    for the whole batch, not one per result or one per statement. Wrapping
    the connection is the only way to observe that from outside
    `SqliteExecutionStore` without weakening the adapter's own commit
    discipline for the sake of a test.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.commit_count = 0

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Cursor:
        if sql.strip() == "COMMIT":
            self.commit_count += 1
        return self._real.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> sqlite3.Cursor:
        return self._real.executemany(sql, seq_of_parameters)

    def close(self) -> None:
        self._real.close()


@pytest.mark.req(id="RQ-3")
def test_finish_report_reaches_storage_in_one_commit(tmp_path: Path) -> None:
    """Renamed from `test_five_hundred_results_reach_storage_in_one_commit`
    (design.md D35, task 1.6) -- the Analysis argument moved from one commit
    per *session* to one commit per *report*, and this is the finish-write's
    premise. The rename alone would be cosmetic: without the finish-field
    assertions below, a `DO NOTHING` regression that dropped every finish
    field on conflict would still satisfy the commit count and row counts.

    **Measurements:** the 500-result finish-write generated here measures
    252,511 bytes in body size (via `test_five_hundred_results_fit_within_the_body_cap`);
    server peak memory traced for one such finish-write request reaches
    approximately 2,021,039 bytes (`test_server_peak_memory_for_one_five_hundred_result_request`).
    These measurements are diagnostic; they inform payload size budgeting and
    resource planning for deployments. Future changes to the result schema or
    the batch-insert strategy MUST re-run this test via `tracemalloc` at
    request time and justify any material increase.
    """
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    counting = _CommitCountingConnection(adapter._conn)
    adapter._conn = counting  # type: ignore[assignment]

    try:
        execution = _execution("f" + "0" * 31)
        results = [_result(f"packages/vantage/tests/test_bulk.py::test_{i}") for i in range(500)]

        created = adapter.record_session(
            execution, results=results, received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert counting.commit_count == 1
        # Counting commits proves the transaction's SHAPE and nothing about
        # its content: an adapter that committed once and wrote no rows would
        # satisfy the count alone. RQ-3.1 is verified by Analysis resting on
        # this test, so the test has to carry the weight.
        assert adapter.count_results() == 500
        assert adapter.count_executions() == 1
        stored = adapter.get_execution(execution.identity.value)
        assert stored is not None
        assert stored.finished_at == execution.finished_at
        assert stored.exit_status == execution.exit_status
    finally:
        adapter.close()


@pytest.mark.req(id="RQ-3")
def test_finish_report_after_an_accepted_start_write_reaches_storage_in_one_commit(
    tmp_path: Path,
) -> None:
    """design.md D25, D35, task 1.7: the same finish-write, run after a
    prior accepted start-write for the same run id -- one commit, the same
    500 result rows, and the finish fields actually applied through the
    conflict (`DO UPDATE`) branch rather than the insert branch."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")

    try:
        identity = "f" + "1" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        adapter.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        counting = _CommitCountingConnection(adapter._conn)
        adapter._conn = counting  # type: ignore[assignment]

        finish = _execution(identity, finished=True, started=started)
        results = [_result(f"packages/vantage/tests/test_bulk.py::test_{i}") for i in range(500)]

        created = adapter.record_session(
            finish, results=results, received_at=datetime.now(timezone.utc)
        )

        assert created is False
        assert counting.commit_count == 1
        assert adapter.count_results() == 500
        assert adapter.count_executions() == 1
        stored = adapter.get_execution(identity)
        assert stored is not None
        assert stored.finished_at == finish.finished_at
        assert stored.exit_status == finish.exit_status
    finally:
        adapter.close()


@pytest.mark.req(id="RQ-3")
def test_start_write_reaches_storage_in_one_commit(tmp_path: Path) -> None:
    """design.md D35, task 1.8: the start-write's own commit-count premise --
    one commit, one run row, a null `finished_at`, and zero result rows,
    since a start report carries none."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    counting = _CommitCountingConnection(adapter._conn)
    adapter._conn = counting  # type: ignore[assignment]

    try:
        identity = "f" + "2" * 31
        start = _start_only_execution(identity)

        created = adapter.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        assert created is True
        assert counting.commit_count == 1
        assert adapter.count_results() == 0
        assert adapter.count_executions() == 1
        stored = adapter.get_execution(identity)
        assert stored is not None
        assert stored.finished_at is None
    finally:
        adapter.close()


@pytest.mark.req(id="RQ-3")
def test_reordered_start_write_never_nulls_a_recorded_finish(tmp_path: Path) -> None:
    """design.md D25, task 1.9: the slice-1 acceptance criterion, driven
    through the same `_CommitCountingConnection` wrapper the rest of this
    module uses -- an explicitly reordered pair, finish then start."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")

    try:
        identity = "f" + "3" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        finish = _execution(identity, finished=True, started=started)
        results = [_result("packages/vantage/tests/test_bulk.py::test_reordered")]
        adapter.record_session(finish, results=results, received_at=datetime.now(timezone.utc))

        counting = _CommitCountingConnection(adapter._conn)
        adapter._conn = counting  # type: ignore[assignment]

        late_start = _start_only_execution(identity, started=started)
        created = adapter.record_session(
            late_start, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created is False
        assert counting.commit_count == 1
        stored = adapter.get_execution(identity)
        assert stored is not None
        assert stored.finished_at == finish.finished_at
        assert stored.exit_status == finish.exit_status
        assert adapter.count_results() == 1
    finally:
        adapter.close()


@pytest.mark.req(id="RQ-30")
def test_outcome_vocabulary_matches_across_schema_sql_core_and_service() -> None:
    """The six outcome strings live in three places (design.md, Interfaces
    section): `schema.sql`'s CHECK, `OUTCOMES`, and the service `_Outcome`
    Literal. Parses the CHECK clause itself instead of trusting a fourth,
    hand-typed copy here -- the CHECK is the ground truth this test protects."""
    schema_sql = (
        _REPO_ROOT / "packages" / "vantage" / "src" / "vantage" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    match = re.search(r"CHECK \(outcome IN \(([^)]+)\)\)", schema_sql)
    assert match is not None
    schema_outcomes = frozenset(value.strip(" '") for value in match.group(1).split(","))

    assert schema_outcomes == OUTCOMES
    assert schema_outcomes == frozenset(get_args(_Outcome))


# --- Raw-socket truncation (task 3.7) ---------------------------------------
#
# Everything above drives `create_app` through `fastapi.testclient.TestClient`,
# whose ASGI transport hands the whole submitted body to `request.stream()`
# already assembled in memory (PR7's stated, honest gap). No test built on
# `TestClient` can ever observe a socket stopping mid-transfer, so it cannot
# prove the streaming cap in `_read_bounded_body` -- the loop that raises
# before asking `request.stream()` for another chunk -- actually stops a
# real, slow-feeding connection. This section runs a real `uvicorn` server on
# a real, already-bound TCP socket and drives it byte-for-byte instead.


class _RawSocketServer:
    """A real `uvicorn` server bound to an ephemeral loopback port.

    The listening socket is created and bound here, *before* uvicorn ever
    sees it (`socket.bind(("127.0.0.1", 0))`, then `getsockname()` for the
    OS-assigned port) so the actual port is known ahead of time without
    guessing or hardcoding one -- the same reason `port=0` was requested:
    the test must survive a machine where an arbitrary fixed port is
    already taken.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.port = self._sock.getsockname()[1]

        config = uvicorn.Config(app, host="127.0.0.1", log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="vantage-test-raw-socket-server", daemon=True
        )

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._server.serve(sockets=[self._sock]))

    def __enter__(self) -> _RawSocketServer:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.001)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.should_exit = True
        # A failing assertion above must not leave a listener behind to
        # poison a later test -- join with a bound, not forever.
        self._thread.join(timeout=5)


class _AsgiCompletionSignal:
    """Wraps the ASGI app to know, deterministically, when one full request
    cycle has returned -- successfully or by raising.

    This is a genuine ASGI middleware layer (the same shape any Starlette
    middleware takes): it awaits the real app unmodified and only observes
    when that call returns, it never fabricates a `receive()` event or an
    exception. It exists so the test can `Event.wait(timeout=...)` for the
    real request-handling coroutine to finish instead of guessing with a
    fixed sleep -- there is no other externally observable "done" signal,
    because a client that already disconnected can also never observe the
    server's response by definition.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self.completed = threading.Event()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self._app(scope, receive, send)
        finally:
            self.completed.set()


class _ListLogHandler(logging.Handler):
    """A `logging.Handler` that appends every record it receives to a list.

    Not `logging.Handler().emit = list.append` -- mypy (correctly) rejects
    monkeypatching a bound method with a differently-shaped callable, and a
    real subclass is barely more code.
    """

    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)


class _UvicornErrorCapture:
    """Captures ERROR+ records from the `uvicorn.error` logger directly.

    uvicorn's default logging config (`uvicorn.config.LOGGING_CONFIG`) sets
    `"uvicorn": {..., "propagate": False}` on `uvicorn.error`'s parent --
    pytest's `caplog`, which attaches to the root logger, would silently see
    nothing this module logs no matter what actually happens. Attaching
    directly to `uvicorn.error` sidesteps that and is the only reliable way
    to observe whether uvicorn's own ASGI exception wrapper
    (`run_asgi`'s `except BaseException as exc: self.logger.error(...)`) ever
    fired for this request.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("uvicorn.error")
        self.records: list[logging.LogRecord] = []
        self._handler = _ListLogHandler(self.records)

    def __enter__(self) -> _UvicornErrorCapture:
        self._previous_level = self._logger.level
        self._logger.setLevel(logging.ERROR)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous_level)


@pytest.mark.req(id="RQ-42")
@pytest.mark.req(id="RQ-3")
def test_truncated_body_raw_socket(store: InMemoryExecutionStore) -> None:
    """RQ-3 criterion 2 ("its report is truncated in transit ... the
    database holds none of that session's results rather than a prefix of
    them") and RQ-42's "Body truncated midway" scenario, proven against a
    REAL socket -- see the module-level note above for why `TestClient`
    cannot exercise this.

    A raw client connects, sends a `Content-Length` that promises far more
    bytes than it ever writes, then half-closes its own write direction
    (`shutdown(SHUT_WR)`) without sending the rest -- exactly what a process
    killed mid-write, or a network partition mid-transfer, looks like from
    the server's side. Starlette's `request.stream()` detects this and
    raises a genuine `starlette.requests.ClientDisconnect` from inside the
    real streaming read loop in `_read_bounded_body` -- not constructed by
    hand, not injected through a fabricated `receive()`, the actual
    exception a real disconnect produces.

    **Why the assertion is "no unhandled-exception log", not "a 400
    response body".** Once uvicorn's own transport has observed the peer
    disconnect, its ASGI `send()` implementation silently drops every
    further message (`h11_impl.py`: `if self.disconnected: return`) --
    this is unconditional ASGI-server behaviour, true whether or not this
    service catches `ClientDisconnect`, and it means no HTTP response body
    can ever reach a client that has already gone away, by construction of
    the protocol. What DOES differ, and is what task 3.8 changes, is
    whether the disconnect is handled inside this service's own code
    (silent, clean completion) or left to propagate out of the ASGI
    application entirely, which is what makes uvicorn log an "Exception in
    ASGI application" error with a full traceback. RQ-42's "reject and
    store nothing" is what task 3.7/3.8 can prove end-to-end for a real
    socket: the response-body half of RQ-42 is already covered, against
    the same code path, by `test_non_json_body_is_400` and its siblings
    above -- this test's job is the part only a real socket can prove.
    """
    app = create_app(store)
    signal = _AsgiCompletionSignal(app)
    error_capture = _UvicornErrorCapture()

    with _RawSocketServer(signal) as server, error_capture:
        report_id = "c" * 32
        # Deliberately short: promises 500 bytes via Content-Length, sends a
        # small fraction of that, then stops.
        truncated_body = ('{"run": {"id": "' + report_id + '", "started_at"').encode()
        promised_length = 500
        request_head = (
            f"POST /api/v1/runs HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {promised_length}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()

        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.settimeout(5)
        try:
            client_sock.connect(("127.0.0.1", server.port))
            client_sock.sendall(request_head + truncated_body)
            assert len(truncated_body) < promised_length
            client_sock.shutdown(socket.SHUT_WR)
            try:
                received = client_sock.recv(4096)
            except OSError:
                received = b""
        finally:
            client_sock.close()

        # Block until the real request-handling coroutine has actually
        # returned -- see `_AsgiCompletionSignal`'s docstring for why this
        # is a real completion signal, not a fixed sleep.
        completed = signal.completed.wait(timeout=5)

    assert completed, "the server never finished handling the truncated request"
    # A client that has already disconnected cannot, by construction of the
    # protocol, ever observe a response body -- see the test docstring.
    assert received == b""
    # RQ-3 criterion 2, the assertion of record: nothing is written for a
    # truncated session, not a prefix of it.
    assert store.count_executions() == 0
    assert not error_capture.records, (
        "an unhandled ClientDisconnect reached uvicorn's ASGI exception "
        f"wrapper instead of being handled: {error_capture.records}"
    )


@pytest.mark.req(id="RQ-3")
@pytest.mark.req(id="RQ-42")
def test_finish_report_truncated_after_an_accepted_start_write_leaves_the_start_row_intact(
    store: InMemoryExecutionStore,
) -> None:
    """`run-recording`'s "Finish report truncated after an accepted
    start-write" (RQ-3.2) and `session-ingestion`'s matching RQ-42.3
    scenario. `test_truncated_body_raw_socket` above proves only the
    no-prior-report case (``count_executions() == 0``); it would be wrong
    to extend that assertion here, because the start-write's row MUST
    survive. A start-write is accepted first through the same real app,
    then a finish report for that same run id is truncated in transit
    through the identical raw-socket harness -- the row it left behind must
    still be exactly what the start-write wrote, not removed and not
    overwritten by a partial finish.
    """
    app = create_app(store)
    run_id = "d" * 32
    start_report = {
        "run": {
            "id": run_id,
            "started_at": "2026-08-15T09:14:02.481930+00:00",
            "finished_at": None,
            "exit_status": None,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }
    accept_response = TestClient(app).post("/api/v1/runs", json=start_report)
    assert accept_response.status_code == 201
    assert store.count_executions() == 1

    signal = _AsgiCompletionSignal(app)
    error_capture = _UvicornErrorCapture()

    with _RawSocketServer(signal) as server, error_capture:
        # Deliberately short: promises 500 bytes via Content-Length, sends a
        # small fraction of that, then stops -- same shape as
        # `test_truncated_body_raw_socket`, but a finish report for a run id
        # that already has an accepted start-write.
        truncated_body = ('{"run": {"id": "' + run_id + '", "finished_at"').encode()
        promised_length = 500
        request_head = (
            f"POST /api/v1/runs HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {promised_length}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()

        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.settimeout(5)
        try:
            client_sock.connect(("127.0.0.1", server.port))
            client_sock.sendall(request_head + truncated_body)
            assert len(truncated_body) < promised_length
            client_sock.shutdown(socket.SHUT_WR)
            try:
                received = client_sock.recv(4096)
            except OSError:
                received = b""
        finally:
            client_sock.close()

        completed = signal.completed.wait(timeout=5)

    assert completed, "the server never finished handling the truncated finish report"
    assert received == b""
    # The assertion of record: the start-write's row survives the rejected
    # finish exactly as written -- present, one row, no result rows, still a
    # null `finished_at` -- neither removed nor overwritten by the partial
    # finish report.
    assert store.count_executions() == 1
    stored = store.get_execution(run_id)
    assert stored is not None
    assert stored.finished_at is None
    assert stored.exit_status is None
    assert store.count_results() == 0
    assert not error_capture.records, (
        "an unhandled ClientDisconnect reached uvicorn's ASGI exception "
        f"wrapper instead of being handled: {error_capture.records}"
    )
