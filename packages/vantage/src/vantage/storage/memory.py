"""An in-memory `ExecutionStore` for tests.

The shared contract suite (``vantage_port_contract.py``) runs against both
this and the SQLite adapter, so the port is proven by two independent
mechanisms rather than one implementation agreeing with itself. It is a real
implementation, not a stub: it mirrors the SQLite adapter's catalogue
monotonicity, first-write-wins results keyed by ``(run_id, node_id)``, and
the run upsert guard -- a finish-write (`exit_status` is not
`None`) applies over a start-only row, never the reverse.

``_last_contact`` is a separate dict because ``Execution`` carries no
``last_contact_at`` field; that column is a storage concern. It is set on
the insert branch of ``record_session`` and advanced only by
``touch_last_contact``, monotonically.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime

from vantage.core.domain.execution import Execution, VcsContext
from vantage.core.domain.projection import project_failure, project_vcs
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    MAX_PAGE_ITEMS,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    Page,
    ResultListEntry,
    RunDetail,
    RunListEntry,
    RunMetadata,
    UserSetting,
)


def _normalized_vcs(vcs: VcsContext | None) -> VcsContext | None:
    """An all-null `VcsContext` becomes `None` -- the rule the SQLite
    adapter's `_row_to_execution` applies on read, applied here on write
    because this adapter stores the object itself."""
    if vcs is None:
        return None
    if (
        vcs.commit is None
        and vcs.branch is None
        and vcs.commit_subject is None
        and vcs.dirty is None
        and vcs.root is None
    ):
        return None
    return vcs


class InMemoryExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` with dicts."""

    def __init__(self) -> None:
        self._executions: dict[str, Execution] = {}
        self._catalogue: dict[str, CatalogueEntry] = {}
        self._results: dict[tuple[str, str], Result] = {}
        self._last_contact: dict[str, datetime] = {}
        self._settings: dict[tuple[str, str], UserSetting] = {}
        self._metadata_files: dict[tuple[str, str], MetadataFile] = {}
        self._metadata_entries: dict[tuple[str, str], MetadataEntry] = {}

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
                stored.vcs if execution.vcs is None else execution.vcs.merged_over(stored.vcs)
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
                self._results[key] = result

        # `setdefault` mirrors the SQLite adapter's `INSERT OR IGNORE`: a
        # metadata file/entry is written once and never updated.
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

        # Mirrors the SQLite `DO UPDATE`: identity fields always refresh, but
        # `last_seen_at`/`last_seen_run_id` advance only when the new run is
        # strictly newer.
        advances = execution.started_at > existing.last_seen_at
        self._catalogue[identity.node_id] = CatalogueEntry(
            identity=identity,
            first_seen_at=existing.first_seen_at,
            last_seen_at=execution.started_at if advances else existing.last_seen_at,
            last_seen_run_id=execution.identity.value if advances else existing.last_seen_run_id,
        )

    def get_execution(self, execution_id: str) -> Execution | None:
        return self._executions.get(execution_id)

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        if execution_id not in self._executions:
            return False
        current = self._last_contact.get(execution_id)
        if current is not None and contacted_at <= current:
            return False
        self._last_contact[execution_id] = contacted_at
        return True

    def count_executions(self) -> int:
        return len(self._executions)

    def get_results(self, execution_id: str) -> Sequence[Result]:
        return [
            result for (run_id, _node_id), result in self._results.items() if run_id == execution_id
        ]

    def count_results(self) -> int:
        return len(self._results)

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        return self._catalogue.get(node_id)

    def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        metadata_key: str | None = None,
        metadata_value: str | None = None,
    ) -> Page[RunListEntry]:
        # Mirrors the SQLite adapter's `ORDER BY started_at DESC, id DESC` /
        # `LIMIT min(limit, 200) + 1 OFFSET`: fetching one extra row is what
        # sets `has_more` without a second query.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        candidates: Iterable[Execution] = self._executions.values()
        if metadata_key is not None and metadata_value is not None:
            # Mirrors `rm.key = ? AND rm.value = ?`: `entry.value` is `None`
            # for a declared-but-uncaptured key, so it never matches, just as
            # SQL NULL never equals a bound string.
            matching_run_ids = {
                run_id
                for (run_id, entry_key), entry in self._metadata_entries.items()
                if entry_key == metadata_key and entry.value == metadata_value
            }
            candidates = [
                execution
                for execution in candidates
                if execution.identity.value in matching_run_ids
            ]
        ordered = sorted(
            candidates,
            key=lambda execution: (execution.started_at, execution.identity.value),
            reverse=True,
        )
        window = ordered[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(
            RunListEntry(
                execution=replace(execution, vcs=None),
                last_contact_at=self._last_contact.get(execution.identity.value),
                vcs=project_vcs(execution.vcs),
            )
            for execution in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    def count_runs_predating_metadata_key(self, key: str) -> int:
        # A row for `key` of any status counts towards `first_seen`,
        # mirroring the SQLite adapter's `run_metadata` join, which does not
        # filter on `value` either.
        run_ids_with_key = {
            run_id for (run_id, entry_key) in self._metadata_entries if entry_key == key
        }
        if not run_ids_with_key:
            return len(self._executions)
        first_seen = min(
            self._executions[run_id].started_at
            for run_id in run_ids_with_key
            if run_id in self._executions
        )
        return sum(
            1 for execution in self._executions.values() if execution.started_at < first_seen
        )

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        execution = self._executions.get(execution_id)
        if execution is None:
            return None
        return RunDetail(
            execution=execution,
            last_contact_at=self._last_contact.get(execution_id),
        )

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        # The paginated, lean sibling of `get_results`, with the same
        # clamp/`has_more` mechanism as `list_runs`. Dict insertion order
        # mirrors the SQLite adapter's `ORDER BY r.id`, and `project_failure`
        # is the reference the SQLite adapter's SQL must agree with.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        matching = [
            result for (run_id, _node_id), result in self._results.items() if run_id == execution_id
        ]
        window = matching[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(
            ResultListEntry(
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
            for result in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        return self._results.get((execution_id, node_id))

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
            HistoryEntry(
                run_id=run_id,
                started_at=self._executions[run_id].started_at,
                finished_at=self._executions[run_id].finished_at,
                last_contact_at=self._last_contact.get(run_id),
                outcome=result.outcome,
                duration=result.duration,
                vcs=project_vcs(self._executions[run_id].vcs),
            )
            for run_id, result in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        # `sorted()` on `key` mirrors the SQLite adapter's `ORDER BY key`.
        matching = [
            setting
            for (setting_namespace, _key), setting in self._settings.items()
            if setting_namespace == namespace
        ]
        return tuple(sorted(matching, key=lambda setting: setting.key))

    def upsert_setting(self, namespace: str, key: str, *, value: str, updated_at: datetime) -> bool:
        identity = (namespace, key)
        created = identity not in self._settings
        self._settings[identity] = UserSetting(
            namespace=namespace, key=key, value=value, updated_at=updated_at
        )
        return created

    def delete_setting(self, namespace: str, key: str) -> bool:
        identity = (namespace, key)
        if identity not in self._settings:
            return False
        del self._settings[identity]
        return True

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        return tuple(
            (result.identity.file_path, result.outcome)
            for (run_id, _node_id), result in self._results.items()
            if run_id == execution_id
        )

    def close(self) -> None:
        self._executions.clear()
        self._catalogue.clear()
        self._results.clear()
        self._last_contact.clear()
        self._settings.clear()
        self._metadata_files.clear()
        self._metadata_entries.clear()
