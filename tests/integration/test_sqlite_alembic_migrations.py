"""RED/GREEN target for the Alembic migration environment (design SS2 --
"Alembic from day one"). Asserts `alembic upgrade head` on a fresh file DB
produces the exact same schema as `metadata.create_all()`, so the two
paths (real deployments vs. test/in-memory DBs, per design SS2) cannot
silently drift.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import vantage.adapters.sqlite as sqlite_adapter
from vantage.adapters.sqlite.tables import metadata

_NEW_RUNS_COLUMNS = {"seed", "seed_source", "marker_expr", "keyword_expr"}
_NEW_TEST_RESULTS_COLUMNS = {
    "base_test_id",
    "relpath",
    "lineno",
    "originalname",
    "parameters",
    "fixture_names",
}
_NEW_INDEX_NAME = "ix_test_results_base_test_id"

MIGRATIONS_DIR = Path(sqlite_adapter.__file__).resolve().parent / "migrations"


def _alembic_config(sqlalchemy_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", sqlalchemy_url)
    return config


def _schema_snapshot(engine: sa.Engine) -> dict[str, set[str]]:
    inspector = sa.inspect(engine)
    return {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in inspector.get_table_names()
        if table_name != "alembic_version"
    }


def test_alembic_upgrade_head_matches_create_all_schema(tmp_path: Path) -> None:
    migrated_db = tmp_path / "migrated.db"
    baseline_db = tmp_path / "baseline.db"

    command.upgrade(_alembic_config(f"sqlite:///{migrated_db}"), "head")

    baseline_engine = sa.create_engine(f"sqlite:///{baseline_db}")
    metadata.create_all(baseline_engine)

    migrated_engine = sa.create_engine(f"sqlite:///{migrated_db}")
    assert _schema_snapshot(migrated_engine) == _schema_snapshot(baseline_engine)


def test_alembic_stamps_a_head_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "vantage.db"
    config = _alembic_config(f"sqlite:///{db_path}")

    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_alembic_upgrade_head_adds_new_columns_preserving_existing_rows(
    tmp_path: Path,
) -> None:
    """0002 must be a pure additive delta on top of frozen 0001: a DB with
    pre-existing rows at 0001 must upgrade cleanly to head, with every new
    column landing NULL on those rows (spec Domain 3 -- Additive-Only
    Migration)."""
    db_path = tmp_path / "vantage.db"
    config = _alembic_config(f"sqlite:///{db_path}")

    command.upgrade(config, "0001")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    created_at = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO runs "
                "(run_id, state, testpath, user, created_at, invocation, env_snapshot, totals) "
                "VALUES (:run_id, :state, :testpath, :user, :created_at, :invocation, "
                ":env_snapshot, :totals)"
            ),
            {
                "run_id": "run-1",
                "state": "pending",
                "testpath": "tests/",
                "user": "alice",
                "created_at": created_at,
                "invocation": "{}",
                "env_snapshot": "{}",
                "totals": "{}",
            },
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT seed, seed_source, marker_expr, keyword_expr, testpath "
                "FROM runs WHERE run_id = :run_id"
            ),
            {"run_id": "run-1"},
        ).mappings().first()
    assert row is not None
    assert row["testpath"] == "tests/"
    assert row["seed"] is None
    assert row["seed_source"] is None
    assert row["marker_expr"] is None
    assert row["keyword_expr"] is None

    inspector = sa.inspect(engine)
    runs_columns = {column["name"] for column in inspector.get_columns("runs")}
    assert _NEW_RUNS_COLUMNS <= runs_columns

    test_results_columns = {
        column["name"] for column in inspector.get_columns("test_results")
    }
    assert _NEW_TEST_RESULTS_COLUMNS <= test_results_columns

    index_names = {index["name"] for index in inspector.get_indexes("test_results")}
    assert _NEW_INDEX_NAME in index_names


def test_alembic_downgrade_0002_to_0001_drops_new_columns_and_index(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "vantage.db"
    config = _alembic_config(f"sqlite:///{db_path}")

    command.upgrade(config, "head")
    command.downgrade(config, "0001")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)

    runs_columns = {column["name"] for column in inspector.get_columns("runs")}
    assert not (_NEW_RUNS_COLUMNS & runs_columns)

    test_results_columns = {
        column["name"] for column in inspector.get_columns("test_results")
    }
    assert not (_NEW_TEST_RESULTS_COLUMNS & test_results_columns)

    index_names = {index["name"] for index in inspector.get_indexes("test_results")}
    assert _NEW_INDEX_NAME not in index_names
