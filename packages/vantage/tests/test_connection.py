"""`open_database` applies `schema.sql` once, inside one transaction, and never
re-issues DDL against an existing, already-schema'd database (RQ-29.2).

RQ-29's verification method is Inspection, not Test -- `docs/schema-manifest.md`
(PR2) is the verification of record. This file is not tagged
`@pytest.mark.req("RQ-29")`, for the same reason PR3's rot-detector isn't
(plain comment instead): it mechanises/protects the same guarantee the
Inspection already covers, rather than being the Inspection itself.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from vantage.storage.connection import open_database

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
    """Patch `sqlite3.Connection.executescript` to record every script it runs.

    `_apply_schema` is the only thing in `connection.py` that calls
    `executescript` -- PRAGMAs and the schema-presence check both go through
    plain `execute`, so a call recorded here is unambiguously a DDL
    application, and zero calls unambiguously means none happened.
    """
    captured: list[str] = []
    real_executescript = sqlite3.Connection.executescript

    def _spy(self: sqlite3.Connection, sql_script: str) -> sqlite3.Cursor:
        captured.append(sql_script)
        return real_executescript(self, sql_script)

    monkeypatch.setattr(sqlite3.Connection, "executescript", _spy)
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
