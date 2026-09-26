"""Malformed session report rejection.

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, same pattern as `test_ingestion.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from loopback_server import LoopbackServer
from memory_store import InMemoryExecutionStore
from starlette.types import ASGIApp, Receive, Scope, Send
from vantage.service.app import create_app
from vantage.service.errors import MAX_REPORT_BYTES, safe_segment
from vantage.service.routes.sections import MAX_SECTION_BODY_BYTES

# `any_store`, for each adapter in turn.
pytest_plugins = ["store_fixtures"]


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
    """One well-formed `results[]` entry -- mirrors `test_ingestion.py`'s
    helper of the same name and shape."""
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
    deliberately malformed at `malformed_index`."""
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


def test_422_response_never_echoes_input_or_pydantic_types(client: TestClient) -> None:
    """FastAPI's default handler mirrors the client's own value back in an
    ``"input"`` key, and can carry pydantic's internal error ``"type"``
    string and a ``"url"`` pointing at versioned pydantic docs. A report can
    legitimately carry a filesystem path, node id, or environment string,
    and the field that fails validation is exactly the field whose value
    would be echoed, so none of that may reach the response.
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


_REPORT_BYTES = json.dumps(_well_formed_report()).encode()
_UNPARSEABLE_BODIES = {
    "invalid_utf8": _REPORT_BYTES.replace(b"null", b'"\xff"', 1),
    "encoded_surrogate": _REPORT_BYTES.replace(b"null", b'"\xed\xa0\x80"', 1),
    "nesting_deeper_than_the_recursion_limit": b"[" * 200_000,
    "integer_over_the_digit_limit": b'{"run": {"exit_status": ' + b"1" * 5000 + b"}}",
}


@pytest.mark.parametrize("body", _UNPARSEABLE_BODIES.values(), ids=_UNPARSEABLE_BODIES.keys())
def test_every_unparseable_body_is_400_invalid_json(
    client: TestClient, store: InMemoryExecutionStore, body: bytes
) -> None:
    """`json.loads` fails with `UnicodeDecodeError`, `RecursionError` or a
    plain `ValueError`, not only `JSONDecodeError`; each is the same client
    error. A UTF-8-encoded surrogate is not UTF-8 at all, so it is refused
    here rather than decoded into a lone surrogate."""
    response = client.post(
        "/api/v1/runs", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"
    assert store.count_executions() == 0


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_a_non_json_number_token_is_400_invalid_json(
    client: TestClient, store: InMemoryExecutionStore, token: str
) -> None:
    """`json.loads` accepts these, but they are not JSON, and a value no
    JSON response can carry would be stored and then read back as `null`."""
    report = _well_formed_report()
    report["results"] = [_result_entry("tests/test_a.py::test_one")]
    body = json.dumps(report).replace('"call_duration": 0.0019', f'"call_duration": {token}')
    assert token in body

    response = client.post(
        "/api/v1/runs", content=body.encode(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"
    assert store.count_executions() == 0


def test_a_duration_too_large_to_be_finite_is_422_naming_the_field(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """`1e400` is valid JSON, but it parses as infinity, which the server
    could store and never give back."""
    report = _well_formed_report()
    report["results"] = [_result_entry("tests/test_a.py::test_one")]
    body = json.dumps(report).replace('"duration": 0.0031', '"duration": 1e400')
    assert "1e400" in body

    response = client.post(
        "/api/v1/runs", content=body.encode(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["fields"] == ["results.0.duration"]
    assert store.count_executions() == 0


def _report_with(field: str, value: object) -> dict[str, Any]:
    """`_well_formed_report` carrying one result, with `value` at the dotted
    `field` (`run.<name>` or `results.0.<name>`)."""
    report = _well_formed_report()
    report["results"] = [_result_entry("tests/test_a.py::test_one")]
    section, *rest = field.split(".")
    target = report["run"] if section == "run" else report["results"][int(rest.pop(0))]
    target[rest[0]] = value
    return report


_OUT_OF_RANGE_VALUES = {
    "exit_status_above_int64": ("run.exit_status", 2**63),
    "exit_status_below_int64": ("run.exit_status", -(2**63) - 1),
    "failure_lineno_above_int64": ("results.0.failure_lineno", 2**63),
    "started_at_past_year_9999_in_utc": ("run.started_at", "9999-12-31T23:59:59-05:00"),
    "started_at_before_year_1_in_utc": ("run.started_at", "0001-01-01T00:00:00+01:00"),
    "finished_at_past_year_9999_in_utc": ("run.finished_at", "9999-12-31T23:59:59-05:00"),
    "result_started_at_past_year_9999_in_utc": (
        "results.0.started_at",
        "9999-12-31T23:59:59-05:00",
    ),
    "result_finished_at_before_year_1_in_utc": (
        "results.0.finished_at",
        "0001-01-01T00:00:00+01:00",
    ),
}


@pytest.mark.parametrize(
    ("field", "value"), _OUT_OF_RANGE_VALUES.values(), ids=_OUT_OF_RANGE_VALUES.keys()
)
def test_a_value_the_store_cannot_hold_is_422_naming_the_field(
    any_store: Any, field: str, value: object
) -> None:
    """Integers outside SQLite's signed 64-bit range, and timestamps whose
    UTC form leaves years 1-9999, are refused at validation in the shared
    shape -- not a 500 from the store or from the UTC conversion, and the
    same answer from either adapter."""
    client = TestClient(create_app(any_store))

    response = client.post("/api/v1/runs", json=_report_with(field, value))

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_report",
        "detail": "The submitted report does not match the expected shape.",
        "fields": [field],
    }
    assert any_store.count_executions() == 0


def test_oversized_body_is_413(client: TestClient, store: InMemoryExecutionStore) -> None:
    from vantage.service.errors import MAX_REPORT_BYTES

    oversized_report = _well_formed_report()
    oversized_report["run"]["interrupt_reason"] = "x" * (MAX_REPORT_BYTES + 1)

    response = client.post("/api/v1/runs", json=oversized_report)

    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert store.count_executions() == 0


def _post_streamed(app: Any, path: str, chunk: bytes, chunks: int) -> tuple[int | None, int]:
    """POST `chunks` copies of `chunk` straight through the ASGI interface,
    one `http.request` message each, and return the status answered and
    how many chunks the app asked for. `TestClient` cannot show this: it
    reads the whole body before the app sees any of it."""
    asked = 0
    status: int | None = None

    async def receive() -> dict[str, Any]:
        nonlocal asked
        asked += 1
        return {"type": "http.request", "body": chunk, "more_body": asked < chunks}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    return status, asked


@pytest.mark.parametrize(
    ("path", "cap"),
    [("/api/v1/runs", MAX_REPORT_BYTES), ("/api/v1/config/sections", MAX_SECTION_BODY_BYTES)],
    ids=["runs", "sections"],
)
def test_reading_a_body_stops_at_the_chunk_that_crosses_the_cap(
    store: InMemoryExecutionStore, path: str, cap: int
) -> None:
    """The cap bounds memory only if reading stops when it is crossed: a
    body checked once it is complete would buffer everything a client
    streams, gigabytes included, before refusing it."""
    chunk = b" " * 4096

    status, asked = _post_streamed(create_app(store), path, chunk, chunks=10_000)

    assert status == 413
    assert asked == cap // len(chunk) + 1
    assert store.count_executions() == 0
    assert store.list_settings("test_sections") == ()


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


def test_absent_content_type_is_415(client: TestClient, store: InMemoryExecutionStore) -> None:
    import json

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(_well_formed_report()).encode(),
    )

    assert response.status_code == 415
    assert "'<absent>'" in response.json()["detail"]
    assert store.count_executions() == 0


def test_the_415_body_names_a_plain_media_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/runs", content=b"{}", headers={"content-type": "text/plain; charset=utf-8"}
    )

    assert response.status_code == 415
    assert "text/plain" in response.json()["detail"]


def test_the_415_body_never_reflects_the_content_type_header(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """The header is client text like any other: echoed verbatim it would
    carry markup and kilobytes of padding back in the response."""
    marker = "</script><img src=x onerror=alert(1)>"

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(_well_formed_report()).encode(),
        headers={"content-type": marker + "a" * 5000},
    )

    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"
    assert marker not in response.text
    assert len(response.content) < 512
    assert store.count_executions() == 0


@pytest.mark.parametrize(
    ("method", "path", "status", "error"),
    [
        ("PUT", "/api/v1/runs", 405, "method_not_allowed"),
        ("DELETE", "/api/v1/runs", 405, "method_not_allowed"),
        ("GET", f"/api/v1/runs/{'e' * 32}/heartbeat", 405, "method_not_allowed"),
        ("POST", "/runs", 404, "not_found"),
        ("GET", "/api/v1/nonexistent", 404, "not_found"),
        ("POST", "/api/v2/runs", 404, "not_found"),
    ],
)
def test_an_unrouted_request_answers_in_the_rejection_shape(
    client: TestClient, method: str, path: str, status: int, error: str
) -> None:
    """The router's own 404 and 405 are rejections too, so a client parses
    them the same way; a 405 still says which methods the path takes."""
    response = client.request(method, path)

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error", "detail", "fields"}
    assert body["error"] == error
    assert body["fields"] == []
    if status == 405:
        assert "POST" in response.headers["allow"]


@pytest.mark.parametrize("body", [b"null", b"[]", b"42", b'"x"'])
def test_a_body_that_is_not_an_object_names_no_empty_field(client: TestClient, body: bytes) -> None:
    """The failure is the body as a whole, which has no dotted path; an
    empty string in `fields` would name nothing."""
    response = client.post(
        "/api/v1/runs", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_report"
    assert response.json()["fields"] == []


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


def test_forbidden_extra_field_cannot_amplify_the_response(client: TestClient) -> None:
    """A 50 KiB key must not come back as a 50 KiB field path."""
    report = _well_formed_report()
    report["run"]["K" * 50_000] = "x"

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert max(len(field) for field in response.json()["fields"]) < 128


# --- Heartbeat rejection ----------------------------------------------------


def test_heartbeat_for_unknown_run_is_404(client: TestClient) -> None:
    response = client.post(f"/api/v1/runs/{'e' * 32}/heartbeat")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "unknown_run"


@pytest.mark.parametrize(
    ("method", "path", "params", "field"),
    [
        ("POST", "/api/v1/runs/not-a-hex-id/heartbeat", {}, "path.run_id"),
        ("GET", "/api/v1/runs/ABC", {}, "path.run_id"),
        ("GET", "/api/v1/runs", {"limit": 0}, "query.limit"),
        ("GET", "/api/v1/runs", {"offset": -1}, "query.offset"),
        ("GET", f"/api/v1/runs/{'e' * 32}/results", {"limit": "x"}, "query.limit"),
        ("GET", "/api/v1/tests/history", {"node_id": "n", "limit": 0}, "query.limit"),
        ("GET", "/api/v1/runs/zzz/sections", {}, "path.run_id"),
        ("DELETE", "/api/v1/config/sections", {}, "query.name"),
    ],
)
def test_a_bad_path_or_query_parameter_is_422_invalid_parameter(
    client: TestClient, method: str, path: str, params: dict[str, Any], field: str
) -> None:
    """Caught by the parameter's own declaration and rejected through the
    shared `RequestValidationError` handler. None of these requests carries
    a report, so none may be told its report is malformed: a client that
    switches on `error` must tell a bad page parameter from a bad session
    report."""
    response = client.request(method, path, params=params)

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_parameter",
        "detail": "A path or query parameter is not valid.",
        "fields": [field],
    }


# --- Whole-report rejection and atomicity ----------------------------------


def test_one_malformed_result_among_five_hundred_rejects_the_whole_report(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """One malformed entry deep inside a large `results` list rejects the
    entire report, including the 250 valid entries ahead of it. Pydantic
    validates the whole model before the route converts a single entry or
    calls `record_session`, so rejection is never partial."""
    report = _bulk_results_report("2" + "a" * 31, 500, malformed_index=250)

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert store.count_executions() == 0
    assert store.count_results() == 0


def test_duplicate_node_id_inside_one_report_is_422_whole_report(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A duplicate `node_id` inside one report is rejected loudly and
    wholesale, catching a plugin defect before storage's
    `ON CONFLICT ... DO NOTHING` result insert would silently drop it."""
    node_id = "packages/vantage/tests/test_dup.py::test_one"
    report = _well_formed_report("3" + "b" * 31)
    report["results"] = [_result_entry(node_id), _result_entry(node_id, outcome="failed")]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    assert store.count_executions() == 0
    assert store.count_results() == 0


