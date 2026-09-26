"""Concurrent writers on one database file never corrupt or drop each
other's writes, a thread never reads another's write half-done, and a slow
store call never holds up an unrelated request.

Every thread is joined with a timeout: a deadlock is exactly what these tests
look for, and an unbounded `join` would hang the suite instead of failing it.
For the same reason every thread is a daemon: a thread still stuck when its
test has failed must not keep the interpreter from exiting afterwards.
"""

from __future__ import annotations

import contextlib
import sqlite3
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.result import Result
from vantage.core.domain.sections import MAX_SECTIONS
from vantage.core.ports.storage import EMPTY_RUN_METADATA, ExecutionStore, RunMetadata
from vantage.service.app import create_app
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage.storage import sqlite_store
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _result, _start_only_execution

# `any_store`, for each adapter in turn.
pytest_plugins = ["store_fixtures"]

_JOIN_TIMEOUT_SECONDS = 10


def _execution(hex_id: str) -> Execution:
    started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    return Execution(
        identity=Identity(hex_id),
        started_at=started,
        finished_at=started + timedelta(seconds=5),
        exit_status=0,
        interrupted=False,
        interrupt_reason=None,
    )


def _run_concurrently(targets: list[Callable[[], object]]) -> list[BaseException]:
    """Start every target at once, behind a barrier, and return whatever they
    raised. Fails if any thread is still running after the timeout."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def _wrap(target: Callable[[], object]) -> Callable[[], None]:
        def _run() -> None:
            try:
                barrier.wait()
                target()
            except BaseException as exc:  # noqa: BLE001 -- collected for the caller's assertion
                errors.append(exc)

        return _run

    threads = [threading.Thread(target=_wrap(target), daemon=True) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not any(thread.is_alive() for thread in threads), "a thread never finished"
    return errors


def test_two_concurrent_sessions_both_leave_a_run_entry(tmp_path: Path) -> None:
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        ids = ["a" * 32, "b" * 32]
        created: dict[str, bool] = {}

        def _report(hex_id: str) -> None:
            created[hex_id] = store.record_session(
                _execution(hex_id), results=(), received_at=datetime.now(timezone.utc)
            )

        errors = _run_concurrently([partial(_report, hex_id) for hex_id in ids])

        assert errors == []
        assert store.count_executions() == 2
        assert created == {"a" * 32: True, "b" * 32: True}
        stored_ids = {store.get_execution(hex_id).identity.value for hex_id in ids}  # type: ignore[union-attr]
        assert stored_ids == set(ids)
    finally:
        store.close()


def test_two_concurrent_two_hundred_test_sessions_leave_four_hundred_results(
    tmp_path: Path,
) -> None:
    """Two sessions, each reporting 200 results, racing through the store's
    `threading.Lock` and `BEGIN IMMEDIATE` transaction, leave exactly 400
    result rows -- neither batch clobbers or drops rows from the other.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        sessions = {"a" * 32: "x", "b" * 32: "y"}

        def _report(hex_id: str, prefix: str) -> None:
            results = [_result(f"t.py::test_{prefix}_{i}") for i in range(200)]
            store.record_session(
                _execution(hex_id), results=results, received_at=datetime.now(timezone.utc)
            )

        errors = _run_concurrently(
            [partial(_report, hex_id, prefix) for hex_id, prefix in sessions.items()]
        )

        assert errors == []
        assert store.count_results() == 400
    finally:
        store.close()


