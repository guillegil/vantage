"""`open_database` applies `schema.sql` once, inside one transaction, never
re-issues DDL against an existing database, and refuses -- leaving it as it
was -- a database stamped with a different schema version or holding some
other schema.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.storage.connection import open_database
from vantage.storage.version import _SCHEMA_VERSION, SchemaVersionError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_SQL = _REPO_ROOT / "packages" / "vantage" / "src" / "vantage" / "storage" / "schema.sql"

# `CREATE TABLE foo (` or `CREATE UNIQUE INDEX foo` -- captures the token right
# after TABLE/INDEX so the test can tell "IF" (as in "IF NOT EXISTS") apart
# from a bare identifier.
_DDL_HEAD = re.compile(r"CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX)\s+(\S+)", re.IGNORECASE)


def _statements_missing_if_not_exists(sql: str) -> list[str]:
    return [
        f"{kind.upper()} {next_token}"
        for kind, next_token in _DDL_HEAD.findall(sql)
        if next_token.upper() != "IF"
    ]


def _spy_on_executescript(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Patch `sqlite3.connect` to hand back a connection subclass that records
    every script `executescript` runs.

    `sqlite3.Connection` is a C extension type -- its methods are read-only
    and cannot be monkeypatched directly, either on the class or on an
    instance. Subclassing it and injecting the subclass via `connect`'s own
    `factory` parameter is the supported way to observe its calls.
    `_apply_schema` is the only thing in `connection.py` that calls
    `executescript` -- PRAGMAs and the schema-presence check both go through
    plain `execute`, so a call recorded here is unambiguously a DDL
    application, and zero calls unambiguously means none happened.
    """
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
    return captured


def test_a_fresh_database_is_stamped_with_the_current_schema_version(tmp_path: Path) -> None:
    conn = open_database(tmp_path / "store" / "vantage.db")
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    finally:
        conn.close()

    assert row == (str(_SCHEMA_VERSION),)


def test_a_new_database_is_stamped_8_and_holds_the_default_project_alone(
    tmp_path: Path,
) -> None:
    """Version 8 is the schema with projects, and a report naming no project
    is recorded in `default`, so a new database has that project before
    anything is written to it -- and no other."""
    conn = open_database(tmp_path / "store" / "vantage.db")
    try:
        stamp = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        projects = conn.execute("SELECT name, created_at FROM project").fetchall()
    finally:
        conn.close()

    assert stamp == ("8",)
    assert [name for name, _created_at in projects] == [DEFAULT_PROJECT]
    ((_name, created_at),) = projects
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", created_at)


@pytest.mark.parametrize(
    "statement",
    ["_STAMP_SCHEMA_VERSION", "_CREATE_DEFAULT_PROJECT"],
    ids=["stamp", "default-project"],
)
def test_the_tables_and_the_version_stamp_commit_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    """A stamp or a `default` project that fails after every table was
    created leaves no table behind: a database with a schema but no stamp
    would be refused as 'absent' on every later open, and one without its
    `default` project would refuse every report that names none."""
    db_path = tmp_path / "store" / "vantage.db"
    monkeypatch.setattr(
        f"vantage.storage.connection.{statement}",
        "INSERT INTO no_such_table (value) VALUES (?)",
    )

    with pytest.raises(sqlite3.OperationalError, match="no_such_table"):
        open_database(db_path)

    probe = sqlite3.connect(str(db_path))
    try:
        tables = probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        probe.close()
    assert tables == []


