"""The storage port: what any `ExecutionStore` adapter must implement (RQ-30).

The read types below (`Page`, `RunListEntry`, `RunDetail`, `HistoryEntry`)
and the pagination constants landed in Phase 1 of the read-api change
(design.md D57, D58, D61). `list_runs` and `get_run_detail` landed in Phase
2, paired with both adapters implementing them in the same commit. Phase 3
adds `list_results` and `list_history` the same way -- all four read methods
are now declared, each paired with both adapters landing in this commit so
no adapter is ever mid-slice out of structural conformance under
`mypy --strict`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from vantage.core.domain.execution import Execution
from vantage.core.domain.projection import FailureProjection, VcsProjection
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result

MAX_PAGE_ITEMS = 200
"""The hard cap on items in one page (design.md D61) -- clamped in the
adapter, never rejected; a request for more is satisfied up to this many."""

MAX_IDENTITY_CHARS = 1024
"""The bound on a client-chosen test identity value (design.md D54) -- a
1,024-character identity percent-encodes to at most ~3 KiB, comfortably
inside the common 8 KiB request-line buffer."""

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """A bounded page of items (design.md D58). No `total` -- no scenario
    asks for one, and a total would need a `COUNT(*)` on every page.

    No `slots=True`: dataclass slot-recreation combined with `Generic` is a
    hazard on this project's Python 3.10 floor, and this envelope gains
    nothing from slots.
    """

    items: tuple[T, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class RunListEntry:
    """One row of `list_runs` (design.md D57, D58, D59).

    `execution.vcs` is always `None` here -- the lean VCS data rides beside
    it in `vcs`, a `VcsProjection`, so there is exactly one place a list
    entry's VCS data can be read from. A list entry carrying both a
    populated `execution.vcs` and this field would be two answers to one
    question.
    """

    execution: Execution
    last_contact_at: datetime | None
    vcs: VcsProjection | None


@dataclass(frozen=True, slots=True)
class RunDetail:
    """The full-record return of `get_run_detail` (design.md D57, D58, D59).

    `execution.vcs` is the whole, unbounded `VcsContext` -- the detail path
    keeps the full record reachable, which is the other half of the
    lean-list rule.
    """

    execution: Execution
    last_contact_at: datetime | None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One row of `list_history` -- a test's execution in one run
    (design.md D57, D58, D59, D60). `vcs` is a lean `VcsProjection`, the
    same read-display bound `RunListEntry` applies."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None
    last_contact_at: datetime | None
    outcome: str
    duration: float | None
    vcs: VcsProjection | None


@dataclass(frozen=True, slots=True)
class ResultListEntry:
    """One row of `list_results` (design.md D76, D77).

    Every field the pre-existing `/runs/{id}/results` wire contract already
    carried -- identity, outcome, every phase outcome/duration, `worker_id`
    -- is unchanged. Only `failure` is new here, and it is a lean
    `FailureProjection`, never the full `FailureEvidence`: this type has no
    field for `traceback`, `failure_repr` or captured output at all, the
    same structural exclusion `VcsProjection` gives `vcs_root` (D59) --
    nothing to leak because the type never carries it in the first place.
    """

    identity: CaseIdentity
    outcome: str
    duration: float | None
    started_at: datetime | None
    finished_at: datetime | None
    setup_outcome: str | None
    call_outcome: str | None
    teardown_outcome: str | None
    setup_duration: float | None
    call_duration: float | None
    teardown_duration: float | None
    worker_id: str | None
    failure: FailureProjection | None


@dataclass(frozen=True, slots=True)
class UserSetting:
    """One row of `user_setting`. `value` is JSON TEXT this layer never
    parses -- the namespace's own Pydantic model in `vantage.service` is the
    only thing that knows its shape (design.md D83)."""

    namespace: str
    key: str
    value: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MetadataFile:
    """One row of `run_metadata_file` (design.md D91, D98). `source_file` is
    the DECLARED, rootpath-relative path exactly as written (P-1) -- never
    the resolved one, which is absolute and can carry a username."""

    source_file: str
    content_type: str
    status: str


@dataclass(frozen=True, slots=True)
class MetadataEntry:
    """One row of `run_metadata` (design.md D91, D98). `value` is `None`
    whenever `status` is not `'captured'` -- a declared-but-uncaptured key
    is a row, never a missing row (design.md D95)."""

    key: str
    value: str | None
    source_file: str
    status: str


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """The frozen aggregate `record_session` accepts (design.md D98): one
    parameter rather than two collections, so a caller cannot pass entries
    without their files -- a state D95's "every declared thing gets a row"
    rule forbids and this shape makes unrepresentable."""

    files: tuple[MetadataFile, ...] = ()
    entries: tuple[MetadataEntry, ...] = ()


EMPTY_RUN_METADATA = RunMetadata()
"""`record_session`'s default (design.md D98) -- what a session with no
metadata declaration reports. Only the two adapter implementations change to
accept the new keyword; no existing call site is widened or broken."""


class ExecutionStore(Protocol):
    """Persists `Execution` rows. Implementations live in `vantage.storage`."""

    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        """Store the run, its results and its declared metadata (design.md
        D98). Return True if a row was created, False if the id was already
        stored. `metadata`'s two tables are written once each -- a report
        with metadata identical to what is already stored is a no-op."""
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

    def list_runs(self, *, limit: int, offset: int) -> Page[RunListEntry]:
        """Return a page of runs, newest first (design.md D57, D61).

        Ordered `started_at DESC, id DESC` -- the `id` tiebreak makes the
        order total, so a page boundary is deterministic even when two runs
        share a `started_at`. `limit` is clamped at `MAX_PAGE_ITEMS`, never
        rejected; `has_more` is true when more rows exist beyond the
        returned page. Each entry's VCS data is a lean `VcsProjection`
        (design.md D59, D60) -- the entry's own `execution.vcs` is always
        `None`."""
        ...

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        """Return the full record for one run, or None if `execution_id` is
        unknown (design.md D57, D58, D59). The whole stored commit subject
        is reachable here -- the complement of `list_runs`' bounded
        projection."""
        ...

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        """Return a page of one run's results (design.md D57, D76, D77) --
        the paginated sibling of `get_results`, which is left unchanged.
        Same clamp and `has_more` mechanism as `list_runs` (design.md D61).
        Each entry's failure data is a lean `FailureProjection`, mirroring
        `list_runs`' `VcsProjection` treatment of VCS data (design.md D59,
        D76) -- the full record is reachable via `get_result`."""
        ...

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        """Return the full record for one result, or None if no result with
        that `node_id` is stored for `execution_id` (design.md D77, D78) --
        the complement of `list_results`' bounded projection. `failure` and
        `captured` are populated in full, unbounded."""
        ...

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        """Return a page of one test's execution history, newest first
        (design.md D57, D61, D63). An unknown `node_id` yields an empty
        page, never an error. Each entry's VCS data is a lean
        `VcsProjection` (design.md D59, D60), same as `list_runs`."""
        ...

    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        """Return every setting stored for `namespace`, ordered by `key`
        (design.md D85, D86) -- the same order `summarize_sections` presents
        its section list in."""
        ...

    def upsert_setting(self, namespace: str, key: str, *, value: str, updated_at: datetime) -> bool:
        """Create or replace one `(namespace, key)` pair. Returns True only
        on a true first insert, mirroring `record_session`'s `created`
        boolean -- the route needs `201` versus `200` (design.md D86)."""
        ...

    def delete_setting(self, namespace: str, key: str) -> bool:
        """Delete one `(namespace, key)` pair. Returns False for a key that
        was not there, mirroring `touch_last_contact`'s boolean -- the route
        needs `404` versus `204` (design.md D86)."""
        ...

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        """Return `(file_path, outcome)` for every result of `execution_id`
        (design.md D85, D86) -- the aggregate input `summarize_sections`
        classifies. Not paginated, like `get_results`: this is an aggregate
        input, not a response."""
        ...

    def close(self) -> None:
        """Release any resources held by the adapter."""
        ...
