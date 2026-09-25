"""The core's storage contract, run against the in-memory adapter."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from memory_store import InMemoryExecutionStore
from vantage.core.ports.storage import ExecutionStore
from vantage_port_contract import ExecutionStoreContract


class TestInMemoryExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def store(self) -> Iterator[ExecutionStore]:
        adapter = InMemoryExecutionStore()
        yield adapter
        adapter.close()
