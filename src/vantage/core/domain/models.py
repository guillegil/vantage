"""Domain models -- frozen dataclasses with zero I/O (design §1, §2).

Core never imports SQLAlchemy, sqlite3, or any adapter/server/collector
module; these dataclasses are the pure in-memory shape of a run and what it
recorded. Concrete persistence lives behind the storage Protocols
(`vantage.core.storage.repository`).

Conventions settled here (design §13, open questions):
- `run_id` is a ULID (sortable, unique) minted by whichever service creates
  the run (server or launcher -- out of scope for this module).
- Artifacts for a run live on disk under `<db_dir>/artifacts/<run_id>/`; the
  DB (and `Artifact.path` below) only ever stores a path reference, never
  file content (spec Domain 3 -- Artifacts On Disk).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar


class RunState(StrEnum):
    """The six lifecycle states a run may occupy (spec Domain 2). All
    members except PENDING and RUNNING are terminal.
    """

    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "stopped"
    FAILED = "failed"
    FAILED_TO_START = "failed-to-start"


@dataclass(frozen=True, slots=True)
class Run:
    """A single pytest invocation Vantage launched and is tracking."""

    run_id: str
    state: RunState
    testpath: str
    user: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    invocation: Mapping[str, Any] = field(default_factory=dict)
    env_snapshot: Mapping[str, str] = field(default_factory=dict)
    root_dir: str | None = None
    totals: Mapping[str, int] = field(default_factory=dict)
    last_heartbeat_at: datetime | None = None
    # Additive/optional (spec Domain 1 -- Run-Level Selection Metadata).
    seed: int | None = None
    seed_source: str | None = None
    marker_expr: str | None = None
    keyword_expr: str | None = None


@dataclass(frozen=True, slots=True)
class Phase:
    """One setup/call/teardown phase of a single test, as pytest reported
    it. Vantage records the outcome verbatim -- it never re-decides
    pass/fail (design §0, "Vantage records, never decides pass/fail").
    """

    name: str
    outcome: str
    duration: float
    # Additive/optional -- per-stream captured stdout/stderr/log, folded here
    # rather than a new top-level contract field (design SS1, SS7).
    captures: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TestResult:
    """A single test's recorded outcome within a run."""

    # Not a pytest test class -- suppress the `Test*` collection warning
    # (this name collision is with pytest's own naming convention, not ours;
    # the domain name is settled by design/spec).
    __test__: ClassVar[bool] = False

    id: int | None
    run_id: str
    node_id: str
    outcome: str
    duration: float
    phases: Mapping[str, Phase] = field(default_factory=dict)
    longrepr: str | None = None
    started_at: datetime | None = None
    # Additive/optional (spec Domain 1 -- Test-Level Structured Identity and
    # Parameters; Domain 6 -- Base Test ID Cross-Run Stability).
    base_test_id: str | None = None
    relpath: str | None = None
    lineno: int | None = None
    originalname: str | None = None
    parameters: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    fixture_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Artifact:
    """A reference to a file produced during a run. The DB never embeds
    artifact content -- only a path under `<db_dir>/artifacts/<run_id>/`
    (spec Domain 3 -- Artifacts On Disk).
    """

    id: int | None
    run_id: str
    kind: str
    path: str
    size: int
    created_at: datetime
