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

`record_session` carries the same monotonic upsert guard as the SQLite
adapter's ``DO UPDATE`` branch (design.md D25): a finish-write (`exit_status`
is not `None`) applies over an existing start-only row (`stored.exit_status
is None`), never the reverse, and a report that changes nothing is a no-op.
The shared contract suite requires both adapters to agree, so this is a
second mechanism proving the guard, not a stub copying it.

``_last_contact`` is a second dict, keyed the same way as ``_executions``,
because ``Execution`` itself carries no ``last_contact_at`` field (design.md
D1) -- that column is a storage-adapter concern, not part of the domain
dataclass. It is set only on the insert branch of ``record_session`` (D27),
left alone on the conflict branch, and advanced by ``touch_last_contact``
under the same monotonic guard the SQLite adapter enforces with SQL (D33) --
no lexicographic-width hazard here, since real `datetime` objects compare
exactly, unlike the SQLite adapter's stored TEXT.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from vantage.core.domain.execution import Execution, VcsContext
from vantage.core.domain.projection import project_vcs
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result
from vantage.core.ports.storage import (
    MAX_PAGE_ITEMS,
    HistoryEntry,
    Page,
    RunDetail,
    RunListEntry,
)


def _normalized_vcs(vcs: VcsContext | None) -> VcsContext | None:
    """The same all-null normalisation rule the SQLite adapter's
    `_row_to_execution` applies on every read (design.md D48) -- applied
    here on write instead, because this adapter stores the Python object
    directly rather than re-deriving it from row columns on every read. In
    production `_to_execution` already normalises before either adapter
    sees the value; this is the second, independent enforcement `vcs=None`
    normalisation (task 4.5) proves at the contract level so the two
    adapters cannot drift apart."""
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


# `attempt` is not on the wire (design.md D19); every result in Phase 1/2/3
# is attempt 0, matching the schema's `DEFAULT 0`.
_ATTEMPT = 0


class InMemoryExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` with dicts."""

    def __init__(self) -> None:
        self._executions: dict[str, Execution] = {}
        self._catalogue: dict[str, CatalogueEntry] = {}
        self._results: dict[tuple[str, str, int], Result] = {}
        self._last_contact: dict[str, datetime] = {}

    def record_session(
        self, execution: Execution, *, results: Sequence[Result], received_at: datetime
    ) -> bool:
        # `received_at` is part of the port's signature (the two-clocks point,
        # design.md D1); `get_execution` still returns only what the client
        # reported, but `received_at` is now the insert-branch source for
        # `_last_contact` (D27) -- the one place this adapter's contract
        # surface reads it back is `touch_last_contact`'s monotonic guard.
        identity = execution.identity.value
        stored = self._executions.get(identity)
        created = stored is None
        if stored is None:
            self._executions[identity] = replace(execution, vcs=_normalized_vcs(execution.vcs))
            self._last_contact[identity] = received_at
        elif stored.exit_status is None and execution.exit_status is not None:
            # Mirrors the SQLite adapter's `DO UPDATE ... WHERE` (design.md
            # D25): `exit_status`, never `finished_at`, is the discriminator,
            # and `started_at` is never advanced on this path.
            #
            # `vcs` merges under the SAME guard, own task 4.12 (design.md
            # D48): `execution.vcs.merged_over(stored.vcs)` is the per-FIELD
            # coalesce -- null -> value only, never value -> null -- the
            # in-memory mirror of the SQLite adapter's per-column SQL
            # `COALESCE`. `stored.vcs if execution.vcs is None else
            # execution.vcs` (whole-object coalesce) is NOT the same rule:
            # it diverges the moment one report carries a partial snapshot
            # (a detached HEAD, a repository with no commits), which a
            # per-field merge tolerates and a whole-object swap does not.
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
            result
            for (run_id, _node_id, _attempt), result in self._results.items()
            if run_id == execution_id
        ]

    def count_results(self) -> int:
        return len(self._results)

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        return self._catalogue.get(node_id)

    def list_runs(self, *, limit: int, offset: int) -> Page[RunListEntry]:
        # Python sort/slice mirrors the SQLite adapter's `ORDER BY
        # started_at DESC, id DESC` / `LIMIT min(limit, 200) + 1 OFFSET`
        # exactly (design.md D57, D61): sorting descending on the same
        # two-key tuple as the SQL `ORDER BY` gives the identical total
        # order, and slicing `page_limit + 1` rows is the same
        # truncation-vs-exhaustion signal without a second pass.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        ordered = sorted(
            self._executions.values(),
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

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        execution = self._executions.get(execution_id)
        if execution is None:
            return None
        return RunDetail(
            execution=execution,
            last_contact_at=self._last_contact.get(execution_id),
        )

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[Result]:
        # Same clamp/`has_more` mechanism as `list_runs` (design.md D57,
        # D61) -- the paginated sibling of `get_results`. Dict insertion
        # order mirrors the SQLite adapter's `ORDER BY r.id`.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        matching = [
            result
            for (run_id, _node_id, _attempt), result in self._results.items()
            if run_id == execution_id
        ]
        window = matching[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        return Page(items=tuple(window[:page_limit]), has_more=has_more)

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        # Mirrors `list_runs`' total order -- `(started_at, run_id)`
        # descending -- over every execution that has a result for this
        # `node_id` (design.md D57, D61, D63). An unknown `node_id` matches
        # nothing and yields an empty page, never an error.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        matches = [
            (run_id, result)
            for (run_id, result_node_id, _attempt), result in self._results.items()
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

    def close(self) -> None:
        self._executions.clear()
        self._catalogue.clear()
        self._results.clear()
        self._last_contact.clear()
