"""RQ-38, criterion 1 only: two sessions reporting concurrently leave two run
entries with different identifiers (design.md D8). Criteria 2 and 3 count
results, and this milestone writes none -- out of scope here.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from vantage.core.domain.execution import Execution, Identity
from vantage.storage.sqlite_store import SqliteExecutionStore


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


@pytest.mark.req("RQ-38")
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
