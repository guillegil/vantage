"""RED/GREEN target for `engine.py` -- WAL pragma wiring only (design SS2,
spec Domain 3 -- Local-Path Documentation for WAL).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from vantage.adapters.sqlite.engine import create_sqlite_engine


def _pragma(engine: Engine, name: str) -> object:
    with engine.connect() as connection:
        return connection.exec_driver_sql(f"PRAGMA {name}").scalar()


def test_file_database_enables_wal_and_related_pragmas(tmp_path: Path) -> None:
    db_path = tmp_path / "vantage.db"

    engine = create_sqlite_engine(f"sqlite:///{db_path}")

    assert _pragma(engine, "journal_mode") == "wal"
    assert _pragma(engine, "foreign_keys") == 1
    assert _pragma(engine, "busy_timeout") == 5000


def test_memory_database_skips_wal_but_keeps_other_pragmas() -> None:
    engine = create_sqlite_engine("sqlite:///:memory:")

    # WAL needs a real file on disk (shared-memory index file); an
    # in-memory DB has none, so it stays on its default journal mode
    # instead of raising.
    assert _pragma(engine, "journal_mode") != "wal"
    assert _pragma(engine, "foreign_keys") == 1
    assert _pragma(engine, "busy_timeout") == 5000
