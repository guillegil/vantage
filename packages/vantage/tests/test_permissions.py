"""Owner-only permissions on the database file, its directory, and the WAL
sidecars.

POSIX-only: there is no defined Windows ACL behaviour to test.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from vantage.storage.connection import SchemaVersionError, open_database

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode semantics only")


@pytest.fixture
def permissive_umask() -> Iterator[None]:
    """A umask of 022 -- the permissive default this hazard depends on.

    Under 022, `os.makedirs`/`sqlite3.connect` creating a file or directory
    with their own defaults land at 0644/0755 -- the widened modes
    `open_database` prevents by creating the paths explicitly.
    """
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_database_file_created_0600_before_connect(
    tmp_path: Path, permissive_umask: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the mode after `open_database` returns would pass against an
    implementation that connects first and `chmod`s afterwards, leaving a
    window where the file is world-readable. Snapshotting the mode when
    `sqlite3.connect` is called proves the file already exists at 0600.
    """
    db_path = tmp_path / "store" / "vantage.db"
    real_connect = sqlite3.connect
    captured_modes: list[int] = []

    def _spy_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        captured_modes.append(_mode(db_path))
        return cast(sqlite3.Connection, real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    conn = open_database(db_path)
    conn.close()

    assert captured_modes == [0o600]


def test_a_directory_it_creates_is_owner_only(tmp_path: Path, permissive_umask: None) -> None:
    db_path = tmp_path / "store" / "vantage.db"

    conn = open_database(db_path)
    conn.close()

    assert _mode(db_path.parent) == 0o700


def test_an_existing_directory_keeps_its_mode(tmp_path: Path, permissive_umask: None) -> None:
    """`--database ~/vantage.db` or `./vantage.db` names a directory that
    belongs to the user, who chose its mode; narrowing it to 0700 would lock
    out everyone else the user shares it with."""
    project = tmp_path / "project"
    project.mkdir()
    os.chmod(project, 0o755)  # noqa: S103 -- the shared mode under test

    conn = open_database(project / "vantage.db")
    conn.close()

    assert _mode(project) == 0o755


def test_an_existing_directory_owned_by_someone_else_is_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writable directory another user owns (the sticky `/tmp`) refuses
    `chmod` with EPERM. Opening a database in it must not try."""
    shared = tmp_path / "shared"
    shared.mkdir()
    real_chmod = os.chmod

    def _chmod(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        if Path(path) == shared:
            raise PermissionError(1, "Operation not permitted", str(path))
        real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", _chmod)

    conn = open_database(shared / "vantage.db")
    conn.close()


def test_existing_permissive_database_still_records_and_warns(
    tmp_path: Path, permissive_umask: None, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "store" / "vantage.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    os.chmod(db_path, 0o644)

    with caplog.at_level(logging.WARNING):
        conn = open_database(db_path)
    conn.execute(
        "INSERT INTO run (id, received_at, started_at) VALUES (?, ?, ?)",
        ("a" * 32, "2026-08-15T09:00:00+00:00", "2026-08-15T09:00:00+00:00"),
    )
    row = conn.execute("SELECT COUNT(*) FROM run").fetchone()
    conn.close()

    # Never silently rewritten -- an operator may have widened the mode on purpose.
    assert _mode(db_path) == 0o644
    assert row == (1,)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("644" in message for message in warnings)


def test_a_widened_database_keeps_sidecars_as_wide_as_itself(
    tmp_path: Path, permissive_umask: None
) -> None:
    """Whoever reads a database in write-ahead-log mode needs both sidecars
    too, the `-shm` one writable. An operator who widened the database for
    a group of readers widened them with it, and narrowing the sidecars on
    every open would lock those readers out while the server runs."""
    db_path = tmp_path / "store" / "vantage.db"
    open_database(db_path).close()
    os.chmod(db_path, 0o664)
    reader = sqlite3.connect(str(db_path))
    try:
        reader.execute("SELECT COUNT(*) FROM run").fetchone()
        sidecars = [db_path.with_name(db_path.name + suffix) for suffix in ("-wal", "-shm")]
        assert [_mode(sidecar) for sidecar in sidecars] == [0o664, 0o664]

        open_database(db_path).close()

        assert [_mode(sidecar) for sidecar in sidecars] == [0o664, 0o664]
        assert _mode(db_path) == 0o664
    finally:
        reader.close()


def test_a_permissive_database_that_is_refused_gets_no_warning_first(
    tmp_path: Path, permissive_umask: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning says recording will continue; ahead of a refusal it says
    the opposite of what happens next."""
    db_path = tmp_path / "store" / "vantage.db"
    open_database(db_path).close()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    os.chmod(db_path, 0o644)

    with caplog.at_level(logging.WARNING), pytest.raises(SchemaVersionError):
        open_database(db_path)

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_wal_and_shm_sidecars_created_0600(tmp_path: Path, permissive_umask: None) -> None:
    """SQLite creates them on the first write, with the database file's own
    mode whatever the umask."""
    db_path = tmp_path / "store" / "vantage.db"

    conn = open_database(db_path)
    try:
        conn.execute(
            "INSERT INTO run (id, received_at, started_at) VALUES (?, ?, ?)",
            ("a" * 32, "2026-08-15T09:00:00+00:00", "2026-08-15T09:00:00+00:00"),
        )
        wal_path = db_path.with_name(db_path.name + "-wal")
        shm_path = db_path.with_name(db_path.name + "-shm")

        assert wal_path.exists()
        assert shm_path.exists()
        assert _mode(wal_path) == 0o600
        assert _mode(shm_path) == 0o600
    finally:
        conn.close()
