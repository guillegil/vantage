"""RQ-40: owner-only permissions on the database file, the artefact store, and
the WAL sidecars.

POSIX-only -- RQ-40's own notes say the Windows ACL equivalent is unspecified
and open; skipped entirely on non-POSIX rather than inventing a Windows
behaviour this project has not decided.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from vantage.storage.connection import open_database

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode semantics only")


@pytest.fixture
def permissive_umask() -> Iterator[None]:
    """A umask of 022 -- the permissive default this hazard depends on.

    Under 022, `os.makedirs`/`sqlite3.connect` creating a file or directory
    with their own defaults land at 0644/0755 -- the exact widened modes D9
    exists to prevent by creating the path explicitly instead.
    """
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.req("RQ-40")
def test_database_file_created_0600_before_connect(
    tmp_path: Path, permissive_umask: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing test.

    `sqlite3.connect` creates the database file itself, at 0644 under a
    umask of 022 -- so a test that reads the mode *after* `open_database`
    returns would pass against a broken implementation that connects first
    and `chmod`s afterward, which still leaves a window where the file is
    world-readable. This test patches `sqlite3.connect` to snapshot the
    file's mode at the moment it is called, proving `os.open(...,
    O_CREAT | O_EXCL | O_RDWR, 0o600)` already ran and closed *before*
    `sqlite3.connect` ever touches the path.
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
