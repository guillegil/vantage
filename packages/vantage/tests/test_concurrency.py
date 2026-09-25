"""Concurrent sessions recorded through one `SqliteExecutionStore` do not
corrupt or drop each other's writes.

Every thread is joined with a timeout: a deadlock is exactly what these tests
look for, and an unbounded `join` would hang the suite instead of failing it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

from vantage.core.domain.execution import Execution, Identity
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
