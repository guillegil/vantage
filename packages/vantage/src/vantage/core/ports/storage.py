"""The storage port: the `ExecutionStore` protocol every adapter implements,
and the types it reads and writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from vantage.core.domain.execution import Execution
from vantage.core.domain.projection import FailureProjection, VcsProjection
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result

MAX_PAGE_ITEMS = 200
"""The hard cap on items in one page -- clamped in the adapter, never
rejected; a request for more is satisfied up to this many."""

MAX_IDENTITY_CHARS = 1024
"""The bound on a client-chosen test identity value -- a 1,024-character
identity percent-encodes to at most ~3 KiB, comfortably inside the common
8 KiB request-line buffer."""

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """A bounded page of items. No `total` -- nothing needs one, and it
    would cost a `COUNT(*)` on every page.

    No `slots=True`: dataclass slot-recreation combined with `Generic` is a
    hazard on this project's Python 3.10 floor, and this envelope gains
    nothing from slots.
    """

    items: tuple[T, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class RunListEntry:
    """One row of `list_runs`.

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
    """The full-record return of `get_run_detail`.

    `execution.vcs` is the whole, unbounded `VcsContext` -- the detail path
    keeps the full record reachable, which is the other half of the
    lean-list rule.
    """

    execution: Execution
    last_contact_at: datetime | None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One row of `list_history` -- a test's execution in one run. `vcs` is
    a lean `VcsProjection`, the same display bound `RunListEntry` applies."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None
    last_contact_at: datetime | None
    outcome: str
    duration: float | None
    vcs: VcsProjection | None


@dataclass(frozen=True, slots=True)
class ResultListEntry:
    """One row of `list_results`.

    `failure` is a lean `FailureProjection`, never the full
    `FailureEvidence`: this type has no field for `traceback`,
    `failure_repr` or captured output, so a list response cannot leak them.
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
    only thing that knows its shape."""

    namespace: str
    key: str
    value: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MetadataFile:
    """One row of `run_metadata_file`. `source_file` is the DECLARED,
    rootpath-relative path exactly as written -- never the resolved one,
    which is absolute and can carry a username."""

    source_file: str
    content_type: str
    status: str


@dataclass(frozen=True, slots=True)
class MetadataEntry:
    """One row of `run_metadata`. `value` is `None` whenever `status` is not
    `'captured'` -- a declared-but-uncaptured key is a row, never a missing
    row."""

    key: str
    value: str | None
    source_file: str
    status: str


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """The frozen aggregate `record_session` accepts: files and entries as
    one parameter rather than two, so a caller passes them together."""

    files: tuple[MetadataFile, ...] = ()
    entries: tuple[MetadataEntry, ...] = ()


EMPTY_RUN_METADATA = RunMetadata()
"""`record_session`'s default -- what a session with no metadata declaration
reports."""


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
        """Store the run, its results and its declared metadata. Return True
        if a row was created, False if the id was already stored.
        `metadata`'s two tables are written once each -- a report with
        metadata identical to what is already stored is a no-op."""
        ...

    def get_execution(self, execution_id: str) -> Execution | None:
        """Return the stored execution for `execution_id`, or None if it is unknown."""
        ...

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        """Advance `execution_id`'s last contact to `contacted_at`.

        Returns False if `execution_id` is unknown, or if a newer contact is
        already recorded -- the two cases are deliberately indistinguishable
        from this boolean alone; a caller that needs to tell them apart calls
        `get_execution` first. Never touches the finish fields."""
        ...

    def count_executions(self) -> int:
        """Return how many executions are stored."""
        ...

    def get_results(self, execution_id: str) -> Sequence[Result]:
        """Return every result stored for `execution_id`."""
        ...

    def count_results(self) -> int:
        """Return how many result rows are stored across all executions."""
        ...

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        """Return the catalogue entry for `node_id`, or None if never observed."""
        ...

    def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        metadata_key: str | None = None,
        metadata_value: str | None = None,
    ) -> Page[RunListEntry]:
        """Return a page of runs, newest first.

        Ordered `started_at DESC, id DESC` -- the `id` tiebreak makes the
        order total, so a page boundary is deterministic even when two runs
        share a `started_at`. `limit` is clamped at `MAX_PAGE_ITEMS`, never
        rejected; `has_more` is true when more rows exist beyond the
        returned page. Each entry's VCS data is a lean `VcsProjection` --
        the entry's own `execution.vcs` is always `None`.

        When both `metadata_key` and `metadata_value` are given, the page is
        narrowed to runs holding that exact declared `(key, value)` pair;
        otherwise both are ignored. The route enforces the both-or-neither
        rule."""
        ...

    def count_runs_predating_metadata_key(self, key: str) -> int:
        """How many runs were recorded before `key` was first declared.

        `first_seen` is `MIN(run.started_at)` over runs holding **any**
        `run_metadata` row for `key`, regardless of status -- a
        declared-but-dropped row still counts, since without it a run whose
        value was too large to capture would be miscounted as predating the
        declaration. When no run has ever carried `key`, `first_seen` is
        undefined and this returns the total run count: every run predates a
        key that was never declared."""
        ...

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        """Return the full record for one run, or None if `execution_id` is
        unknown. The whole stored commit subject is reachable here -- the
        complement of `list_runs`' bounded projection."""
        ...

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        """Return a page of one run's results -- the paginated sibling of
        `get_results`, with the same clamp and `has_more` mechanism as
        `list_runs`. Each entry's failure data is a lean `FailureProjection`,
        as `list_runs` does for VCS data; the full record is reachable via
        `get_result`."""
        ...

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        """Return the full record for one result, or None if no result with
        that `node_id` is stored for `execution_id` -- the complement of
        `list_results`' bounded projection. `failure` and `captured` are
        populated in full, unbounded."""
        ...

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        """Return a page of one test's execution history, newest first. An
        unknown `node_id` yields an empty page, never an error. Each entry's
        VCS data is a lean `VcsProjection`, same as `list_runs`."""
        ...

    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        """Return every setting stored for `namespace`, ordered by `key` --
        the same order `summarize_sections` presents its section list in."""
        ...

    def upsert_setting(self, namespace: str, key: str, *, value: str, updated_at: datetime) -> bool:
        """Create or replace one `(namespace, key)` pair. Returns True only
        on a true first insert, mirroring `record_session`'s `created`
        boolean -- the route needs `201` versus `200`."""
        ...

    def delete_setting(self, namespace: str, key: str) -> bool:
        """Delete one `(namespace, key)` pair. Returns False for a key that
        was not there, mirroring `touch_last_contact`'s boolean -- the route
        needs `404` versus `204`."""
        ...

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        """Return `(file_path, outcome)` for every result of `execution_id`
        -- the aggregate input `summarize_sections` classifies. Not
        paginated, like `get_results`: this is an aggregate input, not a
        response."""
        ...

    def close(self) -> None:
        """Release any resources held by the adapter."""
        ...
