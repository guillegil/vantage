"""The storage port: what any `ExecutionStore` adapter must implement (RQ-30)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from vantage.core.domain.execution import Execution


class ExecutionStore(Protocol):
    """Persists `Execution` rows. Implementations live in `vantage.storage`."""

    def record_execution(self, execution: Execution, *, received_at: datetime) -> bool:
        """Store it. Return True if a row was created, False if the id was already stored."""
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
