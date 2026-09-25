"""The shared `ExecutionStoreContract` run against `SqliteExecutionStore`, plus
SQLite-specific checks that read the database directly."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from vantage.core.ports.storage import ExecutionStore
from vantage.storage.sqlite_store import _LIST_RUNS_BY_METADATA, SqliteExecutionStore
from vantage_port_contract import ExecutionStoreContract, _execution, _start_only_execution


class TestSqliteExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[ExecutionStore]:
        adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
        yield adapter
        adapter.close()


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
            (200, 200, "firmware_version", "2.1", 21, 0),
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
    do_write = _write_run if write == "record_session" else _write_setting
    first, second = ("a" * 32, "b" * 32) if write == "record_session" else ("Billing", "Checkout")
    try:
        conn = store._conn  # noqa: SLF001
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        conn.execute("PRAGMA busy_timeout = 50")
        reader = sqlite3.connect(str(db_path), isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM run").fetchone()

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            do_write(store, first)

        assert conn.in_transaction is False
        assert not _was_written(store, write, first)

        reader.execute("COMMIT")
        reader.close()
        assert do_write(store, second) is True
        other = sqlite3.connect(str(db_path), timeout=0)
        try:
            assert other.execute("SELECT COUNT(*) FROM user_setting").fetchone() is not None
        finally:
            other.close()
    finally:
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
