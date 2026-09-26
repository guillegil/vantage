"""The storage port: the `ExecutionStore` protocol every adapter implements,
and the types it reads and writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from vantage.core.domain.execution import Execution
from vantage.core.domain.metadata import (
    FILE_STATUSES,
    KEY_STATUSES,
    METADATA_CONTENT_TYPES,
    METADATA_SOURCES,
    SESSION_KEY_STATUSES,
)
from vantage.core.domain.projection import (
    FailureProjection,
    VcsProjection,
    project_failure,
    project_vcs,
)
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result

MAX_PAGE_ITEMS = 200
"""The hard cap on items in one page -- clamped in the adapter, never
rejected; a request for more is satisfied up to this many."""

MAX_IDENTITY_CHARS = 1024
"""The length of a short, client-chosen, path-shaped string. Nothing is
refused against this constant itself: a stored node id may be longer --
pytest never shortens a parametrize id -- and must stay readable by its
exact value. It is the value `SECTION_PREFIX_MAX_CHARS`,
`MAX_METADATA_KEY_CHARS` and `MAX_METADATA_VALUE_BYTES` take, and the
plugin's bound on a declared path."""

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

    @classmethod
    def from_execution(
        cls, execution: Execution, *, last_contact_at: datetime | None
    ) -> RunListEntry:
        """The list entry for a stored run, its VCS context moved into the
        lean projection."""
        return cls(
            execution=replace(execution, vcs=None),
            last_contact_at=last_contact_at,
            vcs=project_vcs(execution.vcs),
        )


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

    @classmethod
    def from_execution(
        cls,
        execution: Execution,
        *,
        last_contact_at: datetime | None,
        outcome: str,
        duration: float | None,
    ) -> HistoryEntry:
        """The history entry for one test's result in a stored run."""
        return cls(
            run_id=execution.identity.value,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            last_contact_at=last_contact_at,
            outcome=outcome,
            duration=duration,
            vcs=project_vcs(execution.vcs),
        )


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

    @classmethod
    def from_result(cls, result: Result) -> ResultListEntry:
        """The list entry for a stored result: everything but the heavy
        evidence, with the failure evidence projected."""
        return cls(
            identity=result.identity,
            outcome=result.outcome,
            duration=result.duration,
            started_at=result.started_at,
            finished_at=result.finished_at,
            setup_outcome=result.setup_outcome,
            call_outcome=result.call_outcome,
            teardown_outcome=result.teardown_outcome,
            setup_duration=result.setup_duration,
            call_duration=result.call_duration,
            teardown_duration=result.teardown_duration,
            worker_id=result.worker_id,
            failure=project_failure(result.failure),
        )


@dataclass(frozen=True, slots=True)
class UserSetting:
    """One row of `user_setting`. `value` is JSON TEXT this layer never
    parses -- the namespace's own Pydantic model in `vantage.service` is the
    only thing that knows its shape."""

    namespace: str
    key: str
    value: str
    updated_at: datetime


def _check_vocabulary(field: str, value: str, vocabulary: frozenset[str]) -> None:
    if value not in vocabulary:
        raise ValueError(f"{field} must be one of {sorted(vocabulary)}, got {value!r}")


@dataclass(frozen=True, slots=True)
class MetadataFile:
    """One row of `run_metadata_file`. `source_file` is the DECLARED,
    rootpath-relative path exactly as written -- never the resolved one,
    which is absolute and can carry a username.

    `content_type` and `status` are checked against the vocabularies the
    schema's CHECK constraints accept, so no adapter is ever handed a row
    one of them would store and another refuse."""

    source_file: str
    content_type: str
    status: str

    def __post_init__(self) -> None:
        _check_vocabulary("content_type", self.content_type, METADATA_CONTENT_TYPES)
        _check_vocabulary("status", self.status, FILE_STATUSES)


