"""The shared `ExecutionStoreContract` run against `SqliteExecutionStore`, plus
SQLite-specific checks that read the database directly."""

from __future__ import annotations

import functools
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

import pytest
from sqlite_rows import read_metadata
from vantage.core.domain.access import (
    LOGIN_TOKEN_LABEL,
    LOGIN_TOKEN_LIFETIME,
    MANAGE_SCOPE,
    token_digest,
)
from vantage.core.domain.projects import DEFAULT_PROJECT, EDITOR_ROLE, VIEWER_ROLE, Membership
from vantage.core.ports.storage import (
    ExecutionStore,
    MetadataEntry,
    MetadataFile,
    RunMetadata,
)
from vantage.storage import sqlite_store
from vantage.storage.connection import isoformat_utc
from vantage.storage.sqlite_store import (
    _BRANCH_BASELINE,
    _COUNT_RUNS_PREDATING_KEY,
    _LIST_HISTORY,
    _LIST_RUNS,
    _LIST_RUNS_AFTER,
    _LIST_SUBJECT_PREFIX_BYTES,
    _PROJECT_BASELINE,
    _SELECT_RESULT_CHANGE,
    _SELECT_RUN,
    SqliteExecutionStore,
    _count_changes,
    _count_outcomes,
    _list_changes,
    _list_results_with_outcomes,
    _list_runs_by_metadata,
)
from vantage_port_contract import (
    _HASH,
    ExecutionStoreContract,
    LocalDatabaseContract,
    StoredMetadata,
    _at,
    _captured,
    _compared_run,
    _execution,
    _failure,
    _result,
    _start_only_execution,
)

_Row = TypeVar("_Row", MetadataFile, MetadataEntry)


class TestSqliteExecutionStore(ExecutionStoreContract, LocalDatabaseContract):
    @pytest.fixture
    def database(self, tmp_path: Path) -> Path:
        return tmp_path / "store" / "vantage.db"

    @pytest.fixture
    def store(self, database: Path) -> Iterator[ExecutionStore]:
        adapter = SqliteExecutionStore(database)
        yield adapter
        adapter.close()

    @pytest.fixture
    def local_store(self, database: Path) -> Iterator[ExecutionStore]:
        adapter = SqliteExecutionStore(database, local=True)
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
        store.record_session(start, results=(), received_at=received, project=DEFAULT_PROJECT)

        select_run = "SELECT received_at, started_at, last_contact_at FROM run WHERE id = ?"
        before = store._conn.execute(select_run, (identity,)).fetchone()  # noqa: SLF001

        # A different start time on the finish-write, so the `started_at`
        # assertion below fails if the upsert overwrites the column.
        disagreeing_start = started + timedelta(hours=3)
        finish = _execution(identity, finished=True, started=disagreeing_start)
        later_received = received + timedelta(hours=1)
        store.record_session(
            finish, results=(), received_at=later_received, project=DEFAULT_PROJECT
        )

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
            _start_only_execution(identity, started=started),
            results=(),
            received_at=started,
            project=DEFAULT_PROJECT,
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
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

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


