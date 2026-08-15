"""`vantage` -- resolve configuration, fail fast, then serve (design.md D11, D12).

**Path authority.** A resolved database directory that exists but cannot be
written to fails here, at startup -- not on the first report the server
accepts, by which point the plugin that sent it has already exited and the
report is lost either way. Resolution itself creates nothing
(`core/config/resolution.py` is pure); this is the one place that checks
the resolved path is actually usable before the server binds a port.
"""

from __future__ import annotations

import os
from pathlib import Path


class DatabaseDirectoryNotWritableError(RuntimeError):
    """The resolved database's parent directory exists but is not writable."""


def ensure_database_directory_writable(database_path: Path) -> None:
    """Raise if `database_path`'s parent exists and this process cannot write to it.

    A missing parent is not an error here -- `open_database` (D9) creates
    it. Only an *existing* directory this process cannot write into is a
    startup failure, because nothing later in the path will create it for us.
    """
    parent = database_path.parent
    if parent.exists() and not os.access(parent, os.W_OK):
        raise DatabaseDirectoryNotWritableError(
            f"{parent} exists but is not writable by this process; "
            f"cannot create or open {database_path}."
        )


__all__ = ["DatabaseDirectoryNotWritableError", "ensure_database_directory_writable"]
