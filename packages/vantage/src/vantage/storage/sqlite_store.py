"""SQLite adapter for `ExecutionStore` (RQ-30.1, RQ-38.1) -- design.md D5, D8.

Idempotency is settled inside the `INSERT` itself, never by a preceding
`SELECT`: `record_execution` uses `INSERT ... ON CONFLICT(id) DO NOTHING` and
reads the row count the INSERT itself produced to decide its boolean return
(D3, D5). A `SELECT`-then-`INSERT` shape would leave a window in which two
concurrent replays of the same `run.id` both pass the `SELECT` and both
attempt the `INSERT` -- the exact race this adapter exists to avoid.

Concurrency is a two-layer net (D8), and neither layer alone is sufficient:
a `threading.Lock` held across the whole transaction serialises the several
threads of one process (the server runs this handler in a threadpool), while
`open_database`'s WAL mode, `check_same_thread=False` connection and 5s busy
timeout are the cross-process net for two separate processes sharing one
file -- no in-process lock can reach across a process boundary. Every write
takes the lock up front with `BEGIN IMMEDIATE`, never a deferred
transaction, because a deferred transaction that upgrades to a write
mid-statement is the classic two-writer deadlock.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import cast

from vantage.core.domain.execution import Execution, Identity
from vantage.storage.connection import open_database

_INSERT_RUN = """
    INSERT INTO run (
        id, received_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO NOTHING
"""

_SELECT_RUN = """
    SELECT id, started_at, finished_at, exit_status, interrupted, interrupt_reason
    FROM run WHERE id = ?
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

    def record_execution(self, execution: Execution, *, received_at: datetime) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    _INSERT_RUN,
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
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            created = cursor.rowcount == 1
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

    def close(self) -> None:
        self._conn.close()