def test_duplicate_node_id_rejection_never_echoes_the_node_id_value(
    client: TestClient,
) -> None:
    """The rejection body names the offending field through the
    `safe_segment` allow-list, and the node id **value** -- which can reveal
    a filesystem path -- is never echoed anywhere in the response."""
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


# --- Raw-socket truncation --------------------------------------------------
#
# Everything above drives `create_app` through `fastapi.testclient.TestClient`,
# whose ASGI transport hands the whole submitted body to `request.stream()`
# already assembled in memory, so it can never observe a socket stopping
# mid-transfer. This section serves the app from a `LoopbackServer`, a real
# `uvicorn` server on a real TCP socket, and drives it byte-for-byte instead.


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


def test_truncated_body_raw_socket(store: InMemoryExecutionStore) -> None:
    """A report truncated in transit stores nothing -- not a prefix of its
    results. Proven against a REAL socket; see the section note above for
    why `TestClient` cannot exercise this.

    A raw client connects, sends a `Content-Length` that promises far more
    bytes than it ever writes, then half-closes its own write direction
    (`shutdown(SHUT_WR)`) -- what a process killed mid-write, or a network
    partition mid-transfer, looks like from the server's side. Starlette's
    `request.stream()` then raises a genuine `ClientDisconnect` from inside
    `_read_bounded_body`'s streaming loop.

    **Why the assertion is "no unhandled-exception log", not "a 400
    response body".** Once uvicorn has observed the peer disconnect, its
    ASGI `send()` silently drops every further message (`h11_impl.py`:
    `if self.disconnected: return`), so no response can reach a client that
    has gone away. What differs is whether the disconnect is handled inside
    this service (clean completion) or propagates out of the ASGI
    application, which makes uvicorn log "Exception in ASGI application"
    with a traceback. The rejection body itself is covered by
    `test_non_json_body_is_400` and its siblings above.
    """
    app = create_app(store)
    signal = _AsgiCompletionSignal(app)
    error_capture = _UvicornErrorCapture()

    with LoopbackServer(signal) as server, error_capture:
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
    # The assertion of record: nothing is written for a truncated session,
    # not a prefix of it.
    assert store.count_executions() == 0
    assert not error_capture.records, (
        "an unhandled ClientDisconnect reached uvicorn's ASGI exception "
        f"wrapper instead of being handled: {error_capture.records}"
    )


def test_finish_report_truncated_after_an_accepted_start_write_leaves_the_start_row_intact(
    store: InMemoryExecutionStore,
) -> None:
    """A finish report truncated in transit after an accepted start-write
    leaves the start row exactly as the start-write wrote it -- not removed
    and not overwritten by a partial finish. `test_truncated_body_raw_socket`
    covers the no-prior-report case, where nothing at all may be stored.
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

    with LoopbackServer(signal) as server, error_capture:
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
