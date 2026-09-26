"""The core's storage contract, run against the in-memory adapter."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from memory_store import InMemoryExecutionStore
from vantage_port_contract import ExecutionStoreContract, StoredMetadata


class TestInMemoryExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def store(self) -> Iterator[InMemoryExecutionStore]:
        adapter = InMemoryExecutionStore()
        yield adapter
        adapter.close()

    @pytest.fixture
    def stored_metadata(self, store: InMemoryExecutionStore) -> StoredMetadata:
        return store.metadata
