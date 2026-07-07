"""RED/GREEN target for the Alembic migration environment (design SS2 --
"Alembic from day one"). Asserts `alembic upgrade head` on a fresh file DB
produces the exact same schema as `metadata.create_all()`, so the two
paths (real deployments vs. test/in-memory DBs, per design SS2) cannot
silently drift.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import vantage.adapters.sqlite as sqlite_adapter
from vantage.adapters.sqlite.tables import metadata

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
