"""An in-memory `ExecutionStore`, a test double kept beside the tests: the
`vantage` command always serves a `SqliteExecutionStore`, so nothing shipped
needs it.

The shared contract suite (``vantage_port_contract.py``) runs against both
this and the SQLite adapter, so the port is proven by two independent
mechanisms rather than one implementation agreeing with itself. It is a real
implementation, not a stub: it mirrors the SQLite adapter's catalogue
monotonicity, first-write-wins results keyed by ``(run_id, node_id)``, and
the run upsert guard -- a finish-write (`exit_status` is not `None`) applies
over a start-only row, never the reverse. It compares real `datetime`s where
the SQLite adapter compares fixed-width UTC text; the two orders agree.

A result's identity is read through the catalogue, as the SQLite adapter
reads it through its `test_case` row: one decomposition per node id, shared
by every run's result of it.

``_last_contact`` is a separate dict because ``Execution`` carries no
``last_contact_at`` field; that column is a storage concern. It is set on
the insert branch of ``record_session`` and advanced only by
``touch_last_contact``, monotonically.

The routes call a store from FastAPI's threadpool, so every public method
holds one lock, as every SQLite adapter call holds its own: a call sees and
leaves the dicts whole, and a check-then-write such as ``upsert_setting``'s
key bound is one step.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Concatenate, ParamSpec, TypeVar

from vantage.core.domain.execution import Execution, VcsContext
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    MAX_PAGE_ITEMS,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    Page,
    ResultListEntry,
    RunDetail,
    RunListEntry,
    RunMetadata,
    UserSetting,
)

# Empty VCS context and empty failure evidence become `None` here, on write,
# because this adapter stores the objects themselves; the SQLite adapter
# applies the same `is_empty` rule when it decodes a row.


def _normalized_vcs(vcs: VcsContext | None) -> VcsContext | None:
    return None if vcs is None or vcs.is_empty() else vcs


def _merged_vcs(incoming: VcsContext, previous: VcsContext | None) -> VcsContext:
    """Per-FIELD coalesce, the mirror of the SQLite adapter's per-column
    `COALESCE(excluded.vcs_*, run.vcs_*)`: null -> value only, never value
    -> null. The truncation flag follows whichever subject is kept."""
    if previous is None:
        return incoming
    keeps_subject = incoming.commit_subject is not None
    return VcsContext(
        commit=incoming.commit if incoming.commit is not None else previous.commit,
        branch=incoming.branch if incoming.branch is not None else previous.branch,
        commit_subject=incoming.commit_subject if keeps_subject else previous.commit_subject,
        commit_subject_truncated=incoming.commit_subject_truncated
        if keeps_subject
        else previous.commit_subject_truncated,
        dirty=incoming.dirty if incoming.dirty is not None else previous.dirty,
        root=incoming.root if incoming.root is not None else previous.root,
    )


def _normalized_result(result: Result) -> Result:
    if result.failure is not None and result.failure.is_empty():
        return replace(result, failure=None)
    return result


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _locked(
    method: Callable[Concatenate[InMemoryExecutionStore, _P], _R],
) -> Callable[Concatenate[InMemoryExecutionStore, _P], _R]:
    """Run `method` holding the store's lock."""

    @functools.wraps(method)
    def locked(store: InMemoryExecutionStore, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with store._lock:
            return method(store, *args, **kwargs)

    return locked


class InMemoryExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` with dicts."""

    def __init__(self) -> None:
        # Not re-entrant: no locked method calls another.
        self._lock = threading.Lock()
        self._executions: dict[str, Execution] = {}
        self._catalogue: dict[str, CatalogueEntry] = {}
        self._results: dict[tuple[str, str], Result] = {}
        self._last_contact: dict[str, datetime] = {}
        self._settings: dict[tuple[str, str], UserSetting] = {}
        self._metadata_files: dict[tuple[str, str], MetadataFile] = {}
        self._metadata_entries: dict[tuple[str, str], MetadataEntry] = {}

    @_locked
    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        # `received_at` is the server's clock, not the client's: it seeds
        # `_last_contact` for a new run and is not part of `Execution`.
        identity = execution.identity.value
        stored = self._executions.get(identity)
        created = stored is None
        if stored is None:
            self._executions[identity] = replace(execution, vcs=_normalized_vcs(execution.vcs))
            self._last_contact[identity] = received_at
        elif stored.exit_status is None and execution.exit_status is not None:
            # Mirrors the SQLite adapter's `DO UPDATE ... WHERE`: `exit_status`,
            # never `finished_at`, is the discriminator, and `started_at` is
            # never advanced on this path. `vcs` merges per field, like the
            # SQLite adapter's per-column `COALESCE`, so a partial snapshot
            # never nulls a stored value.
            merged_vcs = _normalized_vcs(
                stored.vcs if execution.vcs is None else _merged_vcs(execution.vcs, stored.vcs)
            )
            self._executions[identity] = Execution(
                identity=stored.identity,
                started_at=stored.started_at,
                finished_at=execution.finished_at,
                exit_status=execution.exit_status,
                interrupted=execution.interrupted,
                interrupt_reason=execution.interrupt_reason,
                vcs=merged_vcs,
            )

        for result in results:
            self._upsert_catalogue_entry(execution, result.identity)
            key = (identity, result.identity.node_id)
            if key not in self._results:
                self._results[key] = _normalized_result(result)

        # `setdefault` mirrors the SQLite adapter's `ON CONFLICT DO NOTHING`:
        # a metadata file/entry is written once and never updated.
        for metadata_file in metadata.files:
            self._metadata_files.setdefault((identity, metadata_file.source_file), metadata_file)
        for metadata_entry in metadata.entries:
            self._metadata_entries.setdefault((identity, metadata_entry.key), metadata_entry)

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

        # Mirrors the SQLite `DO UPDATE`: `first_seen_at` moves back to an
        # earlier run, and the identity, `last_seen_at` and
        # `last_seen_run_id` advance only when the new run is strictly newer.
        advances = execution.started_at > existing.last_seen_at
        self._catalogue[identity.node_id] = CatalogueEntry(
            identity=identity if advances else existing.identity,
            first_seen_at=min(existing.first_seen_at, execution.started_at),
            last_seen_at=execution.started_at if advances else existing.last_seen_at,
            last_seen_run_id=execution.identity.value if advances else existing.last_seen_run_id,
        )

    def _as_catalogued(self, result: Result) -> Result:
        """`result` with the identity its node id's catalogue entry holds,
        for a caller already holding the lock."""
        return replace(result, identity=self._catalogue[result.identity.node_id].identity)

    def _run_results(self, execution_id: str) -> list[Result]:
        """`execution_id`'s results in insertion order, identities read
        through the catalogue, for a caller already holding the lock."""
        return [
            self._as_catalogued(result)
            for (run_id, _node_id), result in self._results.items()
            if run_id == execution_id
        ]

    @_locked
    def get_execution(self, execution_id: str) -> Execution | None:
        return self._executions.get(execution_id)

    @_locked
    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        if execution_id not in self._executions:
            return False
        current = self._last_contact.get(execution_id)
        if current is not None and contacted_at <= current:
            return False
        self._last_contact[execution_id] = contacted_at
        return True

    @_locked
    def count_executions(self) -> int:
        return len(self._executions)

    @_locked
    def get_results(self, execution_id: str) -> Sequence[Result]:
        return self._run_results(execution_id)

    @_locked
    def count_results(self) -> int:
        return len(self._results)

    @_locked
    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        return self._catalogue.get(node_id)

    def _run_page(
        self, candidates: Iterable[Execution], *, limit: int, offset: int
    ) -> Page[RunListEntry]:
        """One page of `candidates`, for a caller already holding the lock.

        Mirrors the SQLite adapter's `ORDER BY started_at DESC, id DESC` /
        `LIMIT min(limit, 200) + 1 OFFSET`: fetching one extra row is what
        sets `has_more` without a second query."""
        page_limit = min(limit, MAX_PAGE_ITEMS)
        ordered = sorted(
            candidates,
            key=lambda execution: (execution.started_at, execution.identity.value),
            reverse=True,
        )
        window = ordered[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(
            RunListEntry.from_execution(
                execution, last_contact_at=self._last_contact.get(execution.identity.value)
            )
            for execution in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    @_locked
    def list_runs(self, *, limit: int, offset: int) -> Page[RunListEntry]:
        return self._run_page(self._executions.values(), limit=limit, offset=offset)

    @_locked
    def list_runs_with_metadata_horizon(
        self, *, key: str, value: str, limit: int, offset: int
    ) -> tuple[Page[RunListEntry], int]:
        # Both reads happen under one hold of the lock, so they describe one
        # state.
        #
        # Mirrors `rm.key = ? AND rm.value = ?`: `entry.value` is `None` for
        # a declared-but-uncaptured key, so it never matches, just as SQL
        # NULL never equals a bound string.
        matching_run_ids = {
            run_id
            for (run_id, entry_key), entry in self._metadata_entries.items()
            if entry_key == key and entry.value == value
        }
        page = self._run_page(
            (
                execution
                for execution in self._executions.values()
                if execution.identity.value in matching_run_ids
            ),
            limit=limit,
            offset=offset,
        )
        # A row for `key` of any status counts towards `first_seen`,
        # mirroring the SQLite adapter's `run_metadata` join, which does not
        # filter on `value` either.
        declared_at = [
            self._executions[run_id].started_at
            for (run_id, entry_key) in self._metadata_entries
            if entry_key == key
        ]
        if not declared_at:
            return page, len(self._executions)
        first_seen = min(declared_at)
        predating = sum(
            1 for execution in self._executions.values() if execution.started_at < first_seen
        )
        return page, predating

    @_locked
    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        execution = self._executions.get(execution_id)
        if execution is None:
            return None
        return RunDetail(
            execution=execution,
            last_contact_at=self._last_contact.get(execution_id),
        )

    @_locked
    def get_run_metadata(self, execution_id: str) -> Sequence[MetadataEntry] | None:
        # `sorted()` compares code points, the order the SQLite adapter's
        # `ORDER BY key` gets from comparing UTF-8 bytes.
        if execution_id not in self._executions:
            return None
        return tuple(
            sorted(
                (
                    entry
                    for (run_id, _key), entry in self._metadata_entries.items()
                    if run_id == execution_id
                ),
                key=lambda entry: entry.key,
            )
        )

    @_locked
    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        # The paginated, lean sibling of `get_results`, with the same
        # clamp/`has_more` mechanism as `list_runs`. Dict insertion order
        # mirrors the SQLite adapter's `ORDER BY r.id`.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        window = self._run_results(execution_id)[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(ResultListEntry.from_result(result) for result in window[:page_limit])
        return Page(items=items, has_more=has_more)

    @_locked
    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        result = self._results.get((execution_id, node_id))
        return None if result is None else self._as_catalogued(result)

    @_locked
    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        # Mirrors `list_runs`' total order -- `(started_at, run_id)`
        # descending -- over every execution that has a result for this
        # `node_id`. An unknown `node_id` yields an empty page, never an error.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        matches = [
            (run_id, result)
            for (run_id, result_node_id), result in self._results.items()
            if result_node_id == node_id
        ]
        ordered = sorted(
            matches,
            key=lambda pair: (self._executions[pair[0]].started_at, pair[0]),
            reverse=True,
        )
        window = ordered[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(
            HistoryEntry.from_execution(
                self._executions[run_id],
                last_contact_at=self._last_contact.get(run_id),
                outcome=result.outcome,
                duration=result.duration,
            )
            for run_id, result in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    @_locked
    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        # `sorted()` on `key` mirrors the SQLite adapter's `ORDER BY key`.
        matching = [
            setting
            for (setting_namespace, _key), setting in self._settings.items()
            if setting_namespace == namespace
        ]
        return tuple(sorted(matching, key=lambda setting: setting.key))

    @_locked
    def upsert_setting(
        self,
        namespace: str,
        key: str,
        *,
        value: str,
        updated_at: datetime,
        max_keys: int | None = None,
    ) -> bool:
        identity = (namespace, key)
        created = identity not in self._settings
        if created and max_keys is not None:
            held = sum(
                1 for setting_namespace, _key in self._settings if setting_namespace == namespace
            )
            if held >= max_keys:
                raise NamespaceFullError(f"{namespace!r} already holds {held} keys")
        self._settings[identity] = UserSetting(
            namespace=namespace, key=key, value=value, updated_at=updated_at
        )
        return created

    @_locked
    def delete_setting(self, namespace: str, key: str) -> bool:
        identity = (namespace, key)
        if identity not in self._settings:
            return False
        del self._settings[identity]
        return True

    @_locked
    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        return tuple(
            (result.identity.file_path, result.outcome)
            for result in self._run_results(execution_id)
        )

    @_locked
    def metadata(self, run_id: str) -> RunMetadata:
        """The metadata files and entries stored for `run_id`, for a test to
        inspect: the port never returns the files."""
        return RunMetadata(
            files=tuple(
                metadata_file
                for (stored_run_id, _source_file), metadata_file in self._metadata_files.items()
                if stored_run_id == run_id
            ),
            entries=tuple(
                entry
                for (stored_run_id, _key), entry in self._metadata_entries.items()
                if stored_run_id == run_id
            ),
        )

    @_locked
    def close(self) -> None:
        self._executions.clear()
        self._catalogue.clear()
        self._results.clear()
        self._last_contact.clear()
        self._settings.clear()
        self._metadata_files.clear()
        self._metadata_entries.clear()
