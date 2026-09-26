"""The shared `ExecutionStoreContract` run against `SqliteExecutionStore`, plus
SQLite-specific checks that read the database directly."""

from __future__ import annotations

import functools
import re
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

import pytest
from sqlite_rows import read_metadata
from vantage.core.ports.storage import (
    ExecutionStore,
    MetadataEntry,
    MetadataFile,
    RunMetadata,
)
from vantage.storage.sqlite_store import (
    _LIST_RUNS_BY_METADATA,
    _LIST_SUBJECT_PREFIX_BYTES,
    SqliteExecutionStore,
)
from vantage_port_contract import (
    ExecutionStoreContract,
    StoredMetadata,
    _captured,
    _execution,
    _failure,
    _result,
    _start_only_execution,
)

_Row = TypeVar("_Row", MetadataFile, MetadataEntry)


class TestSqliteExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def database(self, tmp_path: Path) -> Path:
        return tmp_path / "store" / "vantage.db"

    @pytest.fixture
    def store(self, database: Path) -> Iterator[ExecutionStore]:
        adapter = SqliteExecutionStore(database)
        yield adapter
        adapter.close()

    @pytest.fixture
    def stored_metadata(self, database: Path) -> StoredMetadata:
        return functools.partial(read_metadata, database)


def test_finish_write_leaves_received_at_started_at_and_last_contact_at_untouched(
    tmp_path: Path,
) -> None:
    """A finish-write leaves `received_at`, `started_at` and `last_contact_at`
    as the start-write set them -- a finished run's last contact must not jump
    forward. None of the three is a field on `Execution`, so they are read off
    the `run` row directly.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        identity = "5" * 32
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        received = datetime(2026, 8, 15, 9, 0, 1, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        store.record_session(start, results=(), received_at=received)

        select_run = "SELECT received_at, started_at, last_contact_at FROM run WHERE id = ?"
        before = store._conn.execute(select_run, (identity,)).fetchone()  # noqa: SLF001

        # A different start time on the finish-write, so the `started_at`
        # assertion below fails if the upsert overwrites the column.
        disagreeing_start = started + timedelta(hours=3)
        finish = _execution(identity, finished=True, started=disagreeing_start)
        later_received = received + timedelta(hours=1)
        store.record_session(finish, results=(), received_at=later_received)

        after = store._conn.execute(select_run, (identity,)).fetchone()  # noqa: SLF001

        before_received_at, before_started_at, before_last_contact_at = before
        after_received_at, after_started_at, after_last_contact_at = after
        assert after_received_at == before_received_at
        assert after_started_at == before_started_at
        assert after_last_contact_at == before_last_contact_at
    finally:
        store.close()


def test_touch_last_contact_normalizes_a_non_utc_contact_before_storing_it(
    tmp_path: Path,
) -> None:
    """`touch_last_contact` accepts any aware `datetime`, not only UTC.

    Stamping a `+02:00` value with a `+00:00` suffix would store it two hours
    off and break the text comparison against UTC rows; the in-memory adapter
    compares real `datetime` objects, so the two adapters would also disagree.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        identity = "7" * 32
        started = datetime(2026, 8, 19, 9, 0, 0, tzinfo=timezone.utc)
        store.record_session(
            _start_only_execution(identity, started=started), results=(), received_at=started
        )

        # 12:00+02:00 is 10:00 UTC -- one hour after the start, not three.
        in_madrid = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert store.touch_last_contact(identity, in_madrid) is True

        stored = store._conn.execute(  # noqa: SLF001
            "SELECT last_contact_at FROM run WHERE id = ?", (identity,)
        ).fetchone()[0]
        assert datetime.fromisoformat(stored) == in_madrid
        assert stored.endswith("+00:00")
        assert stored.startswith("2026-08-19T10:00:00")
    finally:
        store.close()