def test_the_default_project_is_written_in_the_transaction_that_creates_the_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure right after the `default` project was written takes the
    project, the stamp and every table with it. Written in a transaction of
    its own, the project would outlive the failure, or the schema would be
    committed without it."""
    real_connect = sqlite3.connect

    class _FailingAfterTheProject(sqlite3.Connection):
        def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
            cursor = super().execute(sql, *args)
            if sql.startswith("INSERT OR IGNORE INTO project"):
                raise sqlite3.OperationalError("provoked after the default project")
            return cursor

    def _connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("factory", _FailingAfterTheProject)
        return cast(sqlite3.Connection, real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", _connect)
    db_path = tmp_path / "store" / "vantage.db"

    with pytest.raises(sqlite3.OperationalError, match="provoked after the default project"):
        open_database(db_path)

    monkeypatch.undo()
    probe = sqlite3.connect(str(db_path))
    try:
        tables = probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        probe.close()
    assert tables == []


def test_every_ddl_statement_in_schema_sql_declares_if_not_exists() -> None:
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")

    assert _DDL_HEAD.findall(sql), "expected at least one CREATE statement in schema.sql"
    assert _statements_missing_if_not_exists(sql) == []


def test_the_if_not_exists_check_catches_a_bare_create_table() -> None:
    """Triangulates the previous test: proves the check would fail loudly."""
    sql = "CREATE TABLE widget (id INTEGER PRIMARY KEY);\n"

    assert _statements_missing_if_not_exists(sql) == ["TABLE widget"]


def test_reopening_an_existing_database_issues_no_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    first = open_database(db_path)
    first.close()

    captured = _spy_on_executescript(monkeypatch)

    second = open_database(db_path)
    second.close()

    assert captured == []


def _seed_meta_only_database(db_path: Path, *, schema_version_value: str | None) -> None:
    """Simulate a database whose `meta` table exists (so `open_database` treats
    the schema as already applied) but whose `schema_version` row is absent or
    set to an arbitrary value -- the shape a database from a different release
    has.

    Built with a plain `sqlite3.connect`, never `open_database`, so the test
    controls the stamped version independently of whatever `schema.sql` itself
    currently stamps.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if schema_version_value is not None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (schema_version_value,),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("stamped", "found"),
    [
        (None, "absent"),
        (str(_SCHEMA_VERSION - 1), str(_SCHEMA_VERSION - 1)),
        (str(_SCHEMA_VERSION + 1), str(_SCHEMA_VERSION + 1)),
        ("not-a-number", "absent"),
    ],
    ids=["absent", "older", "newer", "unparseable"],
)
def test_a_database_stamped_with_any_other_schema_version_is_refused(
    tmp_path: Path, stamped: str | None, found: str
) -> None:
    """Older and newer are both refused: a build cannot honour an invariant
    it does not know about. The message names what was found, what is
    required, and which file."""
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value=stamped)

    with pytest.raises(SchemaVersionError) as exc_info:
        open_database(db_path)

    message = str(exc_info.value)
    assert f"schema_version is {found}," in message
    assert f"requires schema_version {_SCHEMA_VERSION};" in message
    assert str(db_path) in message


