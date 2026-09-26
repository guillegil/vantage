"""A run's metadata rows as a `SqliteExecutionStore` holds them, read with
plain SQL: no port method returns its file rows.

Each read opens a connection of its own on the database file, as another
process would, so it sees only what the store has committed and never
touches the store's connection or lock.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from vantage.core.ports.storage import MetadataEntry, MetadataFile, RunMetadata


def read_metadata(database: Path, run_id: str) -> RunMetadata:
    """The metadata files and entries stored for `run_id`, in no particular
    order. No port method returns the file rows, so both tables are read
    here, the same way."""
    with closing(sqlite3.connect(database)) as conn:
        files = conn.execute(
            "SELECT source_file, content_type, status FROM run_metadata_file WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        entries = conn.execute(
            "SELECT key, name, value, status, source, source_file, declared"
            " FROM run_metadata WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    return RunMetadata(
        files=tuple(
            MetadataFile(source_file=source_file, content_type=content_type, status=status)
            for source_file, content_type, status in files
        ),
        entries=tuple(
            MetadataEntry(
                key=key,
                name=name,
                value=value,
                status=status,
                source=source,
                source_file=source_file,
                declared=bool(declared),
            )
            for key, name, value, status, source, source_file, declared in entries
        ),
    )