def test_vcs_branch_is_sql_null_not_empty_string_for_a_run_outside_a_repository(
    tmp_path: Path,
) -> None:
    """A run recorded with `vcs=None` (outside a repository) must write SQL
    `NULL` to `vcs_branch`, not `''` -- asserted via `typeof(...)`, which
    distinguishes the two, never falsy-equality (`not value`), which `''` would
    also satisfy."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        identity = "8" * 32
        execution = _execution(identity, vcs=None)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        row = store._conn.execute(  # noqa: SLF001
            "SELECT typeof(vcs_branch), typeof(vcs_commit), typeof(vcs_commit_subject),"
            " typeof(vcs_dirty), typeof(vcs_root), typeof(vcs_commit_subject_truncated)"
            " FROM run WHERE id = ?",
            (identity,),
        ).fetchone()

        (
            branch_type,
            commit_type,
            subject_type,
            dirty_type,
            root_type,
            truncated_type,
        ) = row
        assert branch_type == "null"
        assert commit_type == "null"
        assert subject_type == "null"
        assert dirty_type == "null"
        assert root_type == "null"
        # `vcs_commit_subject_truncated` is `INTEGER NOT NULL DEFAULT 0` --
        # unlike its five siblings it is never SQL NULL.
        assert truncated_type == "integer"
    finally:
        store.close()


def test_list_runs_by_metadata_uses_the_key_value_index(tmp_path: Path) -> None:
    """`_LIST_RUNS_BY_METADATA` uses `idx_run_metadata_key_value` rather than
    scanning `run` with one correlated subquery per row.

    A correlated `EXISTS` form returns the same rows but makes SQLite prefer
    `run_metadata`'s primary-key autoindex, so cost grows with the total run
    count. That regression is silent, which is why this asserts the plan;
    `test_routes_read.py` covers the rows.

    No `ANALYZE` is run: production never runs it either, so the no-statistics
    plan asserted here is the plan production gets.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        plan_rows = store._conn.execute(  # noqa: SLF001
            f"EXPLAIN QUERY PLAN {_LIST_RUNS_BY_METADATA}",
            (_LIST_SUBJECT_PREFIX_BYTES, "firmware_version", "2.1", 21, 0),
        ).fetchall()
        plan_text = "\n".join(str(row[-1]) for row in plan_rows)

        assert "idx_run_metadata_key_value" in plan_text
        assert "sqlite_autoindex_run_metadata_1" not in plan_text
    finally:
        store.close()


def _write_run(store: SqliteExecutionStore, hex_id: str) -> bool:
    return store.record_session(
        _execution(hex_id), results=(), received_at=datetime.now(timezone.utc)
    )


def _write_setting(store: SqliteExecutionStore, key: str) -> bool:
    return store.upsert_setting(
        "test_sections", key, value="{}", updated_at=datetime.now(timezone.utc)
    )


def _was_written(store: SqliteExecutionStore, write: str, key: str) -> bool:
    if write == "record_session":
        return store.get_execution(key) is not None
    return any(setting.key == key for setting in store.list_settings("test_sections"))


@pytest.mark.parametrize("write", ["record_session", "upsert_setting"])
def test_a_commit_refused_by_a_busy_reader_is_rolled_back_and_the_store_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write: str
) -> None:
    """Without WAL -- a filesystem that cannot hold the shared-memory file
    -- COMMIT needs an exclusive lock, and another process's open read
    transaction makes it fail with SQLITE_BUSY while leaving the write
    transaction open. The store must roll it back: otherwise its one shared
    connection stays inside that transaction, every later write fails with
    "cannot start a transaction within a transaction", and the rejected
    write is visible to the store's own reads."""
    monkeypatch.setattr("vantage.storage.connection._enable_wal", lambda _conn: None)
    db_path = tmp_path / "store" / "vantage.db"
    store = SqliteExecutionStore(db_path)
    reader = sqlite3.connect(str(db_path), isolation_level=None)
    do_write = _write_run if write == "record_session" else _write_setting
    table = "run" if write == "record_session" else "user_setting"
    first, second = ("a" * 32, "b" * 32) if write == "record_session" else ("Billing", "Checkout")
    try:
        conn = store._conn  # noqa: SLF001
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        conn.execute("PRAGMA busy_timeout = 50")
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM run").fetchone()

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            do_write(store, first)

        assert conn.in_transaction is False
        assert not _was_written(store, write, first)

        reader.execute("COMMIT")
        assert do_write(store, second) is True
        # Another connection, as another process has, reads without waiting
        # -- the store holds no lock any more -- and sees only the write
        # that landed.
        reader.execute("PRAGMA busy_timeout = 0")
        assert reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)  # noqa: S608
    finally:
        reader.close()
        store.close()


