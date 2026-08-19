"""The storage port: what any `ExecutionStore` adapter must implement (RQ-30)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from vantage.core.domain.execution import Execution
from vantage.core.domain.result import CatalogueEntry, Result


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

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        """Advance `execution_id`'s last contact to `contacted_at` (design.md D33).

        Returns False if `execution_id` is unknown, or if a newer contact is
        already recorded -- the two cases are deliberately indistinguishable
        from this boolean alone; a caller that needs to tell them apart calls
        `get_execution` first (design.md D33). Never touches the finish
        fields."""
        ...

    def count_executions(self) -> int:
        """Return how many executions are stored."""
        ...

    def get_results(self, execution_id: str) -> Sequence[Result]:
        """Return every result stored for `execution_id` (RQ-4, RQ-5, RQ-9)."""
        ...

    def count_results(self) -> int:
        """Return how many result rows are stored across all executions (RQ-12, RQ-38.2)."""
        ...

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        """Return the catalogue entry for `node_id`, or None if never observed (RQ-13)."""
        ...

    def close(self) -> None:
        """Release any resources held by the adapter."""
        ...
