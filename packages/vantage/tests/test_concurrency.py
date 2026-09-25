"""Threads sharing one `SqliteExecutionStore` neither corrupt or drop each
other's writes nor read each other's writes half-done.

Every thread is joined with a timeout: a deadlock is exactly what these tests
look for, and an unbounded `join` would hang the suite instead of failing it.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import pytest
from vantage.core.domain.execution import Execution, Identity
from vantage.storage import sqlite_store
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _result

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

    threads = [threading.Thread(target=_wrap(target)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not any(thread.is_alive() for thread in threads), "a writer thread never finished"
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
        writer = threading.Thread(target=_write)
        writer.start()
        assert inside.wait(_JOIN_TIMEOUT_SECONDS)
        reader = threading.Thread(target=_read)
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
        partial(store.list_runs, limit=10, offset=0, metadata_key="k", metadata_value="v"),
        partial(store.count_runs_predating_metadata_key, "k"),
        partial(store.list_runs_with_metadata_horizon, key="k", value="v", limit=10, offset=0),
        partial(store.get_run_detail, run_id),
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
