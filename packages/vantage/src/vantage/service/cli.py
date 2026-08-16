"""`vantage` -- resolve configuration, fail fast, then serve (design.md D11, D12).

**Path authority.** A resolved database directory that exists but cannot be
written to fails here, at startup -- not on the first report the server
accepts, by which point the plugin that sent it has already exited and the
report is lost either way. Resolution itself creates nothing
(`core/config/resolution.py` is pure); this is the one place that checks
the resolved path is actually usable before the server binds a port.

**Network exposure.** Binding wider than the loopback default is a
deliberate `--host`, and it gets a warning naming exactly what is missing:
there is no authentication in front of this server yet (Phase 4). The
default warns about nothing -- a warning on every normal start trains
people to ignore the one that matters.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn

from vantage.core.config.resolution import resolve_server_config
from vantage.service.app import create_app
from vantage.storage.sqlite_store import SqliteExecutionStore

_LOGGER = logging.getLogger(__name__)
_LOOPBACK = "127.0.0.1"


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


def warn_if_bound_wide(host: str) -> None:
    """Warn, naming the missing authentication, for any bind address but the loopback default."""
    if host != _LOOPBACK:
        _LOGGER.warning(
            "Binding to %s: there is no authentication in front of this server yet "
            "(Phase 1); anyone who can route to this host can write to the database.",
            host,
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vantage", description="Run the vantage server.")
    parser.add_argument("--database", default=None, help="Path to the SQLite database file.")
    parser.add_argument("--host", default=None, help=f"Bind address (default {_LOOPBACK}).")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default 8765).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Resolve configuration, fail fast on an unusable path, warn on a wide bind, then serve."""
    args = _parse_args(argv)
    config = resolve_server_config(
        cli_database=args.database,
        env_database=os.environ.get("VANTAGE_DATABASE"),
        cli_host=args.host,
        cli_port=args.port,
        home=Path.home(),
        xdg_data_home=os.environ.get("XDG_DATA_HOME"),
    )

    try:
        ensure_database_directory_writable(config.database_path)
    except DatabaseDirectoryNotWritableError as exc:
        print(f"vantage: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    warn_if_bound_wide(config.host)

    store = SqliteExecutionStore(config.database_path)
    app = create_app(store)
    uvicorn.run(app, host=config.host, port=config.port)


__all__ = [
    "DatabaseDirectoryNotWritableError",
    "ensure_database_directory_writable",
    "main",
    "warn_if_bound_wide",
]
