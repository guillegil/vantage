"""`vantage` -- resolve configuration, fail fast, then serve.

**Path check at startup.** A database directory that exists but cannot be
written to fails here, before the server accepts a request -- not on the
first report, by which point the plugin that sent it has exited and the
report is lost. Resolution itself (`core/config/resolution.py`) is pure and
creates nothing.

**Every startup refusal is one line.** A setting resolution refuses, an
address this process cannot listen on (a port already in use, a host that
does not resolve), a database this process cannot open or create, and a
database from another schema version all end as a single `vantage: ...`
line on stderr and exit status 1, never a traceback. The listening socket
is bound here, before the database is opened, so a refused bind creates
nothing; uvicorn is handed the bound socket rather than binding its own,
which it would do only after the database exists and would report in
several log lines and exit status 3.

**The store is closed by the app's shutdown** (`create_app`'s
`close_store_on_shutdown`). On SIGTERM uvicorn shuts the app down and then
raises the signal again with its default action, which ends the process
inside `Server.run`, so a `finally` here never runs on that path.

**Network exposure.** Binding wider than the loopback default warns that
there is no authentication in front of this server. The default warns about
nothing: a warning on every normal start trains people to ignore the one
that matters.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sqlite3
import sys
from pathlib import Path
from typing import NoReturn

import uvicorn
from fastapi import FastAPI

from vantage.core.config.resolution import (
    ServerConfig,
    ServerConfigError,
    resolve_server_config,
)
from vantage.service.app import create_app
from vantage.service.errors import MAX_REPORT_BYTES
from vantage.storage.connection import SchemaVersionError
from vantage.storage.sqlite_store import SqliteExecutionStore

_LOGGER = logging.getLogger(__name__)
_LOOPBACK = "127.0.0.1"

# uvicorn's own logger, which its logging configuration prints at INFO; this
# module's logger is only printed from WARNING up.
_UVICORN_LOGGER = logging.getLogger("uvicorn.error")

# How much of a request's line and headers the server buffers before it has
# them whole; past it, the request is refused before any route sees it. A
# stored node id is read back by value, in the query string, and it can be
# as long as a report allows: at most `MAX_REPORT_BYTES` of UTF-8, each byte
# percent-encoded in at most three. The HTTP parser's own default, 16 KiB,
# left a result that `/results` lists impossible to fetch.
_MAX_REQUEST_HEAD_BYTES = 3 * MAX_REPORT_BYTES + 64 * 1024


class DatabaseDirectoryNotWritableError(RuntimeError):
    """The resolved database's parent directory exists but is not writable."""


def ensure_database_directory_writable(database_path: Path) -> None:
    """Raise if `database_path`'s parent exists and this process cannot write to it.

    A missing parent is not an error here -- `open_database` creates it.
    Only an *existing* directory this process cannot write into is a
    startup failure.
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
            "Binding to %s: there is no authentication in front of this server yet; "
            "anyone who can route to this host can write to the database.",
            host,
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vantage", description="Run the vantage server.")
    parser.add_argument("--database", default=None, help="Path to the SQLite database file.")
    parser.add_argument("--host", default=None, help=f"Bind address (default {_LOOPBACK}).")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default 8765).")
    parser.add_argument(
        "--grace-period",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Seconds without contact before a run is presented as abandoned (default 900.0).",
    )
    return parser.parse_args(argv)


def _refuse(message: str) -> NoReturn:
    print(f"vantage: {message}", file=sys.stderr)
    raise SystemExit(1)


def _home_directory() -> Path | None:
    """`None` when there is no home directory to find: `HOME` unset and a
    uid with no passwd entry, as in a container run as an unmapped uid.
    Only the default database path needs one, so resolution decides."""
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        return None


def _listen(host: str, port: int) -> socket.socket:
    """A socket bound to `host:port` and listening, the way uvicorn binds
    one it is told to share: IPv6 when the host is an IPv6 literal, and
    `SO_REUSEADDR` on POSIX so a restart is not refused over the previous
    process's connections still in `TIME_WAIT`. Raises `OSError`, including
    `socket.gaierror` for a host that does not resolve."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name == "posix":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        # Listening now, not when uvicorn starts, is what claims the port:
        # a socket that is only bound does not stop another from binding it.
        sock.listen()
    except BaseException:
        sock.close()
        raise
    return sock


def _open_store(database_path: Path) -> SqliteExecutionStore:
    """The store, or a one-line refusal."""
    try:
        ensure_database_directory_writable(database_path)
        return SqliteExecutionStore(database_path)
    except (DatabaseDirectoryNotWritableError, SchemaVersionError) as exc:
        _refuse(str(exc))
    except (OSError, sqlite3.Error) as exc:
        # An ancestor this process cannot search or write, a directory at the
        # path, a file that is not a database: the OS or sqlite3 message
        # already says what is wrong.
        _refuse(f"cannot open the database at {database_path}: {exc}")


def _serve(app: FastAPI, listener: socket.socket, config: ServerConfig) -> None:
    """Run uvicorn on the already bound `listener` until it is stopped."""
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            h11_max_incomplete_event_size=_MAX_REQUEST_HEAD_BYTES,
        )
    )
    # uvicorn announces the address only for a socket it bound itself.
    shown = f"[{config.host}]" if ":" in config.host else config.host
    _UVICORN_LOGGER.info("Listening on http://%s:%d (Press CTRL+C to quit)", shown, config.port)
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:
        # uvicorn raises the SIGINT it shut down for again once it is done,
        # and a stop that was asked for is not a failure.
        pass


def main(argv: list[str] | None = None) -> None:
    """Resolve configuration, refuse anything unusable, warn on a wide bind, serve, then close."""
    args = _parse_args(argv)
    try:
        config = resolve_server_config(
            cli_database=args.database,
            env_database=os.environ.get("VANTAGE_DATABASE"),
            cli_host=args.host,
            cli_port=args.port,
            cli_grace_period=args.grace_period,
            home=_home_directory(),
            xdg_data_home=os.environ.get("XDG_DATA_HOME"),
        )
    except ServerConfigError as exc:
        _refuse(str(exc))

    try:
        listener = _listen(config.host, config.port)
    except OSError as exc:
        _refuse(f"cannot listen on {config.host} port {config.port}: {exc}")

    try:
        store = _open_store(config.database_path)
    except BaseException:
        listener.close()
        raise

    # Warn only once the port and the database are both held: a refused
    # start makes no bind to warn about.
    warn_if_bound_wide(config.host)
    try:
        app = create_app(
            store, grace_period_seconds=config.grace_period_seconds, close_store_on_shutdown=True
        )
        _serve(app, listener, config)
    finally:
        # Also on the paths where the app never shut down: closing twice is
        # harmless.
        store.close()
        listener.close()


__all__ = [
    "DatabaseDirectoryNotWritableError",
    "ensure_database_directory_writable",
    "main",
    "warn_if_bound_wide",
]