class _CommitRolledBackBySqlite:
    """Forwards to a real connection, except that `COMMIT` fails the way a
    disk I/O error does: SQLite has already rolled the transaction back."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        if sql == "COMMIT":
            self._conn.execute("ROLLBACK")
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_a_commit_sqlite_already_rolled_back_raises_its_own_error(tmp_path: Path) -> None:
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    real_conn = store._conn  # noqa: SLF001
    try:
        store._conn = _CommitRolledBackBySqlite(real_conn)  # type: ignore[assignment]  # noqa: SLF001

        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            _write_run(store, "a" * 32)

        store._conn = real_conn  # noqa: SLF001
        assert _write_run(store, "a" * 32) is True
    finally:
        real_conn.close()


def test_the_filtered_page_and_its_horizon_are_read_from_one_snapshot(tmp_path: Path) -> None:
    """A second server process on the same file commits a keyed run while
    this one is between the page and the horizon. Both must describe the
    same state of the database: before the commit, no match and every run
    predating the never-declared key; after it, one match and nothing
    older than it. Never the page from before and the count from after."""
    db_path = tmp_path / "store" / "vantage.db"
    store = SqliteExecutionStore(db_path)
    other_process = SqliteExecutionStore(db_path)
    base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    keyed = RunMetadata(
        files=(MetadataFile(source_file="m.json", content_type="json", status="captured"),),
        entries=(MetadataEntry(key="fw", value="2.1", source_file="m.json", status="captured"),),
    )
    fired: list[str] = []

    def _commit_a_keyed_run_during_the_horizon_read(statement: str) -> None:
        if not fired and "MIN(" in statement:
            fired.append(statement)
            other_process.record_session(
                _execution("f" * 32, started=base - timedelta(hours=1)),
                results=(),
                received_at=base,
                metadata=keyed,
            )

    try:
        for i in range(3):
            other_process.record_session(
                _execution(f"{i:032x}", started=base + timedelta(minutes=i)),
                results=(),
                received_at=base,
            )
        store._conn.set_trace_callback(_commit_a_keyed_run_during_the_horizon_read)  # noqa: SLF001

        page, predating = store.list_runs_with_metadata_horizon(
            key="fw", value="2.1", limit=10, offset=0
        )

        assert fired, "the horizon statement never ran"
        assert (len(page.items), predating) in {(0, 3), (1, 0)}
    finally:
        store._conn.set_trace_callback(None)  # noqa: SLF001
        other_process.close()
        store.close()


_FIXED_WIDTH_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00")


def test_every_stored_timestamp_is_fixed_width_utc_text(tmp_path: Path) -> None:
    """Timestamps are compared as text (`MAX`, `ORDER BY`, `<`), which is
    chronological only when every value has one offset and one width.
    `isoformat()` drops the fraction when it is zero and keeps the caller's
    offset, so neither may reach a column."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    whole_second = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    try:
        store.record_session(
            _execution("a" * 32, started=whole_second),
            results=(_result("t.py::test_x"),),
            received_at=whole_second,
        )
        store.upsert_setting("test_sections", "Billing", value="{}", updated_at=whole_second)
        conn = store._conn  # noqa: SLF001
        stored = [
            *conn.execute(
                "SELECT received_at, last_contact_at, started_at, finished_at FROM run"
            ).fetchone(),
            *conn.execute("SELECT started_at, finished_at FROM result").fetchone(),
            *conn.execute("SELECT first_seen_at, last_seen_at FROM test_case").fetchone(),
            *conn.execute("SELECT updated_at FROM user_setting").fetchone(),
            *conn.execute("SELECT value FROM meta WHERE key = 'created_at'").fetchone(),
        ]
    finally:
        store.close()

    assert [value for value in stored if not _FIXED_WIDTH_UTC.fullmatch(value)] == []


