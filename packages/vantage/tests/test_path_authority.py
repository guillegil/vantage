"""Threat matrix "Path authority": resolving a database path answers a
question and must never act on the answer, and a path this process cannot
use must fail loudly at startup rather than silently at the first write.

POSIX-only for the read-only-parent case -- Windows ACL semantics are a
different check this project has not decided (RQ-40's own notes make the
same call).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from vantage.core.config.resolution import resolve_server_config
from vantage.service.cli import (
    DatabaseDirectoryNotWritableError,
    ensure_database_directory_writable,
)


def test_resolution_creates_no_directory(tmp_path: Path) -> None:
    """`resolve_server_config` is pure: asking where the database would go
    must not materialise it. `xdg_data_home` names a directory that does
    not exist on disk; resolving against it must leave it that way.
    """
    xdg_data_home = tmp_path / "xdg-data"

    config = resolve_server_config(
        cli_database=None,
        env_database=None,
        cli_host=None,
        cli_port=None,
        home=tmp_path,
        xdg_data_home=str(xdg_data_home),
    )

    assert config.database_path == xdg_data_home / "vantage" / "vantage.db"
    assert not xdg_data_home.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode semantics only")
def test_ensure_database_directory_writable_raises_on_read_only_parent(tmp_path: Path) -> None:
    parent = tmp_path / "readonly"
    parent.mkdir(mode=0o500)
    try:
        with pytest.raises(DatabaseDirectoryNotWritableError, match=str(parent)):
            ensure_database_directory_writable(parent / "vantage.db")
    finally:
        parent.chmod(0o700)  # so tmp_path's own fixture cleanup can remove it


def test_ensure_database_directory_writable_passes_for_a_writable_parent(tmp_path: Path) -> None:
    ensure_database_directory_writable(tmp_path / "vantage.db")
