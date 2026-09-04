"""Rot-detector for `docs/schema-manifest.md` against `schema.sql` (RQ-29).

RQ-29's verification method is Inspection, not Test -- `docs/schema-manifest.md`
is the verification of record, not this file. This module mechanises the same
comparison the manifest already records by hand, as a regression guard: it
independently parses the manifest's column tables and index list, applies
`schema.sql` to a throwaway in-memory database, and diffs the two in both
directions -- a column the manifest documents that the schema no longer has,
and a column the schema grew that the manifest never learned about, are both
regressions this test exists to catch. The manifest-parsing/comparison helper
lives here, in the test module, and is never shipped in a wheel.

Applies `schema.sql` with a plain `sqlite3.connect(":memory:").executescript`
-- not `vantage.storage.connection.open_database`, which doesn't exist until
PR4 and which this test must not depend on.
"""

# Rot-detector supporting RQ-29 (Inspection at docs/schema-manifest.md is the verification of record)  # noqa: E501

from __future__ import annotations

import re
import sqlite3
import textwrap
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_SQL = _REPO_ROOT / "packages" / "vantage" / "src" / "vantage" / "storage" / "schema.sql"
_MANIFEST = _REPO_ROOT / "docs" / "schema-manifest.md"

_TABLE_HEADING = re.compile(r"^### `(\w+)`")
_COLUMN_ROW = re.compile(r"^\|\s*`([A-Za-z0-9_]+)`\s*(†)?\s*\|")
_INDEX_ITEM = re.compile(r"^\d+\.\s+\*{0,2}`(\w+)\(([^)]+)\)`")


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """A table/column/named-index inventory, from either side of the comparison."""

    tables: dict[str, frozenset[str]]
    indexes: frozenset[tuple[str, tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    """Drift between a manifest snapshot and a schema snapshot, in both directions."""

    tables_only_in_manifest: frozenset[str]
    tables_only_in_schema: frozenset[str]
    columns_only_in_manifest: dict[str, frozenset[str]]
    columns_only_in_schema: dict[str, frozenset[str]]
    indexes_only_in_manifest: frozenset[tuple[str, tuple[str, ...]]]
    indexes_only_in_schema: frozenset[tuple[str, tuple[str, ...]]]

    @property
    def is_clean(self) -> bool:
        return not (
            self.tables_only_in_manifest
            or self.tables_only_in_schema
            or self.columns_only_in_manifest
            or self.columns_only_in_schema
            or self.indexes_only_in_manifest
            or self.indexes_only_in_schema
        )


def parse_manifest_schema(text: str) -> SchemaSnapshot:
    """Parse the manifest's per-table column tables and its `## Indexes` list.

    A dagger (`†`) on a column row documents the `<name>_truncated` sibling
    column *alongside* it rather than as its own row (the manifest's own
    "Conventions" section) -- so a daggered column expands to both names
    here, matching what `schema.sql` actually declares.
    """
    tables: dict[str, set[str]] = {}
    indexes: set[tuple[str, tuple[str, ...]]] = set()
    current_table: str | None = None
    in_indexes_section = False

    for line in text.splitlines():
        if line.startswith("## Indexes"):
            in_indexes_section = True
            current_table = None
            continue
        if line.startswith("## "):
            in_indexes_section = False
            current_table = None
            continue

        if in_indexes_section:
            match = _INDEX_ITEM.match(line)
            if match is not None:
                table, raw_columns = match.group(1), match.group(2)
                columns = tuple(part.strip() for part in raw_columns.split(","))
                indexes.add((table, columns))
            continue

        heading = _TABLE_HEADING.match(line)
        if heading is not None:
            current_table = heading.group(1)
            tables.setdefault(current_table, set())
            continue

        if current_table is None:
            continue

        column = _COLUMN_ROW.match(line)
        if column is not None:
            name, unbounded_marker = column.group(1), column.group(2)
            tables[current_table].add(name)
            if unbounded_marker:
                tables[current_table].add(f"{name}_truncated")

    return SchemaSnapshot(
        tables={name: frozenset(cols) for name, cols in tables.items()},
        indexes=frozenset(indexes),
    )


def introspect_sqlite_schema(conn: sqlite3.Connection) -> SchemaSnapshot:
    """Read the tables/columns/named-indexes an already-applied connection has.

    Excludes `sqlite_%` tables (the implicit `sqlite_sequence` an
    `AUTOINCREMENT` column creates) and `sqlite_autoindex_%` indexes (the
    anonymous indexes SQLite creates for inline `PRIMARY KEY`/`UNIQUE`
    constraints) -- neither is part of what the manifest documents.
    """
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables: dict[str, frozenset[str]] = {}
    for (name,) in table_rows:
        columns = conn.execute(f"PRAGMA table_info({name})").fetchall()
        tables[name] = frozenset(row[1] for row in columns)

    index_rows = conn.execute(
        "SELECT name, tbl_name FROM sqlite_master "
        "WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex_%'"
    ).fetchall()
    indexes: set[tuple[str, tuple[str, ...]]] = set()
    for index_name, table_name in index_rows:
        info = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        index_columns = tuple(row[2] for row in sorted(info, key=lambda row: row[0]))
        indexes.add((table_name, index_columns))

    return SchemaSnapshot(tables=tables, indexes=frozenset(indexes))


def diff_schema_snapshots(manifest: SchemaSnapshot, actual: SchemaSnapshot) -> SchemaDiff:
    """Compare a manifest-derived snapshot against a schema-derived one, both ways."""
    manifest_tables = frozenset(manifest.tables)
    actual_tables = frozenset(actual.tables)

    columns_only_in_manifest: dict[str, frozenset[str]] = {}
    columns_only_in_schema: dict[str, frozenset[str]] = {}
    for table in manifest_tables & actual_tables:
        only_manifest = manifest.tables[table] - actual.tables[table]
        only_schema = actual.tables[table] - manifest.tables[table]
        if only_manifest:
            columns_only_in_manifest[table] = only_manifest
        if only_schema:
            columns_only_in_schema[table] = only_schema

    return SchemaDiff(
        tables_only_in_manifest=manifest_tables - actual_tables,
        tables_only_in_schema=actual_tables - manifest_tables,
        columns_only_in_manifest=columns_only_in_manifest,
        columns_only_in_schema=columns_only_in_schema,
        indexes_only_in_manifest=manifest.indexes - actual.indexes,
        indexes_only_in_schema=actual.indexes - manifest.indexes,
    )


def _apply_schema(sql: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    return conn


def _real_manifest_snapshot() -> SchemaSnapshot:
    return parse_manifest_schema(_MANIFEST.read_text(encoding="utf-8"))


def _real_schema_snapshot() -> SchemaSnapshot:
    conn = _apply_schema(_SCHEMA_SQL.read_text(encoding="utf-8"))
    try:
        return introspect_sqlite_schema(conn)
    finally:
        conn.close()


def _diff_of(manifest_text: str, schema_sql: str) -> SchemaDiff:
    conn = _apply_schema(schema_sql)
    try:
        actual = introspect_sqlite_schema(conn)
    finally:
        conn.close()
    manifest = parse_manifest_schema(manifest_text)
    return diff_schema_snapshots(manifest, actual)


def test_fresh_database_matches_the_manifest_in_both_directions() -> None:
    diff = diff_schema_snapshots(_real_manifest_snapshot(), _real_schema_snapshot())

    assert diff.is_clean, (
        f"tables only in manifest: {sorted(diff.tables_only_in_manifest)}; "
        f"tables only in schema: {sorted(diff.tables_only_in_schema)}; "
        f"columns only in manifest: {diff.columns_only_in_manifest}; "
        f"columns only in schema: {diff.columns_only_in_schema}; "
        f"indexes only in manifest: {sorted(diff.indexes_only_in_manifest)}; "
        f"indexes only in schema: {sorted(diff.indexes_only_in_schema)}"
    )


def test_fresh_database_matches_the_recorded_ground_truth() -> None:
    snapshot = _real_schema_snapshot()

    assert len(snapshot.tables) == 13
    assert sum(len(columns) for columns in snapshot.tables.values()) == 139
    assert len(snapshot.indexes) == 15


def test_reapplying_schema_sql_issues_no_error_and_no_count_change() -> None:
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")
    conn = _apply_schema(sql)
    try:
        conn.executescript(sql)  # must not raise -- RQ-29.2's mechanism, IF NOT EXISTS
        reapplied = introspect_sqlite_schema(conn)
    finally:
        conn.close()

    original = _real_schema_snapshot()
    assert reapplied.tables.keys() == original.tables.keys()
    assert reapplied.indexes == original.indexes


_BASE_MANIFEST = textwrap.dedent(
    """\
    ## Tables

    ### `widget`

    | Column | Type | Driver | Populated |
    | --- | --- | --- | --- |
    | `id` | INTEGER PK | — | M1 |
    | `name` | TEXT NOT NULL | — | M1 |

    ## Indexes

    1. `widget(name)`
    """
)

_BASE_SCHEMA = textwrap.dedent(
    """\
    CREATE TABLE widget (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL
    );
    CREATE INDEX idx_widget_name ON widget (name);
    """
)


def test_diff_catches_a_column_the_manifest_documents_but_the_schema_lacks() -> None:
    manifest_text = _BASE_MANIFEST.replace(
        "| `name` | TEXT NOT NULL | — | M1 |\n",
        "| `name` | TEXT NOT NULL | — | M1 |\n| `description` | TEXT NULL | — | M1 |\n",
    )

    diff = _diff_of(manifest_text, _BASE_SCHEMA)

    assert not diff.is_clean
    assert diff.columns_only_in_manifest == {"widget": frozenset({"description"})}
    assert diff.columns_only_in_schema == {}


def test_diff_catches_a_column_the_schema_grew_that_the_manifest_never_documented() -> None:
    schema_sql = _BASE_SCHEMA.replace(
        "name TEXT NOT NULL\n", "name TEXT NOT NULL,\n    secret TEXT NULL\n"
    )

    diff = _diff_of(_BASE_MANIFEST, schema_sql)

    assert not diff.is_clean
    assert diff.columns_only_in_schema == {"widget": frozenset({"secret"})}
    assert diff.columns_only_in_manifest == {}


def test_diff_catches_a_table_the_manifest_documents_but_the_schema_lacks() -> None:
    manifest_text = _BASE_MANIFEST.replace(
        "## Indexes",
        "### `ghost`\n\n"
        "| Column | Type | Driver | Populated |\n"
        "| --- | --- | --- | --- |\n"
        "| `id` | INTEGER PK | — | M1 |\n\n"
        "## Indexes",
    )

    diff = _diff_of(manifest_text, _BASE_SCHEMA)

    assert not diff.is_clean
    assert diff.tables_only_in_manifest == frozenset({"ghost"})
    assert diff.tables_only_in_schema == frozenset()


def test_diff_catches_a_table_the_schema_grew_that_the_manifest_never_documented() -> None:
    schema_sql = _BASE_SCHEMA + "CREATE TABLE undocumented (id INTEGER PRIMARY KEY);\n"

    diff = _diff_of(_BASE_MANIFEST, schema_sql)

    assert not diff.is_clean
    assert diff.tables_only_in_schema == frozenset({"undocumented"})
    assert diff.tables_only_in_manifest == frozenset()


def test_diff_catches_an_index_the_manifest_documents_but_the_schema_lacks() -> None:
    manifest_text = _BASE_MANIFEST.replace(
        "1. `widget(name)`\n", "1. `widget(name)`\n2. `widget(id)`\n"
    )

    diff = _diff_of(manifest_text, _BASE_SCHEMA)

    assert not diff.is_clean
    assert diff.indexes_only_in_manifest == frozenset({("widget", ("id",))})
    assert diff.indexes_only_in_schema == frozenset()


def test_diff_catches_an_index_the_schema_grew_that_the_manifest_never_documented() -> None:
    schema_sql = _BASE_SCHEMA + "CREATE INDEX idx_widget_id ON widget (id);\n"

    diff = _diff_of(_BASE_MANIFEST, schema_sql)

    assert not diff.is_clean
    assert diff.indexes_only_in_schema == frozenset({("widget", ("id",))})
    assert diff.indexes_only_in_manifest == frozenset()