@dataclass(frozen=True, slots=True)
class MetadataEntry:
    """One row of `run_metadata`: one key's outcome in one run.

    `source` is where the value came from: `'file'`, a key of the declared
    file `source_file`, or `'session'`, a key the test session reported
    itself, which has no `source_file`. `name` is the display name the run's
    declaration gave the key, and `declared` whether the declaration named
    the key at all -- a file's key always is. The defaults describe a file
    key with no display name.

    `value` is set exactly when `status` is `'captured'` -- a key without a
    value is a row, never a missing row. `status` and `source` are checked
    as `MetadataFile`'s fields are, and a session key's status against the
    narrower `SESSION_KEY_STATUSES`, so no adapter is handed a row that
    contradicts itself."""

    key: str
    value: str | None
    source_file: str | None
    status: str
    source: str = "file"
    name: str | None = None
    declared: bool = True

    def __post_init__(self) -> None:
        _check_vocabulary("status", self.status, KEY_STATUSES)
        _check_vocabulary("source", self.source, METADATA_SOURCES)
        if (self.status == "captured") != (self.value is not None):
            raise ValueError("value must be set exactly when status is 'captured'")
        if self.source == "file":
            if self.source_file is None:
                raise ValueError("a file key must name its source_file")
            if not self.declared:
                raise ValueError("a file key is always declared")
        elif self.source_file is not None:
            raise ValueError("a session key has no source_file")
        else:
            _check_vocabulary("status", self.status, SESSION_KEY_STATUSES)


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """The frozen aggregate `record_session` accepts: files and entries as
    one parameter rather than two, so a caller passes them together."""

    files: tuple[MetadataFile, ...] = ()
    entries: tuple[MetadataEntry, ...] = ()


EMPTY_RUN_METADATA = RunMetadata()
"""`record_session`'s default -- what a session with no metadata declaration
reports."""


class NamespaceFullError(Exception):
    """`upsert_setting` refused a new key: its namespace already holds the
    `max_keys` it was given."""


class ExecutionStore(Protocol):
    """Persists `Execution` rows. Implementations live in `vantage.storage`.

    The service calls one store from several worker threads at once, so an
    implementation must be safe to share between them.

    `count_executions`, `get_results`, `count_results` and
    `get_catalogue_entry` are called by no route: they are how the tests,
    the plugin's included, check what a write stored, through the same port
    on either adapter."""

    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        """Store the run, its results and its metadata. Return True if a row
        was created, False if the id was already stored. Every metadata row
        is written once: the first report to carry a file or a key keeps it,
        whatever a later report says, and whichever source a later row of the
        same key comes from."""
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

    def list_runs(self, *, limit: int, offset: int) -> Page[RunListEntry]:
        """Return a page of runs, newest first.

        Ordered `started_at DESC, id DESC` -- the `id` tiebreak makes the
        order total, so a page boundary is deterministic even when two runs
        share a `started_at`. `limit` is clamped at `MAX_PAGE_ITEMS`, never
        rejected; `has_more` is true when more rows exist beyond the
        returned page. Each entry's VCS data is a lean `VcsProjection` --
        the entry's own `execution.vcs` is always `None`."""
        ...

    def list_runs_with_metadata_horizon(
        self, *, key: str, value: str, limit: int, offset: int
    ) -> tuple[Page[RunListEntry], int]:
        """`list_runs` narrowed to runs holding the exact declared
        `(key, value)` pair, together with how many runs were recorded
        before `key` was first declared, both read from one snapshot of the
        store. Two separate reads can straddle a session another process
        records, and then describe two different sets of runs. This is the
        only filtered read of the run list.

        A key declared but not captured has no value, so it never matches.

        `first_seen` is `MIN(run.started_at)` over runs holding **any**
        `run_metadata` row for `key`, regardless of status -- a
        declared-but-dropped row still counts, since without it a run whose
        value was too large to capture would be miscounted as predating the
        declaration. When no run has ever carried `key`, every run predates
        it and the count is the total run count."""
        ...

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        """Return the full record for one run, or None if `execution_id` is
        unknown. The whole stored commit subject is reachable here -- the
        complement of `list_runs`' bounded projection."""
        ...

    def get_run_metadata(self, execution_id: str) -> Sequence[MetadataEntry] | None:
        """Return every metadata row stored for one run, ordered by `key`
        (code point order), or None if `execution_id` is unknown. A known
        run with no metadata has an empty sequence. Not paginated:
        `MAX_METADATA_ENTRIES` bounds a run's rows. The existence check and
        the rows are read from one snapshot."""
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

    def upsert_setting(
        self,
        namespace: str,
        key: str,
        *,
        value: str,
        updated_at: datetime,
        max_keys: int | None = None,
    ) -> bool:
        """Create or replace one `(namespace, key)` pair. Returns True only
        on a true first insert, mirroring `record_session`'s `created`
        boolean -- the route needs `201` versus `200`.

        With `max_keys`, a new key is refused with `NamespaceFullError`, and
        nothing written, when `namespace` already holds that many; replacing
        an existing key never is. The count and the write are one step, so
        concurrent callers cannot pass the bound together."""
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