@pytest.mark.parametrize("after", [False, True], ids=["first-page", "after-a-key"])
@pytest.mark.parametrize("pair_count", [1, 3])
def test_list_runs_by_metadata_uses_the_key_value_index(
    tmp_path: Path, pair_count: int, after: bool
) -> None:
    """`_list_runs_by_metadata` seeks `idx_run_metadata_key_value` once per
    pair, then looks each run it found up by primary key, rather than
    scanning `run` with one probe per row.

    A correlated `EXISTS` form returns the same rows but makes SQLite prefer
    `run_metadata`'s primary-key autoindex, so cost grows with the total run
    count; comparing the project in a form SQLite can use
    `idx_run_project_started_at` for makes it walk every run of the project
    and probe the pair list for each. Either regression is silent, which is
    why this asserts the plan; `test_routes_read.py` covers the rows.

    No `ANALYZE` is run: production never runs it either, so the no-statistics
    plan asserted here is the plan production gets.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    run_key = ("2026-08-15T09:00:00.000000+00:00", "a" * 32) if after else ()
    try:
        plan_rows = store._conn.execute(  # noqa: SLF001
            f"EXPLAIN QUERY PLAN {_list_runs_by_metadata(pair_count, after=after)}",
            (
                _LIST_SUBJECT_PREFIX_BYTES,
                *["firmware_version", "2.1"] * pair_count,
                DEFAULT_PROJECT,
                *run_key,
                21,
                0,
            ),
        ).fetchall()
        plan_text = "\n".join(str(row[-1]) for row in plan_rows)

        assert plan_text.count("USING INDEX idx_run_metadata_key_value") == pair_count
        assert "SEARCH run USING INDEX sqlite_autoindex_run_1 (id=?)" in plan_text
        assert "idx_run_project_started_at" not in plan_text
        assert "sqlite_autoindex_run_metadata_1" not in plan_text
    finally:
        store.close()


def test_the_metadata_horizon_finds_a_keys_first_run_through_the_key_value_index(
    tmp_path: Path,
) -> None:
    """`_COUNT_RUNS_PREDATING_KEY` finds the earliest run holding the key by
    seeking `idx_run_metadata_key_value` on the key alone, then each run by
    primary key. Driven from the project's own index instead, it reads
    every run of the project and probes its metadata for the key, so each
    filtered page costs as much as the project has runs."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        plan_rows = store._conn.execute(  # noqa: SLF001
            f"EXPLAIN QUERY PLAN {_COUNT_RUNS_PREDATING_KEY}",
            (DEFAULT_PROJECT, DEFAULT_PROJECT, "firmware_version", DEFAULT_PROJECT),
        ).fetchall()
        plan_text = "\n".join(str(row[-1]) for row in plan_rows)

        assert "SEARCH rm USING INDEX idx_run_metadata_key_value (key=?)" in plan_text
        assert "SEARCH run USING INDEX sqlite_autoindex_run_1 (id=?)" in plan_text
        assert "sqlite_autoindex_run_metadata_1" not in plan_text
    finally:
        store.close()


def test_the_run_list_after_a_key_starts_its_index_scan_at_the_key(tmp_path: Path) -> None:
    """`_LIST_RUNS_AFTER` bounds `idx_run_project_started_at` at the project
    and the key, so a late page costs what the first one does. A predicate
    SQLite could not bound the index with would return the same rows,
    reading every run of the project older than the key to find them --
    silently, which is why this asserts the plan."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    started_at = "2026-08-15T09:00:00.000000+00:00"
    try:
        plan_rows = store._conn.execute(  # noqa: SLF001
            f"EXPLAIN QUERY PLAN {_LIST_RUNS_AFTER}",
            (_LIST_SUBJECT_PREFIX_BYTES, DEFAULT_PROJECT, started_at, "a" * 32, 21, 0),
        ).fetchall()
        plan_text = "\n".join(str(row[-1]) for row in plan_rows)

        assert (
            "SEARCH run USING INDEX idx_run_project_started_at"
            " (project=? AND (started_at,id)<(?,?))" in plan_text
        )
    finally:
        store.close()


def test_a_runs_outcomes_are_counted_and_filtered_along_its_results_index(
    tmp_path: Path,
) -> None:
    """`_count_outcomes` seeks `idx_result_run_id` once per run of the page,
    and the outcome filter reads the one run's results through it, so
    neither costs more as other runs' results pile up."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        plans = [
            store._conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()  # noqa: SLF001
            for sql, params in (
                (_count_outcomes(3), ("a" * 32, "b" * 32, "c" * 32)),
                (_list_results_with_outcomes(2), (100, "a" * 32, "failed", "error", 21, 0)),
            )
        ]

        for plan_rows in plans:
            plan_text = "\n".join(str(row[-1]) for row in plan_rows)
            assert "USING INDEX idx_result_run_id (run_id=?)" in plan_text, plan_text
    finally:
        store.close()


def _write_run(store: SqliteExecutionStore, hex_id: str) -> bool:
    return store.record_session(
        _execution(hex_id),
        results=(),
        received_at=datetime.now(timezone.utc),
        project=DEFAULT_PROJECT,
    )


def _write_setting(store: SqliteExecutionStore, key: str) -> bool:
    return store.upsert_setting(
        "test_sections",
        key,
        value="{}",
        updated_at=datetime.now(timezone.utc),
        project=DEFAULT_PROJECT,
    )


