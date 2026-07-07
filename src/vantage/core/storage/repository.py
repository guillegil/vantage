"""Storage interfaces (design §2) -- narrow Protocols per aggregate. Core
and its services depend only on these; concrete SQLite (and later Postgres)
implementations live in `vantage.adapters` and satisfy these Protocols
structurally, with no inheritance required. This is the storage half of the
decoupling litmus (design §11): swapping the storage engine only requires a
new adapter module.

`ScheduleRepository` and `UserRepository` are declared here as empty
interface stubs so this module's shape is stable for later imports; their
full method surface (and the `Schedule`/`User` domain models they operate
on) land with the scheduling (slice 5) and users-and-attribution (slice 7)
slices respectively (design §2 handoff notes).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from vantage.core.domain.events import EventEnvelope
from vantage.core.domain.models import Run, RunState, TestResult


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """A single row read back from the append-only `events` log."""

    seq: int
    envelope: EventEnvelope


@runtime_checkable
class RunRepository(Protocol):
    """Persists and retrieves `Run` aggregates. `update_state` is the
    single writer of run state (design §6 -- state is authoritative in
    storage)."""

    def create(self, run: Run) -> None: ...

    def get(self, run_id: str) -> Run | None: ...

    def list(self, *, limit: int = 50, before: datetime | None = None) -> list[Run]: ...

    def update_state(
        self,
        run_id: str,
        state: RunState,
        *,
        at: datetime,
        reason: str | None = None,
    ) -> None: ...


@runtime_checkable
class ResultRepository(Protocol):
    """Persists and retrieves `TestResult` records for a run."""

    def create(self, result: TestResult) -> None: ...

    def list_for_run(self, run_id: str) -> list[TestResult]: ...


@runtime_checkable
class EventLog(Protocol):
    """Append-only ingestion audit trail; `since` is also the source of the
    SSE poll cursor (design §5)."""

    def append(self, envelope: EventEnvelope) -> int: ...

    def since(self, run_id: str, after_seq: int) -> list[StoredEvent]: ...


class ScheduleRepository(Protocol):
    """Stub -- see module docstring. Full interface lands at slice 5."""


class UserRepository(Protocol):
    """Stub -- see module docstring. Full interface lands at slice 7."""
