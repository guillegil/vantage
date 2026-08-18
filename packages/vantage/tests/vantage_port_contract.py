"""Shared contract for any `ExecutionStore` implementation (RQ-30).

Never collected directly by pytest -- it is not named ``test_*`` -- and never
shipped in a wheel: the ``pytest`` import here would break ``vantage.core``'s
stdlib-only rule (RQ-26) if this module lived inside the package instead of
alongside the tests. Each adapter's own ``test_*_store.py`` subclasses
``ExecutionStoreContract``, provides a ``store`` fixture, and inherits every
test method unchanged -- that is what proves the core suite "passes
unchanged" against a second adapter (RQ-30.1). ``vantage.storage`` implements
this now (``test_memory_store.py``); ``vantage.storage.sqlite_store`` does
the same in PR5.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from vantage.core.domain.execution import Execution, Identity
from vantage.core.ports.storage import ExecutionStore


def _execution(hex_id: str, *, finished: bool = True) -> Execution:
    started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    return Execution(
        identity=Identity(hex_id),
        started_at=started,
        finished_at=(started + timedelta(seconds=5)) if finished else None,
        exit_status=0 if finished else None,
        interrupted=not finished,
        interrupt_reason=None if finished else "ctrl-c",
    )


class ExecutionStoreContract:
    """Inherit this and override the `store` fixture with a fresh adapter instance."""

    @pytest.fixture
    def store(self) -> ExecutionStore:
        raise NotImplementedError("subclasses must override the `store` fixture")

    @pytest.mark.req("RQ-30")
    def test_first_write_creates_a_row(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)

        created = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert store.count_executions() == 1

    @pytest.mark.req("RQ-30")
    def test_replaying_the_same_id_reports_no_new_row(self, store: ExecutionStore) -> None:
        execution = _execution("b" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        created_again = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created_again is False
        assert store.count_executions() == 1

    @pytest.mark.req("RQ-30")
    def test_get_execution_returns_what_was_stored(self, store: ExecutionStore) -> None:
        execution = _execution("c" * 32, finished=False)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        found = store.get_execution(execution.identity.value)

        assert found == execution

    @pytest.mark.req("RQ-30")
    def test_get_execution_returns_none_for_an_unknown_id(self, store: ExecutionStore) -> None:
        assert store.get_execution("d" * 32) is None