def _was_written(store: SqliteExecutionStore, write: str, key: str) -> bool:
    if write == "record_session":
        return store.get_execution(key) is not None
    return any(
        setting.key == key
        for setting in store.list_settings("test_sections", project=DEFAULT_PROJECT)
    )


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
    table = "run" if write == "record_session" else "project_setting"
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
                project=DEFAULT_PROJECT,
            )

    try:
        for i in range(3):
            other_process.record_session(
                _execution(f"{i:032x}", started=base + timedelta(minutes=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )
        store._conn.set_trace_callback(_commit_a_keyed_run_during_the_horizon_read)  # noqa: SLF001

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1")], limit=10, offset=0, project=DEFAULT_PROJECT
        )

        assert fired, "the horizon statement never ran"
        assert (len(page.items), predating) in {(0, (3,)), (1, (0,))}
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
            project=DEFAULT_PROJECT,
        )
        store.upsert_setting(
            "test_sections", "Billing", value="{}", updated_at=whole_second, project=DEFAULT_PROJECT
        )
        store.create_project("firmware", created_at=whole_second)
        store.create_first_admin("admin", password_hash=_HASH, created_at=whole_second)
        store.create_login_token(
            "admin",
            password_hash=_HASH,
            digest=token_digest("login"),
            created_at=whole_second,
            expires_at=whole_second + LOGIN_TOKEN_LIFETIME,
        )
        store.set_password("admin", password_hash=_HASH, changed_at=whole_second)
        conn = store._conn  # noqa: SLF001
        stored = [
            *conn.execute(
                "SELECT received_at, last_contact_at, started_at, finished_at FROM run"
            ).fetchone(),
            *conn.execute("SELECT started_at, finished_at FROM result").fetchone(),
            *conn.execute("SELECT first_seen_at, last_seen_at FROM test_case").fetchone(),
            *conn.execute("SELECT updated_at FROM project_setting").fetchone(),
            *(value for (value,) in conn.execute("SELECT created_at FROM project")),
            *conn.execute("SELECT value FROM meta WHERE key = 'created_at'").fetchone(),
            *conn.execute("SELECT created_at FROM account").fetchone(),
            # A login token's expiry is compared as text with the moment a
            # token is used and a login is made.
            *conn.execute("SELECT created_at, revoked_at, expires_at FROM access_token").fetchone(),
        ]
    finally:
        store.close()

    # Both projects' rows, `default`'s and the one made here, were read, and
    # the admin's and its login token's.
    assert len(stored) == 16
    assert [value for value in stored if not _FIXED_WIDTH_UTC.fullmatch(value)] == []


def test_a_timestamp_from_an_early_year_is_stored_zero_padded(tmp_path: Path) -> None:
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    started = datetime(100, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    try:
        store.record_session(
            _execution("a" * 32, started=started),
            results=(),
            received_at=started,
            project=DEFAULT_PROJECT,
        )
        raw = store._conn.execute("SELECT started_at FROM run").fetchone()  # noqa: SLF001
        found = store.get_execution("a" * 32)
    finally:
        store.close()

    assert raw == ("0100-01-01T09:00:00.000000+00:00",)
    assert found is not None
    assert found.started_at == started


_ADMIN_AT = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("made_locally", "gets_an_admin"),
    [(True, False), (False, True)],
    ids=["local-store-made-it", "server-made-it"],
)
def test_the_maker_of_a_database_decides_its_first_admin_whoever_opens_it_now(
    tmp_path: Path, made_locally: bool, gets_an_admin: bool
) -> None:
    """`vantage` serving the file a local session made gives it no admin,
    while a local session opening the database `vantage` made first does
    not keep it from getting one: what counts is what `meta.origin` says,
    not how the file is opened now."""
    database = tmp_path / "store" / "vantage.db"
    SqliteExecutionStore(database, local=made_locally).close()

    store = SqliteExecutionStore(database, local=not made_locally)
    try:
        created = store.create_first_admin("admin", password_hash=_HASH, created_at=_ADMIN_AT)
        users = store.list_users()
    finally:
        store.close()

    assert (created is not None) is gets_an_admin
    assert [user.name for user in users] == (["admin"] if gets_an_admin else [])


@pytest.mark.parametrize(
    "origin",
    [None, "server", "Local", "local ", ""],
    ids=["missing", "server", "capitalised", "trailing-space", "empty"],
)
def test_only_the_local_stores_own_origin_keeps_a_database_from_its_first_admin(
    tmp_path: Path, origin: str | None
) -> None:
    """A database whose origin is missing or unknown -- a file edited by
    hand -- is taken for a server's: it gets an admin rather than being
    served open."""
    database = tmp_path / "store" / "vantage.db"
    SqliteExecutionStore(database, local=True).close()
    with closing(sqlite3.connect(database)) as conn, conn:
        if origin is None:
            conn.execute("DELETE FROM meta WHERE key = 'origin'")
        else:
            conn.execute("UPDATE meta SET value = ? WHERE key = 'origin'", (origin,))

    store = SqliteExecutionStore(database)
    try:
        created = store.create_first_admin("admin", password_hash=_HASH, created_at=_ADMIN_AT)
    finally:
        store.close()

    assert created is not None


