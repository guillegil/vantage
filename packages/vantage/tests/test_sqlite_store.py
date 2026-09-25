"""The shared `ExecutionStoreContract` run against `SqliteExecutionStore`, plus
SQLite-specific checks that read the database directly."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from vantage.core.domain.result import CapturedOutput
from vantage.core.ports.storage import ExecutionStore
from vantage.storage.connection import SchemaVersionError
from vantage.storage.sqlite_store import _LIST_RUNS_BY_METADATA, SqliteExecutionStore
from vantage_port_contract import ExecutionStoreContract, _execution, _start_only_execution

# A result insert naming none of the failure or captured-output columns, so
# they keep their schema defaults. Defined here rather than derived from
# `_INSERT_RESULT`, so edits to the adapter cannot change what this writes.
_INSERT_RESULT_WITHOUT_EVIDENCE = """
    INSERT INTO result (
        run_id, test_case_id, node_id, outcome, duration, started_at, finished_at,
        setup_outcome, call_outcome, teardown_outcome,
        setup_duration, call_duration, teardown_duration, worker_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


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


def test_a_result_row_without_evidence_columns_reads_back_with_no_failure_or_output(
    tmp_path: Path,
) -> None:
    """A result row written without any failure or captured-output column (all
    left at their schema defaults) reads back, through a freshly opened
    adapter, as `failure=None` and an all-`None` `CapturedOutput`."""
    db_path = tmp_path / "store" / "vantage.db"

    # The run goes through the adapter; the catalogue entry and the result
    # row are written directly, with the narrow insert above.
    writer = SqliteExecutionStore(db_path)
    try:
        execution = _execution("9" * 32)
        writer.record_session(execution, results=(), received_at=datetime.now(timezone.utc))
        conn = writer._conn  # noqa: SLF001
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO test_case (stable_id, node_id, file_path, class_name, function_name,"
            " param_id, first_seen_at, last_seen_at, last_seen_run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "t.py::test_old",
                "t.py::test_old",
                "t.py",
                None,
                "test_old",
                None,
                execution.started_at.isoformat(),
                execution.started_at.isoformat(),
                "9" * 32,
            ),
        )
        test_case_id = conn.execute(
            "SELECT id FROM test_case WHERE node_id = ?", ("t.py::test_old",)
        ).fetchone()[0]
        conn.execute(
            _INSERT_RESULT_WITHOUT_EVIDENCE,
            (
                "9" * 32,
                test_case_id,
                "t.py::test_old",
                "passed",
                0.01,
                execution.started_at.isoformat(),
                execution.started_at.isoformat(),
                "passed",
                "passed",
                "passed",
                0.001,
                0.001,
                0.001,
                None,
            ),
        )
        conn.execute("COMMIT")
    finally:
        writer.close()

    # Reopen the same file with a new adapter instance, which re-runs
    # `open_database`'s schema-version check.
    reader = SqliteExecutionStore(db_path)
    try:
        assert reader.get_execution("9" * 32) is not None

        result = reader.get_result("9" * 32, node_id="t.py::test_old")
        assert result is not None
        assert result.failure is None
        assert result.captured == CapturedOutput(
            stdout=None, stdout_truncated=False, stderr=None, stderr_truncated=False
        )
    finally:
        reader.close()


def test_a_v2_stamped_database_is_refused_naming_version_found_required_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database stamped with an older schema version is refused at open,
    naming the version found, the version required and the database path, and
    no schema script runs against it."""
    db_path = tmp_path / "store" / "vantage.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    seed = sqlite3.connect(str(db_path))
    try:
        seed.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        seed.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '2')")
        seed.commit()
    finally:
        seed.close()

    captured: list[str] = []

    class _SpyConnection(sqlite3.Connection):
        def executescript(self, sql_script: str) -> sqlite3.Cursor:
            captured.append(sql_script)
            return super().executescript(sql_script)

    real_connect = sqlite3.connect

    def _spy_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("factory", _SpyConnection)
        return cast(sqlite3.Connection, real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    with pytest.raises(SchemaVersionError) as exc_info:
        SqliteExecutionStore(db_path)

    message = str(exc_info.value)
    assert "schema_version is 2," in message
    assert "requires schema_version 4" in message
    assert str(db_path) in message
    assert captured == []


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
