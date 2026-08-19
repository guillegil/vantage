"""RQ-30.1: the core's storage contract, run against the SQLite adapter.

Completes RQ-30.1: the same `ExecutionStoreContract` (`test_memory_store.py`
runs it against `InMemoryExecutionStore`) now runs unchanged against
`SqliteExecutionStore`, proving the port was never shaped around one
implementation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from vantage.core.ports.storage import ExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import ExecutionStoreContract, _execution, _start_only_execution


class TestSqliteExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[ExecutionStore]:
        adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
        yield adapter
        adapter.close()


@pytest.mark.req(id="RQ-3")
def test_finish_write_leaves_received_at_started_at_and_last_contact_at_untouched(
    tmp_path: Path,
) -> None:
    """W2: `received_at`, `started_at` and `last_contact_at` are not fields
    on `Execution`, so no contract test built on `get_execution` can observe
    them -- they are read directly off the `run` row here instead. Adding
    `last_contact_at = excluded.last_contact_at` to `_UPSERT_RUN`'s `DO
    UPDATE SET` list currently leaves every other test in this suite green
    while making a finished run's last contact jump on a later finish-write
    -- exactly the "a finished run is not stale" invariant D27 states.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        identity = "5" * 32
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        received = datetime(2026, 8, 15, 9, 0, 1, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        store.record_session(start, results=(), received_at=received)

        select_run = "SELECT received_at, started_at, last_contact_at FROM run WHERE id = ?"
        before = store._conn.execute(select_run, (identity,)).fetchone()  # noqa: SLF001

        # A DIFFERENT start time on the finish-write, deliberately. Handing it
        # the same `started` literal made the assertion below compare a value
        # with itself: it held whether or not the upsert overwrote the column,
        # and adding `started_at = excluded.started_at` to the DO UPDATE list
        # left the whole suite green. Its `received_at` and `last_contact_at`
        # siblings already bite, because their values differ.
        disagreeing_start = started + timedelta(hours=3)
        finish = _execution(identity, finished=True, started=disagreeing_start)
        later_received = received + timedelta(hours=1)
        store.record_session(finish, results=(), received_at=later_received)

        after = store._conn.execute(select_run, (identity,)).fetchone()  # noqa: SLF001

        before_received_at, before_started_at, before_last_contact_at = before
        after_received_at, after_started_at, after_last_contact_at = after
        assert after_received_at == before_received_at
        assert after_started_at == before_started_at
        assert after_last_contact_at == before_last_contact_at
    finally:
        store.close()