_INSERT_MEMBER = "INSERT INTO project_member (project, account, role) VALUES (?, ?, ?)"


@pytest.mark.parametrize(
    "role",
    ["admin", "Owner", "owner ", ""],
    ids=["not-a-role", "capitalised", "trailing-space", "empty"],
)
def test_the_schema_refuses_a_member_row_holding_a_role_outside_the_roles(
    tmp_path: Path, role: str
) -> None:
    """The store refuses such a role before writing; the CHECK keeps one
    written any other way out too, so every row read back holds a role
    `effective_role` knows."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        store.create_user("alice", admin=False, created_at=_ADMIN_AT)
        store.create_project("firmware", created_at=_ADMIN_AT)
        conn = store._conn  # noqa: SLF001

        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(_INSERT_MEMBER, ("firmware", "alice", role))
        conn.execute(_INSERT_MEMBER, ("firmware", "alice", VIEWER_ROLE))

        members = store.list_members(project="firmware")
    finally:
        store.close()

    assert members == (Membership(project="firmware", user="alice", role=VIEWER_ROLE),)


@pytest.mark.parametrize(
    ("project", "account"), [("nope", "alice"), ("firmware", "nobody")], ids=["project", "user"]
)
def test_the_schema_refuses_a_member_row_naming_a_missing_project_or_user(
    tmp_path: Path, project: str, account: str
) -> None:
    """Projects and users are never deleted, so a row naming one keeps
    naming one; the foreign keys keep a row written past the store's probes
    from naming nothing in the first place."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        store.create_user("alice", admin=False, created_at=_ADMIN_AT)
        store.create_project("firmware", created_at=_ADMIN_AT)
        conn = store._conn  # noqa: SLF001

        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(_INSERT_MEMBER, (project, account, VIEWER_ROLE))

        stored = conn.execute("SELECT COUNT(*) FROM project_member").fetchone()
    finally:
        store.close()

    assert stored == (0,)


def test_a_member_row_no_store_may_write_is_refused_before_any_statement(tmp_path: Path) -> None:
    """`default` and a role outside the roles are refused before the store
    runs anything -- not even `BEGIN IMMEDIATE`, so a refusal never waits
    for another process's write lock."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    statements: list[str] = []
    try:
        store._conn.set_trace_callback(statements.append)  # noqa: SLF001
        for project, role in ((DEFAULT_PROJECT, EDITOR_ROLE), ("nope", "admin")):
            with pytest.raises(ValueError):
                store.set_member("nobody", project=project, role=role)
    finally:
        store._conn.set_trace_callback(None)  # noqa: SLF001
        store.close()

    assert statements == []


def test_each_scope_is_stored_in_a_flag_column_of_its_own(tmp_path: Path) -> None:
    """`manage` has a column of its own, set for a made token holding it
    and for every login token, whose `can_record` never is. Read straight
    off the rows, since decoding the columns the way they were encoded
    would hide a flag stored in another scope's column."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        store.create_first_admin("admin", password_hash=_HASH, created_at=_ADMIN_AT)
        store.create_user("alice", admin=False, created_at=_ADMIN_AT)
        store.set_password("alice", password_hash=_HASH, changed_at=_ADMIN_AT)
        store.create_token(
            "alice",
            digest=token_digest("made"),
            label="made",
            scopes=frozenset({MANAGE_SCOPE}),
            created_at=_ADMIN_AT,
        )
        for user in ("alice", "admin"):
            store.create_login_token(
                user,
                password_hash=_HASH,
                digest=token_digest(f"login-{user}"),
                created_at=_ADMIN_AT,
                expires_at=_ADMIN_AT + LOGIN_TOKEN_LIFETIME,
            )
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT account, label, can_read, can_record, can_manage, can_admin"
            " FROM access_token ORDER BY id"
        ).fetchall()
    finally:
        store.close()

    assert rows == [
        ("alice", "made", 0, 0, 1, 0),
        ("alice", LOGIN_TOKEN_LABEL, 1, 0, 1, 0),
        ("admin", LOGIN_TOKEN_LABEL, 1, 0, 1, 1),
    ]


def _forced(row: _Row, **fields: str | None) -> _Row:
    """`row` with values the core refuses, forced past its validation -- the
    shape a caller bypassing the domain types could hand the adapter."""
    for name, value in fields.items():
        object.__setattr__(row, name, value)
    return row