def test_a_timestamp_from_an_early_year_is_stored_zero_padded(tmp_path: Path) -> None:
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    started = datetime(100, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    try:
        store.record_session(_execution("a" * 32, started=started), results=(), received_at=started)
        raw = store._conn.execute("SELECT started_at FROM run").fetchone()  # noqa: SLF001
        found = store.get_execution("a" * 32)
    finally:
        store.close()

    assert raw == ("0100-01-01T09:00:00.000000+00:00",)
    assert found is not None
    assert found.started_at == started


def _forced(row: _Row, **fields: str) -> _Row:
    """`row` with values the core refuses, forced past its validation -- the
    shape a caller bypassing the domain types could hand the adapter."""
    for name, value in fields.items():
        object.__setattr__(row, name, value)
    return row


_VALID_FILE = MetadataFile(source_file="m.json", content_type="json", status="captured")
_VALID_ENTRY = MetadataEntry(key="fw", value="2.1", source_file="m.json", status="captured")

# Every table `record_session` writes.
_SESSION_TABLES = ("run", "test_case", "result", "run_metadata_file", "run_metadata")


@pytest.mark.parametrize(
    ("files", "entries"),
    [
        ((_forced(replace(_VALID_FILE), content_type="xml"),), (_VALID_ENTRY,)),
        ((_forced(replace(_VALID_FILE), status="bogus"),), (_VALID_ENTRY,)),
        ((_VALID_FILE,), (_forced(replace(_VALID_ENTRY), status="bogus"),)),
    ],
    ids=["content-type", "file-status", "entry-status"],
)
def test_a_metadata_row_the_schema_refuses_rolls_back_the_whole_session(
    tmp_path: Path, files: tuple[MetadataFile, ...], entries: tuple[MetadataEntry, ...]
) -> None:
    """Metadata is write-once, but only a repeated key may be skipped: a row
    outside the CHECK vocabulary is an error, and the session it arrived
    with is not stored at all rather than stored without it."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            store.record_session(
                _execution("a" * 32),
                results=(_result("t.py::test_x"),),
                received_at=datetime.now(timezone.utc),
                metadata=RunMetadata(files=files, entries=entries),
            )

        conn = store._conn  # noqa: SLF001
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in _SESSION_TABLES
        }
        assert counts == dict.fromkeys(_SESSION_TABLES, 0)
        assert _write_run(store, "b" * 32) is True
    finally:
        store.close()


class _CommitCountingConnection:
    """Wraps a real `sqlite3.Connection`, counting only `COMMIT` statements.

    `record_session` must reach storage in exactly one commit for the whole
    batch, not one per result or one per statement, so a report is stored
    completely or not at all. Wrapping the connection observes that from
    outside `SqliteExecutionStore` without weakening the adapter's own
    commit discipline for the sake of a test.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.commit_count = 0

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Cursor:
        if sql.strip() == "COMMIT":
            self.commit_count += 1
        return self._real.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> sqlite3.Cursor:
        return self._real.executemany(sql, seq_of_parameters)

    def close(self) -> None:
        self._real.close()


def test_finish_report_reaches_storage_in_one_commit(tmp_path: Path) -> None:
    """A 500-result finish report reaches storage in exactly one commit, and
    the finish fields and every result row are actually written.

    A handful of the 500 carry failure evidence and captured output, so the
    single commit is checked at the full width of `_INSERT_RESULT`, and a
    failing result's evidence must round-trip.
    """
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    counting = _CommitCountingConnection(adapter._conn)
    adapter._conn = counting  # type: ignore[assignment]

    try:
        execution = _execution("f" + "0" * 31)
        failure = _failure()
        captured = _captured(stdout="some output", stderr="")
        results = [
            _result(
                f"packages/vantage/tests/test_bulk.py::test_{i}",
                outcome="failed" if i < 5 else "passed",
                failure=failure if i < 5 else None,
                captured=captured if i < 5 else None,
            )
            for i in range(500)
        ]

        created = adapter.record_session(
            execution, results=results, received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert counting.commit_count == 1
        # Counting commits proves the transaction's shape, not its content:
        # an adapter that committed once and wrote no rows would satisfy the
        # count alone.
        assert adapter.count_results() == 500
        assert adapter.count_executions() == 1
        stored = adapter.get_execution(execution.identity.value)
        assert stored is not None
        assert stored.finished_at == execution.finished_at
        assert stored.exit_status == execution.exit_status

        found = adapter.get_result(
            execution.identity.value,
            node_id="packages/vantage/tests/test_bulk.py::test_0",
        )
        assert found is not None
        assert found.failure == failure
        assert found.captured == captured
    finally:
        adapter.close()


def test_finish_report_after_an_accepted_start_write_reaches_storage_in_one_commit(
    tmp_path: Path,
) -> None:
    """The same finish-write, run after a prior accepted start-write for the
    same run id -- one commit, the same 500 result rows, and the finish
    fields actually applied through the conflict (`DO UPDATE`) branch rather
    than the insert branch."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")

    try:
        identity = "f" + "1" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        adapter.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        counting = _CommitCountingConnection(adapter._conn)
        adapter._conn = counting  # type: ignore[assignment]

        finish = _execution(identity, finished=True, started=started)
        results = [_result(f"packages/vantage/tests/test_bulk.py::test_{i}") for i in range(500)]

        created = adapter.record_session(
            finish, results=results, received_at=datetime.now(timezone.utc)
        )

        assert created is False
        assert counting.commit_count == 1
        assert adapter.count_results() == 500
        assert adapter.count_executions() == 1
        stored = adapter.get_execution(identity)
        assert stored is not None
        assert stored.finished_at == finish.finished_at
        assert stored.exit_status == finish.exit_status
    finally:
        adapter.close()


def test_start_write_reaches_storage_in_one_commit(tmp_path: Path) -> None:
    """A start-write reaches storage in one commit: one run row, a null
    `finished_at`, and zero result rows, since a start report carries
    none."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    counting = _CommitCountingConnection(adapter._conn)
    adapter._conn = counting  # type: ignore[assignment]

    try:
        identity = "f" + "2" * 31
        start = _start_only_execution(identity)

        created = adapter.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        assert created is True
        assert counting.commit_count == 1
        assert adapter.count_results() == 0
        assert adapter.count_executions() == 1
        stored = adapter.get_execution(identity)
        assert stored is not None
        assert stored.finished_at is None
    finally:
        adapter.close()


def test_reordered_start_write_never_nulls_a_recorded_finish(tmp_path: Path) -> None:
    """A start-write arriving after the finish-write for the same run is one
    commit and leaves the recorded finish intact."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")

    try:
        identity = "f" + "3" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        finish = _execution(identity, finished=True, started=started)
        results = [_result("packages/vantage/tests/test_bulk.py::test_reordered")]
        adapter.record_session(finish, results=results, received_at=datetime.now(timezone.utc))

        counting = _CommitCountingConnection(adapter._conn)
        adapter._conn = counting  # type: ignore[assignment]

        late_start = _start_only_execution(identity, started=started)
        created = adapter.record_session(
            late_start, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created is False
        assert counting.commit_count == 1
        stored = adapter.get_execution(identity)
        assert stored is not None
        assert stored.finished_at == finish.finished_at
        assert stored.exit_status == finish.exit_status
        assert adapter.count_results() == 1
    finally:
        adapter.close()