def test_ten_simultaneous_sessions_leave_ten_run_entries_and_raise_nothing(
    tmp_path: Path,
) -> None:
    """Ten sessions reporting at once each leave their own run entry and
    none raises -- the store's lock serialises the ten transactions rather
    than letting any of them fail.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        # Single hex digits (0-9) are already valid lowercase hex, so
        # `str(i) * 32` yields ten distinct, valid 32-char identities.
        session_ids = [str(i) * 32 for i in range(10)]

        def _report(hex_id: str) -> None:
            store.record_session(
                _execution(hex_id), results=(), received_at=datetime.now(timezone.utc)
            )

        errors = _run_concurrently([partial(_report, hex_id) for hex_id in session_ids])

        assert errors == []
        assert store.count_executions() == 10
    finally:
        store.close()


def _park_the_writer_inside_its_transaction(
    monkeypatch: pytest.MonkeyPatch, *, then_fail: bool
) -> tuple[threading.Event, threading.Event]:
    """Stop `record_session` after its run row is written and before its
    results are: `inside` is set once it is parked, and it carries on (or
    raises, rolling everything back) once `release` is set."""
    inside = threading.Event()
    release = threading.Event()
    resolve = sqlite_store._resolve_test_case_ids

    def _parked(conn: sqlite3.Connection, node_ids: Sequence[str]) -> dict[str, int]:
        inside.set()
        release.wait(_JOIN_TIMEOUT_SECONDS)
        if then_fail:
            raise RuntimeError("the write fails after its run row")
        return resolve(conn, node_ids)

    monkeypatch.setattr(sqlite_store, "_resolve_test_case_ids", _parked)
    return inside, release


@pytest.mark.parametrize("then_fail", [False, True], ids=["commits", "rolls-back"])
def test_a_read_never_sees_another_threads_write_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, then_fail: bool
) -> None:
    """Every thread shares one connection, so a read that ran while another
    thread was inside `BEGIN IMMEDIATE` would run inside that transaction:
    it would see the run before its results, or a run that is then rolled
    back. A read must wait for the write to finish instead."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    inside, release = _park_the_writer_inside_its_transaction(monkeypatch, then_fail=then_fail)
    run_id = "a" * 32
    results = [_result(f"t.py::test_{i}") for i in range(5)]
    observed: list[tuple[bool, int, int, int]] = []

    def _write() -> None:
        with contextlib.suppress(RuntimeError):
            store.record_session(
                _execution(run_id), results=results, received_at=datetime.now(timezone.utc)
            )

    def _read() -> None:
        observed.append(
            (
                store.get_execution(run_id) is not None,
                len(store.get_results(run_id)),
                store.count_executions(),
                len(store.list_runs(limit=10, offset=0).items),
            )
        )

    try:
        writer = threading.Thread(target=_write, daemon=True)
        writer.start()
        assert inside.wait(_JOIN_TIMEOUT_SECONDS)
        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        # A reader that does not wait for the writer has finished by now.
        reader.join(timeout=0.2)
        release.set()
        writer.join(timeout=_JOIN_TIMEOUT_SECONDS)
        reader.join(timeout=_JOIN_TIMEOUT_SECONDS)
        assert not writer.is_alive()
        assert not reader.is_alive()

        assert observed == [(False, 0, 0, 0) if then_fail else (True, 5, 1, 1)]
    finally:
        release.set()
        store.close()


def test_every_read_returns_rather_than_waiting_on_the_stores_own_lock(tmp_path: Path) -> None:
    """Each read takes the store's lock; one that called another locking
    read while holding it would wait on itself forever."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    run_id = "a" * 32
    store.record_session(
        _execution(run_id),
        results=[_result("t.py::test_a")],
        received_at=datetime.now(timezone.utc),
    )
    reads: list[Callable[[], object]] = [
        partial(store.get_execution, run_id),
        store.count_executions,
        partial(store.get_results, run_id),
        store.count_results,
        partial(store.get_catalogue_entry, "t.py::test_a"),
        partial(store.list_runs, limit=10, offset=0),
        partial(store.list_runs_with_metadata_horizon, filters=[("k", "v")], limit=10, offset=0),
        partial(store.get_run_detail, run_id),
        partial(store.get_run_metadata, run_id),
        partial(store.list_results, run_id, limit=10, offset=0),
        partial(store.get_result, run_id, node_id="t.py::test_a"),
        partial(store.list_history, node_id="t.py::test_a", limit=10, offset=0),
        partial(store.list_settings, "test_sections"),
        partial(store.get_run_case_outcomes, run_id),
    ]
    try:
        assert _run_concurrently(reads) == []
    finally:
        store.close()


def test_two_stores_on_one_file_both_land_every_session(tmp_path: Path) -> None:
    """Two server processes on one database each hold their own connection,
    which no in-process lock can serialise. WAL, `BEGIN IMMEDIATE` and the
    busy timeout must make them take turns rather than fail with "database
    is locked"."""
    db_path = tmp_path / "store" / "vantage.db"
    stores = [SqliteExecutionStore(db_path), SqliteExecutionStore(db_path)]
    try:

        def _report(store: SqliteExecutionStore, prefix: str) -> None:
            for i in range(20):
                store.record_session(
                    _execution(f"{prefix}{i:031x}"),
                    results=[_result(f"t.py::test_{prefix}_{i}_{j}") for j in range(20)],
                    received_at=datetime.now(timezone.utc),
                )

        errors = _run_concurrently(
            [partial(_report, store, prefix) for store, prefix in zip(stores, "ab")]
        )

        assert errors == []
        assert stores[0].count_executions() == 40
        assert stores[1].count_results() == 800
    finally:
        for store in stores:
            store.close()