_VALID_FILE = MetadataFile(source_file="m.json", content_type="json", status="captured")
_VALID_ENTRY = MetadataEntry(key="fw", value="2.1", source_file="m.json", status="captured")
_SESSION_ENTRY = MetadataEntry(
    key="bench", value="lab-3", source_file=None, status="captured", source="session"
)

# Every table `record_session` writes.
_SESSION_TABLES = (
    "run",
    "test_case",
    "result",
    "run_metadata_file",
    "run_metadata",
    "result_change",
)


@pytest.mark.parametrize(
    ("files", "entries"),
    [
        ((_forced(replace(_VALID_FILE), content_type="xml"),), (_VALID_ENTRY,)),
        ((_forced(replace(_VALID_FILE), status="bogus"),), (_VALID_ENTRY,)),
        ((_VALID_FILE,), (_forced(replace(_VALID_ENTRY), status="bogus"),)),
        ((_VALID_FILE,), (_forced(replace(_VALID_ENTRY), source="bogus"),)),
        ((_VALID_FILE,), (_forced(replace(_VALID_ENTRY), source_file=None),)),
        ((_VALID_FILE,), (_forced(replace(_SESSION_ENTRY), source_file="m.json"),)),
    ],
    ids=[
        "content-type",
        "file-status",
        "entry-status",
        "entry-source",
        "file-entry-without-file",
        "session-entry-with-file",
    ],
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
                project=DEFAULT_PROJECT,
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
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
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
        adapter.record_session(
            start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        counting = _CommitCountingConnection(adapter._conn)
        adapter._conn = counting  # type: ignore[assignment]

        finish = _execution(identity, finished=True, started=started)
        results = [_result(f"packages/vantage/tests/test_bulk.py::test_{i}") for i in range(500)]

        created = adapter.record_session(
            finish, results=results, received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
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

        created = adapter.record_session(
            start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

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
        adapter.record_session(
            finish, results=results, received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        counting = _CommitCountingConnection(adapter._conn)
        adapter._conn = counting  # type: ignore[assignment]

        late_start = _start_only_execution(identity, started=started)
        created = adapter.record_session(
            late_start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
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


def _plan(store: SqliteExecutionStore, sql: str, params: Sequence[object]) -> str:
    """The plan SQLite picks for `sql`, without statistics, as production
    never runs `ANALYZE`."""
    rows = store._conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()  # noqa: SLF001
    return "\n".join(str(row[-1]) for row in rows)


def test_a_finishing_runs_baseline_is_found_by_one_backward_seek(tmp_path: Path) -> None:
    """Both baseline statements run inside the write lock, so each must
    start at the finishing run's key, in an index of complete runs alone: a
    plan that scanned the project's history -- or, for a new branch, all of
    it -- or one that passed over every run stopped early on the way would
    hold every writer for as long."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    key = ("2026-08-15T09:00:00.000000+00:00", "a" * 32)
    try:
        branch = _plan(store, _BRANCH_BASELINE, (DEFAULT_PROJECT, "main", *key))
        project = _plan(store, _PROJECT_BASELINE, (DEFAULT_PROJECT, *key))
    finally:
        store.close()

    assert branch == (
        "SEARCH run USING INDEX idx_run_baseline_branch"
        " (project=? AND vcs_branch=? AND (started_at,id)<(?,?))"
    )
    assert project == (
        "SEARCH run USING INDEX idx_run_baseline (project=? AND (started_at,id)<(?,?))"
    )


def test_a_baseline_seek_passes_over_no_run_that_cannot_be_one(tmp_path: Path) -> None:
    """A suite run with `-x` that keeps failing stops early every time, so
    none of its runs is ever complete. Finding that no baseline exists must
    still cost one step, not a read back through every such run inside the
    write lock: the work the seeks do is the same for 5 such runs as for
    300."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    key = (isoformat_utc(_at(10_000)), "f" * 32)

    def steps(sql: str, params: Sequence[object]) -> int:
        """The virtual machine instructions `sql` runs."""
        counted = [0]

        def count() -> int:
            counted[0] += 1
            return 0

        store._conn.set_progress_handler(count, 1)  # noqa: SLF001
        try:
            assert store._conn.execute(sql, params).fetchall() == []  # noqa: SLF001
        finally:
            store._conn.set_progress_handler(None, 1)  # noqa: SLF001
        return counted[0]

    def both() -> tuple[int, int]:
        return (
            steps(_BRANCH_BASELINE, (DEFAULT_PROJECT, "main", *key)),
            steps(_PROJECT_BASELINE, (DEFAULT_PROJECT, *key)),
        )

    def stopped_early(numbers: range) -> None:
        for number in numbers:
            store.record_session(
                _compared_run(number, number, exit_status=1, reason="stopped after 1 failure"),
                results=(),
                received_at=_at(number),
                project=DEFAULT_PROJECT,
            )

    try:
        stopped_early(range(1, 6))
        few = both()
        stopped_early(range(6, 301))
        many = both()
    finally:
        store.close()

    assert many == few


def test_changes_are_read_along_their_own_indexes(tmp_path: Path) -> None:
    """A page's change counts come from `idx_result_change_run` alone; one
    kind of change is read in order from it, without a sort; a history
    entry's change and a result's are found by `result_change`'s primary
    key; and a result's position is counted along `idx_result_run_id`
    alone. None of them costs more as other runs pile up."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        counts = _plan(store, _count_changes(3), ("a" * 32, "b" * 32, "c" * 32))
        one_kind = _plan(store, _list_changes(1), ("a" * 32, "fixed", 21, 0))
        history = _plan(
            store, _LIST_HISTORY, (_LIST_SUBJECT_PREFIX_BYTES, DEFAULT_PROJECT, "t.py::x", 21, 0)
        )
        result = _plan(store, _SELECT_RESULT_CHANGE, ("a" * 32, "t.py::x"))
    finally:
        store.close()

    assert counts == "SEARCH result_change USING COVERING INDEX idx_result_change_run (run_id=?)"
    assert "SEARCH rc USING INDEX idx_result_change_run (run_id=? AND change=?)" in one_kind
    assert "TEMP B-TREE" not in one_kind
    assert (
        "SEARCH rc USING INDEX sqlite_autoindex_result_change_1 (run_id=? AND test_case_id=?)"
        in history
    )
    assert (
        "SEARCH rc USING INDEX sqlite_autoindex_result_change_1 (run_id=? AND test_case_id=?)"
        in result
    )
    assert "SEARCH earlier USING COVERING INDEX idx_result_run_id (run_id=? AND rowid<?)" in result


def test_a_run_list_reads_each_baseline_by_primary_key(tmp_path: Path) -> None:
    """A page and its comparisons are one statement: each run's baseline is
    looked up by id, and the run list keeps its own index."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    key = ("2026-08-15T09:00:00.000000+00:00", "a" * 32)
    try:
        plans = [
            _plan(store, _LIST_RUNS, (_LIST_SUBJECT_PREFIX_BYTES, DEFAULT_PROJECT, 21, 0)),
            _plan(
                store, _LIST_RUNS_AFTER, (_LIST_SUBJECT_PREFIX_BYTES, DEFAULT_PROJECT, *key, 21, 0)
            ),
            _plan(
                store,
                _list_runs_by_metadata(1),
                (_LIST_SUBJECT_PREFIX_BYTES, "k", "v", DEFAULT_PROJECT, 21, 0),
            ),
            _plan(store, _SELECT_RUN, ("a" * 32,)),
        ]
    finally:
        store.close()

    for plan in plans:
        assert "SEARCH base USING INDEX sqlite_autoindex_run_1 (id=?) LEFT-JOIN" in plan, plan
    assert "SEARCH run USING INDEX idx_run_project_started_at (project=?)" in plans[0]


def test_a_finish_whose_comparison_fails_stores_nothing_of_the_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison is written in the transaction of the report that
    finishes the run, so a finish is stored compared or not at all: a run
    never reads as final without its comparison."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    started = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    try:
        store.record_session(
            _execution("1" * 32, started=started),
            results=(_result("t.py::test_a"),),
            received_at=started,
            project=DEFAULT_PROJECT,
        )

        def _failing(*_args: object, **_kwargs: object) -> list[object]:
            raise RuntimeError("compare failed")

        monkeypatch.setattr(sqlite_store, "compare", _failing)
        with pytest.raises(RuntimeError, match="compare failed"):
            store.record_session(
                _execution("2" * 32, started=started + timedelta(minutes=1)),
                results=(_result("t.py::test_a", outcome="failed"),),
                received_at=started,
                project=DEFAULT_PROJECT,
            )

        assert store.get_execution("2" * 32) is None
        assert store.count_results() == 1
        monkeypatch.undo()
        assert _write_run(store, "3" * 32) is True
    finally:
        store.close()