def _capture_connections(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    """Record every connection `sqlite3.connect` hands out, so a test can
    check the one `open_database` made after it raised."""
    created: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def _capturing_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = cast(sqlite3.Connection, real_connect(*args, **kwargs))
        created.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", _capturing_connect)
    return created


def test_a_refusal_changes_nothing_and_closes_the_connection_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a byte of the refused file changes. Switching it to write-ahead
    logging first would: the journal mode is stored in the file's header
    and outlives the connection, so the refusal would leave behind a
    database no older build opens the way it left it."""
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value="1")
    before = db_path.read_bytes()

    created = _capture_connections(monkeypatch)

    with pytest.raises(SchemaVersionError):
        open_database(db_path)

    # `open_database` made exactly one `sqlite3.connect` call; proving it is
    # unusable after the refusal proves `close()` ran as part of raising, not
    # merely eventually via garbage collection.
    assert len(created) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        created[0].execute("SELECT 1")

    assert db_path.read_bytes() == before
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["vantage.db"]


def test_a_database_holding_another_schema_is_refused_and_left_as_it_was(
    tmp_path: Path,
) -> None:
    """A mistyped `--database` can name some other application's SQLite
    file. Adding vantage's tables to it, stamping it and switching its
    journal mode would change someone else's data; refusing it says what
    the file is."""
    db_path = tmp_path / "store" / "customers.db"
    db_path.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(str(db_path))) as foreign, foreign:
        foreign.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        foreign.execute("INSERT INTO customers (name) VALUES ('a')")
    before = db_path.read_bytes()

    with pytest.raises(SchemaVersionError, match="not a vantage database"):
        open_database(db_path)

    assert db_path.read_bytes() == before
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["customers.db"]


def test_a_version_7_database_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    """A database from the build before projects has runs, a catalogue and
    settings that belong to no project. It is refused, not given a
    `project` table or a `default` project: rows would have to be moved
    into one, which is a migration, and there are none."""
    db_path = tmp_path / "store" / "vantage.db"
    db_path.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(str(db_path))) as old, old:
        old.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        old.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '7')")
        old.execute("CREATE TABLE run (id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
        old.execute("INSERT INTO run VALUES (?, '2026-09-01T10:00:00')", ("a" * 32,))
        old.execute(
            "CREATE TABLE user_setting (namespace TEXT, key TEXT, value TEXT, updated_at TEXT,"
            " PRIMARY KEY (namespace, key))"
        )
    before = db_path.read_bytes()

    with pytest.raises(SchemaVersionError) as refused:
        open_database(db_path)

    assert "schema_version is 7, but this build requires schema_version 8;" in str(refused.value)
    assert db_path.read_bytes() == before
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["vantage.db"]


def test_a_schema_that_fails_partway_is_rolled_back_and_its_lock_released_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A statement near the end of the schema fails after every table
    before it was created inside `BEGIN IMMEDIATE`. The connection must be
    closed as part of raising, so the half-applied schema rolls back and the
    write lock is free while the caller is still handling the error -- not
    whenever the traceback that references the connection is collected."""
    broken = tmp_path / "schema.sql"
    broken.write_text(
        _SCHEMA_SQL.read_text(encoding="utf-8")
        + "\nCREATE INDEX IF NOT EXISTS idx_broken ON no_such_table (x);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("vantage.storage.connection._SCHEMA_SQL_PATH", broken)
    db_path = tmp_path / "store" / "vantage.db"
    created = _capture_connections(monkeypatch)

    with pytest.raises(sqlite3.OperationalError, match="no_such_table"):
        open_database(db_path)

    assert len(created) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        created[0].execute("SELECT 1")
    monkeypatch.undo()
    other = sqlite3.connect(str(db_path), isolation_level=None, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
        tables = other.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        other.close()
    assert tables == []


def test_a_file_that_is_not_a_database_leaves_no_open_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database, just some bytes " * 64)
    created = _capture_connections(monkeypatch)

    with pytest.raises(sqlite3.DatabaseError):
        open_database(db_path)

    assert len(created) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        created[0].execute("SELECT 1")


def test_opening_a_database_with_the_current_schema_version_succeeds_and_applies_no_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value=str(_SCHEMA_VERSION))

    captured = _spy_on_executescript(monkeypatch)

    conn = open_database(db_path)
    conn.close()

    assert captured == []


def test_creating_a_database_survives_a_username_lookup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before 3.13, `getpass.getuser()` raises `KeyError` from `pwd.getpwuid`
    in a container run as an unmapped uid with no `LOGNAME`/`USER` set.

    `created_by` is a convenience row; losing it must not stop the server
    from starting.
    """

    def _no_such_user() -> str:
        raise KeyError("getpwuid(): uid not found: 1234")

    monkeypatch.setattr("vantage.storage.connection.getpass.getuser", _no_such_user)

    conn = open_database(tmp_path / "store" / "vantage.db")
    try:
        stored = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()

    # The database exists and is usable; only the convenience row is absent.
    assert stored["schema_version"] == str(_SCHEMA_VERSION)
    assert "created_by" not in stored


def _wal_switch_locked(monkeypatch: pytest.MonkeyPatch, times: int | None) -> list[str]:
    """Patch `sqlite3.connect` so the first `times` switches to WAL -- every
    one, for `None` -- fail as SQLite fails one of two connections switching
    a new database at once. Returns the answer each attempt got."""
    attempts: list[str] = []

    class _ContendedConnection(sqlite3.Connection):
        def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
            if sql == "PRAGMA journal_mode=WAL":
                if times is None or len(attempts) < times:
                    attempts.append("locked")
                    raise sqlite3.OperationalError("database is locked")
                attempts.append("switched")
            return super().execute(sql, *args)

    real_connect = sqlite3.connect

    def _connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("factory", _ContendedConnection)
        return cast(sqlite3.Connection, real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", _connect)
    return attempts


def test_a_switch_to_wal_another_connection_holds_up_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two processes opening one new database at once -- two pytest
    sessions storing their first runs -- must both get it."""
    attempts = _wal_switch_locked(monkeypatch, times=2)

    conn = open_database(tmp_path / "store" / "vantage.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        conn.close()

    assert attempts == ["locked", "locked", "switched"]


def test_a_switch_to_wal_that_stays_locked_gives_up_after_the_busy_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vantage.storage.connection._BUSY_TIMEOUT_SECONDS", 0.05)
    attempts = _wal_switch_locked(monkeypatch, times=None)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        open_database(tmp_path / "store" / "vantage.db")

    assert len(attempts) > 1
