"""An in-memory `ExecutionStore`, kept for tests and RQ-30's contract proof.

No file, no lock, no adapter-specific pragmas -- everything the SQLite
adapter (PR5) has to earn back. It exists so the shared contract suite
(``vantage_port_contract.py``) runs against two different mechanisms and the
port stays honest (design.md, D10) rather than being satisfied by only one
implementation that happens to agree with itself.
"""

from __future__ import annotations

from datetime import datetime

from vantage.core.domain.execution import Execution


class InMemoryExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` with a `dict`."""

    def __init__(self) -> None:
        self._executions: dict[str, Execution] = {}

    def record_execution(self, execution: Execution, *, received_at: datetime) -> bool:
        # `received_at` is part of the port's signature (the two-clocks point,
        # design.md D1) but nothing in this adapter's contract surface reads
        # it back -- `get_execution` returns only what the client reported.
        del received_at
        identity = execution.identity.value
        if identity in self._executions:
            return False
        self._executions[identity] = execution
        return True

    def get_execution(self, execution_id: str) -> Execution | None:
        return self._executions.get(execution_id)

    def count_executions(self) -> int:
        return len(self._executions)

    def close(self) -> None:
        self._executions.clear()
