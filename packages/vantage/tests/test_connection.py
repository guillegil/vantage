"""`open_database` applies `schema.sql` once, inside one transaction, never
re-issues DDL against an existing database, and refuses a database stamped
with a different schema version.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from vantage.storage.connection import _SCHEMA_VERSION, SchemaVersionError, open_database

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


def test_the_tables_and_the_version_stamp_commit_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stamp that fails after every table was created leaves no table
    behind: a database with a schema but no stamp would be refused as
    'absent' on every later open."""
    db_path = tmp_path / "store" / "vantage.db"
    monkeypatch.setattr(
        "vantage.storage.connection._STAMP_SCHEMA_VERSION",
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


def test_a_refusal_issues_no_ddl_and_closes_the_connection_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value="1")

    before = sqlite3.connect(str(db_path))
    before_master = before.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    before.close()

    created: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def _capturing_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = cast(sqlite3.Connection, real_connect(*args, **kwargs))
        created.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", _capturing_connect)

    with pytest.raises(SchemaVersionError):
        open_database(db_path)

    # `open_database` made exactly one `sqlite3.connect` call; proving it is
    # unusable after the refusal proves `close()` ran as part of raising, not
    # merely eventually via garbage collection.
    assert len(created) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        created[0].execute("SELECT 1")

    monkeypatch.undo()
    after = sqlite3.connect(str(db_path))
    after_master = after.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    after.close()

    assert after_master == before_master


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
