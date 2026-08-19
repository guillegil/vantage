"""An in-memory `ExecutionStore`, kept for tests and RQ-30's contract proof.

No file, no lock, no adapter-specific pragmas -- everything the SQLite
adapter has to earn back. It exists so the shared contract suite
(``vantage_port_contract.py``) runs against two different mechanisms and the
port stays honest (design.md, D10) rather than being satisfied by only one
implementation that happens to agree with itself.

This is a second mechanism, not a stub (RQ-30, design.md D22): the catalogue
is keyed by node id with the same `MAX`-style monotonicity guard the SQLite
adapter enforces with SQL (design.md D20), and results are keyed by
``(run_id, node_id, attempt)`` with first-write-wins, mirroring the SQLite
adapter's ``ON CONFLICT ... DO NOTHING`` (design.md D19 layer 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from vantage.core.domain.execution import Execution
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result

# `attempt` is not on the wire (design.md D19); every result in Phase 1/2/3
# is attempt 0, matching the schema's `DEFAULT 0`.
_ATTEMPT = 0


class InMemoryExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` with dicts."""

    def __init__(self) -> None:
        self._executions: dict[str, Execution] = {}
        self._catalogue: dict[str, CatalogueEntry] = {}
        self._results: dict[tuple[str, str, int], Result] = {}

    def record_session(
        self, execution: Execution, *, results: Sequence[Result], received_at: datetime
    ) -> bool:
        # `received_at` is part of the port's signature (the two-clocks point,
        # design.md D1) but nothing in this adapter's contract surface reads
        # it back -- `get_execution` returns only what the client reported.
        del received_at
        identity = execution.identity.value
        created = identity not in self._executions
        if created:
            self._executions[identity] = execution

        for result in results:
            self._upsert_catalogue_entry(execution, result.identity)
            key = (identity, result.identity.node_id, _ATTEMPT)
            if key not in self._results:
                self._results[key] = result

        return created

    def _upsert_catalogue_entry(self, execution: Execution, identity: CaseIdentity) -> None:
        existing = self._catalogue.get(identity.node_id)
        if existing is None:
            self._catalogue[identity.node_id] = CatalogueEntry(
                identity=identity,
                first_seen_at=execution.started_at,
                last_seen_at=execution.started_at,
                last_seen_run_id=execution.identity.value,
            )
            return

        # Mirrors the SQLite `DO UPDATE`: identity fields always refresh, but
        # `last_seen_at`/`last_seen_run_id` advance only when the new run is
        # strictly newer (design.md D20's monotonicity guard).
        advances = execution.started_at > existing.last_seen_at
        self._catalogue[identity.node_id] = CatalogueEntry(
            identity=identity,
            first_seen_at=existing.first_seen_at,
            last_seen_at=execution.started_at if advances else existing.last_seen_at,
            last_seen_run_id=execution.identity.value if advances else existing.last_seen_run_id,
        )

    def get_execution(self, execution_id: str) -> Execution | None:
        return self._executions.get(execution_id)

    def count_executions(self) -> int:
        return len(self._executions)

    def get_results(self, execution_id: str) -> Sequence[Result]:
        return [
            result
            for (run_id, _node_id, _attempt), result in self._results.items()
            if run_id == execution_id
        ]

    def count_results(self) -> int:
        return len(self._results)

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        return self._catalogue.get(node_id)

    def close(self) -> None:
        self._executions.clear()
        self._catalogue.clear()
        self._results.clear()