# --- The HTTP layer ------------------------------------------------------------
#
# `TestClient` used as a context manager runs every request on one event
# loop, as uvicorn does, so a request sent from a second thread shares that
# loop with one still in flight.


def _report(run_id: str) -> dict[str, Any]:
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


class _ParkedWriteStore(InMemoryExecutionStore):
    """Holds every `record_session` until `release` is set, before it takes
    the store's lock: the report stays in flight for as long as the test
    needs, and only the way a route calls the store can hold up another
    request."""

    def __init__(self) -> None:
        super().__init__()
        self.writing = threading.Event()
        self.release = threading.Event()

    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        self.writing.set()
        self.release.wait(2 * _JOIN_TIMEOUT_SECONDS)
        return super().record_session(
            execution, results=results, received_at=received_at, metadata=metadata
        )


def test_a_slow_store_write_holds_up_no_other_request() -> None:
    """A store call made on the event loop stalls every request until it
    returns -- heartbeats included, and a session that cannot heartbeat
    reads as abandoned. With one report's write parked inside the store, a
    heartbeat for another run and the capability check must both answer."""
    store = _ParkedWriteStore()
    live_run = "b" * 32
    # The base class's method, which does not park: a run already in progress.
    InMemoryExecutionStore.record_session(
        store,
        _start_only_execution(live_run),
        results=(),
        received_at=datetime.now(timezone.utc),
    )
    answered: dict[str, int] = {}

    with TestClient(create_app(store)) as client:

        def _write() -> None:
            answered["write"] = client.post("/api/v1/runs", json=_report("a" * 32)).status_code

        def _others() -> None:
            answered["capabilities"] = client.get("/api/v1/capabilities").status_code
            answered["heartbeat"] = client.post(f"/api/v1/runs/{live_run}/heartbeat").status_code

        writer = threading.Thread(target=_write, daemon=True)
        others = threading.Thread(target=_others, daemon=True)
        writer.start()
        try:
            assert store.writing.wait(_JOIN_TIMEOUT_SECONDS)
            others.start()
            others.join(timeout=_JOIN_TIMEOUT_SECONDS)
            assert not others.is_alive(), "a request waited for another request's store write"
            assert "write" not in answered
        finally:
            store.release.set()
            writer.join(timeout=_JOIN_TIMEOUT_SECONDS)
            others.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert not writer.is_alive()
    assert answered == {"capabilities": 200, "heartbeat": 200, "write": 201}


_HELD_RUN = "c" * 32
_HELD_NODE = "tests/test_held.py::test_x"

# Each route that reaches the store, with the store method it calls first.
_STORE_ROUTES: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "create_run": ("POST", "/api/v1/runs", {"json": _report("d" * 32)}, "record_session"),
    "heartbeat": ("POST", f"/api/v1/runs/{_HELD_RUN}/heartbeat", {}, "get_execution"),
    "list_runs": ("GET", "/api/v1/runs", {}, "list_runs"),
    "list_runs_by_metadata": (
        "GET",
        "/api/v1/runs",
        {"params": {"metadata_key": "k", "metadata_value": "v"}},
        "list_runs_with_metadata_horizon",
    ),
    "get_run_detail": ("GET", f"/api/v1/runs/{_HELD_RUN}", {}, "get_run_detail"),
    "get_run_metadata": ("GET", f"/api/v1/runs/{_HELD_RUN}/metadata", {}, "get_run_metadata"),
    "list_results": ("GET", f"/api/v1/runs/{_HELD_RUN}/results", {}, "get_execution"),
    "get_result": (
        "GET",
        f"/api/v1/runs/{_HELD_RUN}/result",
        {"params": {"node_id": _HELD_NODE}},
        "get_execution",
    ),
    "list_history": (
        "GET",
        "/api/v1/tests/history",
        {"params": {"node_id": _HELD_NODE}},
        "list_history",
    ),
    "list_sections": ("GET", "/api/v1/config/sections", {}, "list_settings"),
    "upsert_section": (
        "POST",
        "/api/v1/config/sections",
        {"json": {"name": "Held", "prefix": "tests/held"}},
        "upsert_setting",
    ),
    "delete_section": (
        "DELETE",
        "/api/v1/config/sections",
        {"params": {"name": "Seeded"}},
        "delete_setting",
    ),
    "get_run_sections": ("GET", f"/api/v1/runs/{_HELD_RUN}/sections", {}, "get_execution"),
}


