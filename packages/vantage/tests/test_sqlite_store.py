"""RQ-30.1: the core's storage contract, run against the SQLite adapter.

Completes RQ-30.1: the same `ExecutionStoreContract` (`test_memory_store.py`
runs it against `InMemoryExecutionStore`) now runs unchanged against
`SqliteExecutionStore`, proving the port was never shaped around one
implementation.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from vantage.core.ports.storage import ExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import ExecutionStoreContract


class TestSqliteExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[ExecutionStore]:
        adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
        yield adapter
        adapter.close()
