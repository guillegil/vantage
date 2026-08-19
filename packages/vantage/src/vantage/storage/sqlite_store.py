"""SQLite adapter for `ExecutionStore` (RQ-30.1, RQ-38.1) -- design.md D5, D8,
D19-D22, D25, D26.

A run row is now written by `INSERT ... ON CONFLICT(id) DO UPDATE`, not
`DO NOTHING` (D25): a start-write and a finish-write share one `id`, and the
finish-write must be able to apply over a row the start-write already
created. The `DO UPDATE`'s own `WHERE run.exit_status IS NULL AND
excluded.exit_status IS NOT NULL` is the monotonic guard -- `exit_status`,
never `finished_at`, is the discriminator, because a Ctrl-C session reports
an integer exit status with a null finish time. `received_at` and
`started_at` are never advanced on the conflict path.

Under `DO UPDATE`, `cursor.rowcount` can no longer answer "was this row
created?" -- an applied conflict also reports one changed row (D26). One
`SELECT 1 FROM run WHERE id = ?` immediately after `BEGIN IMMEDIATE`, before
the write, decides `created` instead. This *is* a `SELECT`-then-write shape,
and the reasoning that used to rule that out still applies to every other
use of this connection -- but not here: `BEGIN IMMEDIATE` takes the
`RESERVED` lock for the whole transaction before the `SELECT` runs, and
`self._lock` serialises the server's threadpool, so the probe reads under
precisely the lock the write will write under. There is no window here for
two concurrent replays to both pass the `SELECT` and both attempt the write.

Concurrency is a two-layer net (D8), and neither layer alone is sufficient:
a `threading.Lock` held across the whole transaction serialises the several
threads of one process (the server runs this handler in a threadpool), while
`open_database`'s WAL mode, `check_same_thread=False` connection and 5s busy
timeout are the cross-process net for two separate processes sharing one
file -- no in-process lock can reach across a process boundary. Every write
takes the lock up front with `BEGIN IMMEDIATE`, never a deferred
transaction, because a deferred transaction that upgrades to a write
mid-statement is the classic two-writer deadlock.

Results and the catalogue join the same transaction (D22): run insert, the
catalogue upsert, the surrogate-key resolve, and the result insert are four
statements inside one `BEGIN IMMEDIATE` -- never four separate calls, and
never `RETURNING` (needs SQLite >= 3.35, above the 3.10 floor). The
catalogue upsert (D20) advances `last_seen_at` with `MAX` rather than an
unconditional overwrite, so a late-arriving report with an older
`run.started_at` cannot roll a test's last-seen timestamp backwards. The
result insert (D19 layer 3) is `ON CONFLICT(run_id, node_id, attempt) DO
NOTHING`, which makes a replayed report a silent no-op rather than an error
(RQ-41).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result
from vantage.storage.connection import open_database

# SQLITE_MAX_VARIABLE_NUMBER is 999 on older SQLite builds; 500 leaves
# headroom without needing to introspect the running library's compile-time
# limit (design.md D20).
_MAX_PLACEHOLDERS = 500

# `last_contact_at` is not written here yet -- the column does not exist
# until Phase 3 (design.md D25's note above the equivalent end-state block).
_UPSERT_RUN = """
    INSERT INTO run (
        id, received_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        finished_at      = excluded.finished_at,
        exit_status      = excluded.exit_status,
        interrupted      = excluded.interrupted,
        interrupt_reason = excluded.interrupt_reason
     WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL
"""

_PROBE_RUN_EXISTS = "SELECT 1 FROM run WHERE id = ?"

_SELECT_RUN = """
    SELECT id, started_at, finished_at, exit_status, interrupted, interrupt_reason
    FROM run WHERE id = ?
"""

# Conflict target is `node_id` -- the Phase 1 identity key (schema.sql
# comment). `stable_id` carries the identical string in Phase 1, so its own
# UNIQUE index cannot be violated by the row this statement updates.
_UPSERT_TEST_CASE = """
    INSERT INTO test_case (
        stable_id, node_id, file_path, class_name, function_name,
        param_id, first_seen_at, last_seen_at, last_seen_run_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(node_id) DO UPDATE SET
        file_path        = excluded.file_path,
        class_name       = excluded.class_name,
        function_name    = excluded.function_name,
        param_id         = excluded.param_id,
        last_seen_run_id = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.last_seen_run_id ELSE test_case.last_seen_run_id END,
        last_seen_at     = MAX(test_case.last_seen_at, excluded.last_seen_at)
"""

_INSERT_RESULT = """
    INSERT INTO result (
        run_id, test_case_id, node_id, outcome, duration, started_at, finished_at,
        setup_outcome, call_outcome, teardown_outcome,
        setup_duration, call_duration, teardown_duration, worker_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id, node_id, attempt) DO NOTHING
"""

_SELECT_RESULTS_FOR_RUN = """
    SELECT r.node_id, tc.file_path, tc.class_name, tc.function_name, tc.param_id,
           r.outcome, r.duration, r.started_at, r.finished_at,
           r.setup_outcome, r.call_outcome, r.teardown_outcome,
           r.setup_duration, r.call_duration, r.teardown_duration, r.worker_id
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
    ORDER BY r.id
"""

_SELECT_TEST_CASE = """
    SELECT node_id, file_path, class_name, function_name, param_id,
           first_seen_at, last_seen_at, last_seen_run_id
    FROM test_case WHERE node_id = ?
"""


def _row_to_execution(row: tuple[object, ...]) -> Execution:
    identity_value, started_at, finished_at, exit_status, interrupted, interrupt_reason = row
    # `id` and `started_at` are `NOT NULL` in `schema.sql` -- a `cast`, not a
    # runtime check, documents that without an assert statement (S101).
    return Execution(
        identity=Identity(cast(str, identity_value)),
        started_at=datetime.fromisoformat(cast(str, started_at)),
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        exit_status=exit_status if isinstance(exit_status, int) else None,
        interrupted=bool(interrupted),
        interrupt_reason=interrupt_reason if isinstance(interrupt_reason, str) else None,
    )


def _row_to_result(row: tuple[object, ...]) -> Result:
    (
        node_id,
        file_path,
        class_name,
        function_name,
        param_id,
        outcome,
        duration,
        started_at,
        finished_at,
        setup_outcome,
        call_outcome,
        teardown_outcome,
        setup_duration,
        call_duration,
        teardown_duration,
        worker_id,
    ) = row
    return Result(
        identity=CaseIdentity(
            node_id=cast(str, node_id),
            file_path=cast(str, file_path),
            class_name=cast("str | None", class_name),
            function_name=cast(str, function_name),
            param_id=cast("str | None", param_id),
        ),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
        started_at=datetime.fromisoformat(started_at) if isinstance(started_at, str) else None,
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        setup_outcome=cast("str | None", setup_outcome),
        call_outcome=cast("str | None", call_outcome),
        teardown_outcome=cast("str | None", teardown_outcome),
        setup_duration=cast("float | None", setup_duration),
        call_duration=cast("float | None", call_duration),
        teardown_duration=cast("float | None", teardown_duration),
        worker_id=cast("str | None", worker_id),
    )


def _row_to_catalogue_entry(row: tuple[object, ...]) -> CatalogueEntry:
    (
        node_id,
        file_path,
        class_name,
        function_name,
        param_id,
        first_seen_at,
        last_seen_at,
        last_seen_run_id,
    ) = row
    return CatalogueEntry(
        identity=CaseIdentity(
            node_id=cast(str, node_id),
            file_path=cast(str, file_path),
            class_name=cast("str | None", class_name),
            function_name=cast(str, function_name),
            param_id=cast("str | None", param_id),
        ),
        first_seen_at=datetime.fromisoformat(cast(str, first_seen_at)),
        last_seen_at=datetime.fromisoformat(cast(str, last_seen_at)),
        last_seen_run_id=cast("str | None", last_seen_run_id),
    )


def _catalogue_rows(
    execution: Execution, results: Sequence[Result]
) -> list[tuple[str, str, str, str | None, str, str | None, str, str, str]]:
    started_at = execution.started_at.isoformat()
    run_id = execution.identity.value
    # Keyed by node_id so a report carrying the same node id twice (should
    # not happen -- the service layer rejects it, D19 layer 2) still yields
    # one upsert row rather than a batch containing a duplicate key.
    by_node_id: dict[str, CaseIdentity] = {
        result.identity.node_id: result.identity for result in results
    }
    return [
        (
            identity.node_id,  # stable_id -- identical to node_id in Phase 1 (D20)
            identity.node_id,
            identity.file_path,
            identity.class_name,
            identity.function_name,
            identity.param_id,
            started_at,  # first_seen_at -- only used on INSERT, ignored on conflict
            started_at,  # last_seen_at -- the MAX/CASE clause decides on conflict
            run_id,  # last_seen_run_id
        )
        for identity in by_node_id.values()
    ]


def _result_rows(
    execution: Execution, results: Sequence[Result], test_case_ids: dict[str, int]
) -> list[tuple[object, ...]]:
    run_id = execution.identity.value
    return [
        (
            run_id,
            test_case_ids[result.identity.node_id],
            result.identity.node_id,
            result.outcome,
            result.duration,
            result.started_at.isoformat() if result.started_at is not None else None,
            result.finished_at.isoformat() if result.finished_at is not None else None,
            result.setup_outcome,
            result.call_outcome,
            result.teardown_outcome,
            result.setup_duration,
            result.call_duration,
            result.teardown_duration,
            result.worker_id,
        )
        for result in results
    ]


def _resolve_test_case_ids(conn: sqlite3.Connection, node_ids: Sequence[str]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for start in range(0, len(node_ids), _MAX_PLACEHOLDERS):
        batch = node_ids[start : start + _MAX_PLACEHOLDERS]
        # `placeholders` is built only from the literal `?` marker, repeated
        # once per batch item -- no value is ever interpolated into the SQL
        # text itself, so this is not the injection pattern S608 flags.
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT id, node_id FROM test_case WHERE node_id IN ({placeholders})",  # noqa: S608
            batch,
        ).fetchall()
        for row_id, row_node_id in rows:
            resolved[cast(str, row_node_id)] = cast(int, row_id)
    return resolved


class SqliteExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` against SQLite.

    One connection per process, `check_same_thread=False` (D8) -- opened via
    `open_database`, which already applies D9's permissions and WAL. Every
    write acquires `self._lock` for the whole transaction: the in-process
    half of D8's "neither lock alone is sufficient". `BEGIN IMMEDIATE` and
    the connection's busy timeout are the cross-process half, and neither
    substitutes for the other -- the Python lock has no effect on a second
    server process sharing the same file.
    """

    def __init__(self, path: Path) -> None:
        self._conn = open_database(path)
        self._lock = threading.Lock()

    def record_session(
        self, execution: Execution, *, results: Sequence[Result], received_at: datetime
    ) -> bool:
        # Five statements, one transaction, one fixed order (design.md D22,
        # extended by D26): existence probe, run upsert, catalogue upsert,
        # surrogate-key resolve, result insert. The order is required, not
        # tidy -- `PRAGMA foreign_keys=ON` is set on every connection, so
        # `result.run_id`, `test_case.last_seen_run_id` and
        # `result.test_case_id` each need their referent to exist first.
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                probe = self._conn.execute(
                    _PROBE_RUN_EXISTS, (execution.identity.value,)
                ).fetchone()
                created = probe is None

                self._conn.execute(
                    _UPSERT_RUN,
                    (
                        execution.identity.value,
                        received_at.isoformat(),
                        execution.started_at.isoformat(),
                        execution.finished_at.isoformat() if execution.finished_at else None,
                        execution.exit_status,
                        1 if execution.interrupted else 0,
                        execution.interrupt_reason,
                    ),
                )

                if results:
                    catalogue_rows = _catalogue_rows(execution, results)
                    self._conn.executemany(_UPSERT_TEST_CASE, catalogue_rows)

                    node_ids = [row[1] for row in catalogue_rows]
                    test_case_ids = _resolve_test_case_ids(self._conn, node_ids)

                    result_rows = _result_rows(execution, results, test_case_ids)
                    self._conn.executemany(_INSERT_RESULT, result_rows)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return created

    def get_execution(self, execution_id: str) -> Execution | None:
        row = self._conn.execute(_SELECT_RUN, (execution_id,)).fetchone()
        if row is None:
            return None
        return _row_to_execution(row)

    def count_executions(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM run").fetchone()
        return int(row[0])

    def get_results(self, execution_id: str) -> Sequence[Result]:
        rows = self._conn.execute(_SELECT_RESULTS_FOR_RUN, (execution_id,)).fetchall()
        return [_row_to_result(row) for row in rows]

    def count_results(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM result").fetchone()
        return int(row[0])

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        row = self._conn.execute(_SELECT_TEST_CASE, (node_id,)).fetchone()
        if row is None:
            return None
        return _row_to_catalogue_entry(row)

    def close(self) -> None:
        self._conn.close()