def _hold(store: InMemoryExecutionStore, method: str) -> tuple[threading.Event, threading.Event]:
    """Make `store.<method>` wait until `release` is set, after setting
    `entered`. An instance attribute shadows the class's method, so every
    route that calls it through the store gets the held one."""
    entered = threading.Event()
    release = threading.Event()
    original = getattr(store, method)

    def _held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait(2 * _JOIN_TIMEOUT_SECONDS)
        return original(*args, **kwargs)

    setattr(store, method, _held)
    return entered, release


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "held"), _STORE_ROUTES.values(), ids=_STORE_ROUTES
)
def test_a_held_store_call_holds_up_no_other_request(
    method: str, path: str, request_kwargs: dict[str, Any], held: str
) -> None:
    """A route that called the store on the event loop would stall every
    other request until the call returned -- heartbeats included, and a
    session that cannot heartbeat reads as abandoned. With each route's
    store call held, the capability check must still answer."""
    store = InMemoryExecutionStore()
    now = datetime.now(timezone.utc)
    store.record_session(
        _start_only_execution(_HELD_RUN), results=[_result(_HELD_NODE)], received_at=now
    )
    store.upsert_setting(
        TEST_SECTIONS_NAMESPACE, "Seeded", value='{"prefix": "tests/seeded/"}', updated_at=now
    )
    entered, release = _hold(store, held)
    answered: dict[str, int] = {}

    with TestClient(create_app(store)) as client:

        def _held_request() -> None:
            answered["held"] = client.request(method, path, **request_kwargs).status_code

        def _capabilities() -> None:
            answered["capabilities"] = client.get("/api/v1/capabilities").status_code

        holder = threading.Thread(target=_held_request, daemon=True)
        other = threading.Thread(target=_capabilities, daemon=True)
        holder.start()
        try:
            assert entered.wait(_JOIN_TIMEOUT_SECONDS), f"{path} never called {held}"
            other.start()
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)
            assert not other.is_alive(), f"a request waited for {path}'s {held}"
            assert "held" not in answered
        finally:
            release.set()
            holder.join(timeout=_JOIN_TIMEOUT_SECONDS)
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert not holder.is_alive()
    assert answered["capabilities"] == 200
    assert 200 <= answered["held"] < 300


def test_section_posts_racing_for_the_last_slot_never_pass_the_bound(
    any_store: ExecutionStore,
) -> None:
    """Counting the stored sections and then writing would let every post
    racing for the last free slot see it free. The store makes the count
    and the write one step, so exactly one racer is created."""
    now = datetime.now(timezone.utc)
    for index in range(MAX_SECTIONS - 1):
        any_store.upsert_setting(
            TEST_SECTIONS_NAMESPACE,
            f"Seeded{index:03d}",
            value='{"prefix": "tests/seeded/"}',
            updated_at=now,
        )
    racers = 16
    statuses: list[int] = []

    with TestClient(create_app(any_store)) as client:

        def _post(index: int) -> None:
            section = {"name": f"Racer{index}", "prefix": f"tests/racer{index}"}
            statuses.append(client.post("/api/v1/config/sections", json=section).status_code)

        # Pure-Python work rarely yields the GIL mid-request at the default
        # interval; switching threads far more often is what makes a race
        # in the in-memory adapter likely enough to be caught.
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            errors = _run_concurrently([partial(_post, index) for index in range(racers)])
        finally:
            sys.setswitchinterval(interval)

    assert errors == []
    assert sorted(statuses) == [201] + [422] * (racers - 1)
    assert len(any_store.list_settings(TEST_SECTIONS_NAMESPACE)) == MAX_SECTIONS
