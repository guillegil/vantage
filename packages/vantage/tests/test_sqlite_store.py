"""RQ-30.1: the core's storage contract, run against the SQLite adapter.

Completes RQ-30.1: the same `ExecutionStoreContract` (`test_memory_store.py`
runs it against `InMemoryExecutionStore`) now runs unchanged against
`SqliteExecutionStore`, proving the port was never shaped around one
implementation.
"""

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

# The pre-`failure-capture` `_INSERT_RESULT` shape (14 bound columns, no
# failure/captured-output columns at all) -- kept here as a fixture-building
# constant, never imported from `sqlite_store.py`, so a future edit to the
# CURRENT `_INSERT_RESULT` cannot accidentally rewrite history under this
# test's feet.
_OLD_INSERT_RESULT = """
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


@pytest.mark.req(id="RQ-3")
def test_finish_write_leaves_received_at_started_at_and_last_contact_at_untouched(
    tmp_path: Path,
) -> None:
    """W2: `received_at`, `started_at` and `last_contact_at` are not fields
    on `Execution`, so no contract test built on `get_execution` can observe
    them -- they are read directly off the `run` row here instead. Adding
    `last_contact_at = excluded.last_contact_at` to `_UPSERT_RUN`'s `DO
    UPDATE SET` list currently leaves every other test in this suite green
    while making a finished run's last contact jump on a later finish-write
    -- exactly the "a finished run is not stale" invariant D27 states.
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

        # A DIFFERENT start time on the finish-write, deliberately. Handing it
        # the same `started` literal made the assertion below compare a value
        # with itself: it held whether or not the upsert overwrote the column,
        # and adding `started_at = excluded.started_at` to the DO UPDATE list
        # left the whole suite green. Its `received_at` and `last_contact_at`
        # siblings already bite, because their values differ.
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


@pytest.mark.req(id="RQ-44")
def test_touch_last_contact_normalizes_a_non_utc_contact_before_storing_it(
    tmp_path: Path,
) -> None:
    """`touch_last_contact` is a public port method: its signature accepts any
    aware `datetime`, not only the UTC ones the route happens to pass today.

    Stamping a `+02:00` value with a `+00:00` suffix would store it two hours
    ahead of the truth and then compare it lexicographically against
    genuinely-UTC rows. The in-memory adapter compares real `datetime` objects
    and gets this input right, so trusting the caller is also what would make
    the two adapters disagree on an input the shared contract suite never
    exercises. Found by review, 2026-08-19.
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
    """design.md D48, task 4.4: a run recorded with `vcs=None` (outside a
    repository) must write SQL `NULL` to `vcs_branch`, not `''` -- asserted
    via `typeof(...)`, which distinguishes the two, never falsy-equality
    (`not value`), which `''` would also satisfy."""
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


def test_an_existing_pre_change_database_opens_unrefused_and_reads_back_its_rows(
    tmp_path: Path,
) -> None:
    """ADR-0013's non-firing, proven not assumed (design.md D80): a
    database written by the pre-`failure-capture` 14-column
    `_INSERT_RESULT` (`schema_version` stays `2`, unchanged by this whole
    change -- `git diff schema.sql` is empty, RQ-29) opens unrefused under
    the widened adapter, and its pre-existing row reads back with `NULL` in
    every new failure/captured-output column."""
    db_path = tmp_path / "store" / "pre_change.db"

    # Phase 1: write the fixture with the OLD 14-column result insert,
    # directly against a freshly-opened connection -- a database this
    # change's widened `_INSERT_RESULT` never wrote a row into. The run row
    # and catalogue entry go through the ordinary (unaffected) API.
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
            _OLD_INSERT_RESULT,
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

    # Phase 2: open the SAME file as a brand-new adapter instance -- the
    # assertion under test. Constructing `SqliteExecutionStore` re-runs
    # `open_database`'s schema-version check; it must not raise.
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
    """design.md D82: this change bumps `meta.schema_version` from 2 to 3.
    A database stamped `2` by an earlier release -- exactly what every
    developer database looks like before this change -- must be refused at
    open, naming the version found, the version required and the database
    path (ADR-0013), and it must issue no schema-altering statement in the
    process (RQ-29's refusal scenario)."""
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
    assert "2" in message
    assert "3" in message
    assert str(db_path) in message
    assert captured == []


def test_list_runs_by_metadata_uses_the_key_value_index(tmp_path: Path) -> None:
    """sdd-verify CRITICAL-2: `_LIST_RUNS_BY_METADATA` MUST reach the
    read filter's whole reason to exist -- `idx_run_metadata_key_value`
    (schema.sql, docs/schema-manifest.md) -- rather than a full scan of
    `run` with one correlated subquery per row.

    A prior `WHERE EXISTS (SELECT 1 FROM run_metadata rm WHERE rm.run_id =
    run.id AND rm.key = ? AND rm.value = ?)` form correlated the subquery on
    `rm.run_id = run.id`, so SQLite's planner anchored there and preferred
    `run_metadata`'s own `PRIMARY KEY (run_id, key)` autoindex instead --
    `idx_run_metadata_key_value` was never touched, and cost was O(total
    runs) rather than O(matching runs). Results were correct either way;
    only the plan regressed silently, which is exactly why this asserts the
    plan and not just the rows -- `test_run_list_metadata_filter_returns_
    only_matching_runs` (`test_routes_read.py`) already covers correctness.

    No `ANALYZE` is run here, deliberately: production never runs it
    either (no `sqlite_stat1` table exists), so the no-stats plan asserted
    here is the plan production actually gets, not an optimistic one only
    reachable after statistics collection.
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
