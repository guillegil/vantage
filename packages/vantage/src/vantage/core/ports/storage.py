"""The storage port: what any `ExecutionStore` adapter must implement (RQ-30)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from vantage.core.domain.execution import Execution
from vantage.core.domain.result import Result


class ExecutionStore(Protocol):
    """Persists `Execution` rows. Implementations live in `vantage.storage`."""

    def record_session(
        self, execution: Execution, *, results: Sequence[Result], received_at: datetime
    ) -> bool:
        """Store the run and its results. Return True if a row was created, False if the
        id was already stored."""
        ...

    def get_execution(self, execution_id: str) -> Execution | None:
        """Return the stored execution for `execution_id`, or None if it is unknown."""
        ...

    def count_executions(self) -> int:
        """Return how many executions are stored."""
        ...

    def close(self) -> None:
        """Release any resources held by the adapter."""
        ...
