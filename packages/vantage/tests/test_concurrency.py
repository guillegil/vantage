"""Concurrent sessions recorded through one `SqliteExecutionStore` do not
corrupt or drop each other's writes.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vantage.core.domain.execution import Execution, Identity
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _result


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


def test_two_concurrent_sessions_both_leave_a_run_entry(tmp_path: Path) -> None:
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        ids = ["a" * 32, "b" * 32]
        results: dict[str, bool] = {}
        barrier = threading.Barrier(len(ids))

        def _report(hex_id: str) -> None:
            barrier.wait()
            created = store.record_session(
                _execution(hex_id), results=(), received_at=datetime.now(timezone.utc)
            )
            results[hex_id] = created

        threads = [threading.Thread(target=_report, args=(hex_id,)) for hex_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert store.count_executions() == 2
        assert results == {"a" * 32: True, "b" * 32: True}
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
        barrier = threading.Barrier(len(sessions))

        def _report(hex_id: str, prefix: str) -> None:
            barrier.wait()
            results = [_result(f"t.py::test_{prefix}_{i}") for i in range(200)]
            store.record_session(
                _execution(hex_id), results=results, received_at=datetime.now(timezone.utc)
            )

        threads = [
            threading.Thread(target=_report, args=(hex_id, prefix))
            for hex_id, prefix in sessions.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

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
        errors: list[Exception] = []
        barrier = threading.Barrier(len(session_ids))

        def _report(hex_id: str) -> None:
            try:
                barrier.wait()
                store.record_session(
                    _execution(hex_id), results=(), received_at=datetime.now(timezone.utc)
                )
            except Exception as exc:  # noqa: BLE001 -- collected for the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=_report, args=(hex_id,)) for hex_id in session_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert store.count_executions() == 10
    finally:
        store.close()
