"""RED/GREEN target for `tables.py` -- schema shape only (design SS2,
spec Domain 3). No repository logic exercised here; see
`test_sqlite_repository.py` for round-trip behavior.
"""

from __future__ import annotations

import sqlalchemy as sa

from vantage.adapters.sqlite.tables import metadata


def _columns(table_name: str) -> set[str]:
    return {column.name for column in metadata.tables[table_name].columns}


def test_create_all_creates_expected_tables() -> None:
    engine = sa.create_engine("sqlite:///:memory:")

    metadata.create_all(engine)

    table_names = set(sa.inspect(engine).get_table_names())
    assert {"runs", "test_results", "artifacts", "discovery", "events"} <= table_names


def test_runs_table_columns() -> None:
    expected = {
        "run_id",
        "state",
        "testpath",
        "user",
        "created_at",
        "started_at",
        "finished_at",
        "exit_code",
        "stop_reason",
        "invocation",
        "env_snapshot",
        "root_dir",
        "totals",
        "last_heartbeat_at",
        # Additive/optional (spec Domain 1 -- Run-Level Selection Metadata).
        "seed",
        "seed_source",
        "marker_expr",
        "keyword_expr",
    }
    assert _columns("runs") == expected
    assert [c.name for c in metadata.tables["runs"].primary_key.columns] == ["run_id"]


def test_test_results_table_columns() -> None:
    expected = {
        "id",
        "run_id",
        "node_id",
        "outcome",
        "duration",
        "phases",
        "longrepr",
        "started_at",
        # Additive/optional (spec Domain 1 -- Test-Level Structured Identity
        # and Parameters; Domain 6 -- Base Test ID Cross-Run Stability).
        "base_test_id",
        "relpath",
        "lineno",
        "originalname",
        "parameters",
        "fixture_names",
    }
    assert _columns("test_results") == expected


def test_test_results_base_test_id_is_indexed() -> None:
    indexed_columns = {
        column.name
        for index in metadata.tables["test_results"].indexes
        for column in index.columns
    }
    assert "base_test_id" in indexed_columns


def test_artifacts_table_columns() -> None:
    expected = {"id", "run_id", "kind", "path", "size", "created_at"}
    assert _columns("artifacts") == expected


def test_discovery_table_columns() -> None:
    expected = {"id", "run_id", "node_ids", "collected_at"}
    assert _columns("discovery") == expected


def test_events_table_columns() -> None:
    expected = {"seq", "run_id", "schema_version", "event_type", "timestamp", "payload"}
    assert _columns("events") == expected
    assert [c.name for c in metadata.tables["events"].primary_key.columns] == ["seq"]
