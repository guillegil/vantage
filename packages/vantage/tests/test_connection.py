"""`open_database` applies `schema.sql` once, inside one transaction, and never
re-issues DDL against an existing, already-schema'd database (RQ-29.2).

RQ-29's verification method is Inspection, not Test -- `docs/schema-manifest.md`
(PR2) is the verification of record. This file is not tagged
`@pytest.mark.req(id="RQ-29")`, for the same reason PR3's rot-detector isn't
(plain comment instead): it mechanises/protects the same guarantee the
Inspection already covers, rather than being the Inspection itself.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from vantage.storage.connection import SchemaVersionError, open_database

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


def test_open_database_applies_schema_inside_one_begin_immediate_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    captured = _spy_on_executescript(monkeypatch)

    conn = open_database(db_path)
    conn.close()

    assert len(captured) == 1
    script = captured[0].strip()
    assert script.startswith("BEGIN IMMEDIATE")
    assert script.rstrip().rstrip(";").endswith("COMMIT")


def test_every_ddl_statement_in_schema_sql_declares_if_not_exists() -> None:
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")

    assert _DDL_HEAD.findall(sql), "expected at least one CREATE statement in schema.sql"
    assert _statements_missing_if_not_exists(sql) == []


def test_the_if_not_exists_check_catches_a_bare_create_table() -> None:
    """Triangulates the previous test: proves the check would fail loudly."""
    sql = "CREATE TABLE widget (id INTEGER PRIMARY KEY);\n"

    assert _statements_missing_if_not_exists(sql) == ["TABLE widget"]


# RQ-29.2: opening an existing database issues no schema-altering statement.
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
    set to an arbitrary value -- exactly the shape D28 found every pre-change
    database in, and the shape a database from a different release would have.

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


def test_opening_a_database_with_no_schema_version_row_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value=None)

    with pytest.raises(SchemaVersionError) as exc_info:
        open_database(db_path)

    message = str(exc_info.value)
    assert "absent" in message
    assert "3" in message


def test_opening_a_database_with_an_older_schema_version_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value="1")

    with pytest.raises(SchemaVersionError) as exc_info:
        open_database(db_path)

    message = str(exc_info.value)
    assert "1" in message
    assert "3" in message


def test_opening_a_database_with_a_newer_schema_version_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    _seed_meta_only_database(db_path, schema_version_value="4")

    with pytest.raises(SchemaVersionError) as exc_info:
        open_database(db_path)

    message = str(exc_info.value)
    assert "4" in message
    assert "3" in message


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
    _seed_meta_only_database(db_path, schema_version_value="3")

    captured = _spy_on_executescript(monkeypatch)

    conn = open_database(db_path)
    conn.close()

    assert captured == []


def test_creating_a_database_survives_a_username_lookup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`getpass.getuser()` only normalises its failures to `OSError` on 3.13+;
    its own docstring records the change. On 3.10-3.12 -- three of this
    project's four CI legs -- it raises `KeyError` from `pwd.getpwuid`, which
    is what a container run as an unmapped uid with no `LOGNAME`/`USER` in the
    environment produces.

    `created_by` is a convenience row. Losing it must cost nothing; aborting
    `open_database` would stop the server from starting at all. Found by
    review, 2026-08-19.
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
    assert stored["schema_version"] == "3"
    assert "created_by" not in stored
