"""`any_store` -- each `ExecutionStore` implementation in turn, one test run
each -- and `any_stored_metadata`, the reader of the metadata rows that
store holds, for tests whose behaviour must not depend on the adapter.

A plugin module rather than a `conftest.py` (see `vantage_test_server.py`
for why this layout has no package-level conftest). A test module that
needs the fixtures loads it with ``pytest_plugins = ["store_fixtures"]``.

The id of each parametrisation appears in the test id (``[memory]``,
``[sqlite]``, ``[postgres]``), so a failure names the adapter that produced
it. The SQLite adapter gets a fresh database under `tmp_path`, the
PostgreSQL one a fresh database on the server `postgres_fixtures` is
pointed at -- skipped when it is pointed at none -- and every store is
closed afterwards, so no test shares state with another.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from pathlib import Path

import pytest
from memory_store import InMemoryExecutionStore
from sqlite_rows import read_metadata
from vantage.core.ports.storage import ExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import StoredMetadata

# `postgres_store` and `postgres_metadata`, for the PostgreSQL adapter.
pytest_plugins = ["postgres_fixtures"]


@pytest.fixture(params=["memory", "sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def any_adapter(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[ExecutionStore, StoredMetadata]]:
    """Each adapter, with a reader of the metadata rows it stores."""
    if request.param == "memory":
        memory = InMemoryExecutionStore()
        try:
            yield memory, memory.metadata
        finally:
            memory.close()
        return
    if request.param == "postgres":
        # Requested only for this parameter, so the others never skip for
        # want of a server; `postgres_store` closes the store itself.
        yield (
            request.getfixturevalue("postgres_store"),
            request.getfixturevalue("postgres_metadata"),
        )
        return
    database = tmp_path / "store" / "vantage.db"
    adapter = SqliteExecutionStore(database)
    try:
        yield adapter, functools.partial(read_metadata, database)
    finally:
        adapter.close()


@pytest.fixture
def any_store(any_adapter: tuple[ExecutionStore, StoredMetadata]) -> ExecutionStore:
    return any_adapter[0]


@pytest.fixture
def any_stored_metadata(any_adapter: tuple[ExecutionStore, StoredMetadata]) -> StoredMetadata:
    return any_adapter[1]
