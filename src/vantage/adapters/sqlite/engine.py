"""SQLAlchemy engine factory for the SQLite adapter -- enables WAL mode
and related pragmas on every new DBAPI connection (design SS2).

IMPORTANT -- local filesystem only: the `url` passed here (and therefore
the value an operator supplies via `--vantage-db=<path>`) MUST reference a
path local to the host running the server. SQLite's WAL mode relies on a
`-wal`/`-shm` file pair next to the database, which requires shared-memory
primitives unavailable over network filesystems. Network-mounted paths are
unsupported for v1 (spec Domain 3 -- Local-Path Documentation for WAL);
this MUST also be documented for operators in deployment docs (see
`docs/architecture.md`).

In-memory databases (`sqlite:///:memory:`, used by tests) have no file to
hold a WAL segment, so this factory skips the `journal_mode` pragma for
them cleanly instead of erroring -- the other pragmas (`foreign_keys`,
`busy_timeout`) still apply.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy import Engine, create_engine, event


def create_sqlite_engine(url: str, **engine_kwargs: Any) -> Engine:
    """Create a SQLAlchemy engine for `url`, wiring a `connect` event
    listener that sets WAL-related pragmas on every new connection."""

    engine = create_engine(url, **engine_kwargs)
    is_memory_database = engine.url.database in (None, "", ":memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            if not is_memory_database:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    return engine
