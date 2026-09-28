"""`vantage` -- resolve configuration, fail fast, then serve; `vantage push`
(`service/push.py`), which sends the runs the plugin queued; and `vantage
user`, `vantage token` and `vantage project` (`service/manage.py`), which
manage who may use the database and the projects in it.

**Serving needs the `server` extra; nothing else does.** FastAPI and
uvicorn are imported only once the arguments ask to serve, so `vantage
push`, `vantage user`, `vantage token`, `vantage project` and `--help` work
in an install
without them, and serving without them is one line naming the extra --
checked before anything is bound or created.

**Path check at startup.** A SQLite database directory that exists but
cannot be written to fails here, before the server accepts a request -- not
on the first report, by which point the plugin that sent it has exited and
the report is lost. Resolution itself (`core/config/resolution.py`) is pure
and creates nothing.

**Every startup refusal is one line.** The `server` extra missing, a
setting resolution refuses, an address this process cannot listen on (a
port already in use, a host that does not resolve), a database this
process cannot open, create or connect to, a PostgreSQL database without
its driver installed, and a database from another schema version all end
as a single `vantage: ...` line on stderr and exit status 1, never a
traceback. The listening socket is bound here,
before the database is opened, so a refused bind creates nothing; uvicorn
is handed the bound socket rather than binding its own, which it would do
only after the database exists and would report in several log lines and
exit status 3.

**PostgreSQL is optional.** Its adapter is imported only when a PostgreSQL
URL is given, since the driver it needs comes with the `postgres` extra and
a SQLite server must start without it. A URL is only ever shown redacted,
every message quoted from the driver has the URL's password taken out
first, and the driver's own logging is silenced while the store opens.

**The store is closed by the app's shutdown** (`create_app`'s
`close_store_on_shutdown`). On SIGTERM uvicorn shuts the app down and then
raises the signal again with its default action, which ends the process
inside `Server.run`, so a `finally` here never runs on that path -- except
as a container's PID 1, which the kernel does not let a default action end:
there `main` returns through its `finally`, with exit status 0.

**A database of the server's own starts with an admin.** Before serving,
a database with no user gets the admin `admin`, with a random password
printed once, on one stderr line, by the start whose write created it
(`create_first_admin`): so a server never serves its own database open. A
database pytest-vantage's local store made (`meta.origin`) is the
exception, served open, as it holds one person's runs, until its first
`vantage user add`. The line is printed only once the user is stored, so a
printed password is always the stored one, and never through `logging`,
whose handlers could copy it anywhere. A crash between the two leaves an
admin nobody knows the password of, which `vantage user password admin`
recovers; a user made before the first start keeps any password out of
the log.

**Network exposure.** Binding wider than the loopback default, to a
database with no user, warns that nothing authenticates the requests. The
default warns about nothing, and neither does a database with users, whose
server requires a token: a warning on every normal start trains people to
ignore the one that matters. Since the server gives its own databases a
user, only a database the local store made can warn.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from vantage.core.config.database import (
    DatabaseTarget,
    PostgresTarget,
    redact_message,
    redacted,
)
from vantage.core.config.resolution import (
    ServerConfig,
    ServerConfigError,
    resolve_server_config,
)
from vantage.core.domain.passwords import hash_password, new_password
from vantage.core.ports.storage import ExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage.storage.version import SchemaVersionError

if TYPE_CHECKING:
    from fastapi import FastAPI

_LOGGER = logging.getLogger(__name__)
_LOOPBACK = "127.0.0.1"

# uvicorn's own logger, which its logging configuration prints at INFO; this
# module's logger is only printed from WARNING up.
_UVICORN_LOGGER = logging.getLogger("uvicorn.error")

_SERVER_EXTRA_MODULES = frozenset({"fastapi", "starlette", "uvicorn"})
_SERVER_EXTRA_MISSING = "serving needs the server extra: pip install 'vantage[server]'"

_PLUGIN_MODULES = frozenset({"pytest_vantage"})
_PLUGIN_OUTBOX_MISSING = (
    "push sends pytest-vantage's outbox, and the pytest-vantage installed here has none: "
    "pip install --upgrade pytest-vantage"
)

_POSTGRES_ADAPTER = "vantage.storage.postgres"
_POSTGRES_DRIVER_MODULES = frozenset({"psycopg", "psycopg_pool"})
_POSTGRES_DRIVER_MISSING = "PostgreSQL needs the postgres extra: pip install 'vantage[postgres]'"

# Above every level, so the driver's loggers pass nothing on.
_SILENT = logging.CRITICAL + 1


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


def warn_if_bound_wide(host: str, *, access_required: bool) -> None:
    """Warn, naming the missing authentication, for any bind address but
    the loopback default, unless the database has a user and so the server
    requires a token."""
    if host != _LOOPBACK and not access_required:
        _LOGGER.warning(
            "Binding to %s with no user in the database, which pytest-vantage's local store "
            "made: nothing authenticates requests, so anyone who can route to this host can "
            "read and write everything. Add one with vantage user add NAME --admin, and the "
            "server requires a token.",
            host,
        )


FIRST_ADMIN = "admin"
"""The user `create_first_admin` makes."""


def create_first_admin(store: ExecutionStore, database: DatabaseTarget) -> None:
    """Give the database the admin `FIRST_ADMIN`, with a fresh random
    password printed once on stderr, if it has no user and the local store
    did not make it; or refuse in one line. The store decides, in one
    write, so of two servers starting on one new database one creates the
    user and prints its password; a database with a user skips the hash."""
    try:
        if store.access_required():
            return
        password = new_password()
        created = store.create_first_admin(
            FIRST_ADMIN,
            password_hash=hash_password(password),
            created_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        if isinstance(database, PostgresTarget):
            shown, detail = redacted(database.url), _driver_detail(exc, database.url)
        else:
            shown, detail = str(database.path), " ".join(str(exc).split()) or type(exc).__name__
        _refuse(f"cannot create the user {FIRST_ADMIN} in {shown}: {detail}")
    if created is not None:
        # Password last, so copying it picks up nothing else.
        print(
            f"vantage: created the user {FIRST_ADMIN}; change its password at once "
            f"(vantage user password {FIRST_ADMIN}, or POST /api/v1/password). "
            f"Shown this once: {password}",
            file=sys.stderr,
            flush=True,
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vantage",
        description="Run the vantage server.",
        epilog=(
            "vantage push sends the runs pytest-vantage queued for a server it could not "
            "reach, vantage user manages the users of the database, vantage token their "
            "tokens and vantage project its projects and their members; each one's --help "
            "says how."
        ),
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Path to the SQLite database file, or a postgresql:// URL.",
    )
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


def home_directory() -> Path | None:
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


def open_store(database: DatabaseTarget) -> ExecutionStore:
    """The store, or a one-line refusal."""
    if isinstance(database, PostgresTarget):
        return _open_postgres(database.url)
    return _open_sqlite(database.path)


def _open_sqlite(database_path: Path) -> SqliteExecutionStore:
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


def _require_the_server_extra() -> None:
    """Import what serving needs, or refuse in one line naming the extra.
    An import failure in anything else is a fault in this installation, and
    raised as it is."""
    try:
        importlib.import_module("uvicorn")
        importlib.import_module("vantage.service.app")
    except ImportError as exc:
        if not _is_missing(exc, _SERVER_EXTRA_MODULES):
            raise
        _refuse(_SERVER_EXTRA_MISSING)


def _open_postgres(url: str) -> ExecutionStore:
    shown = redacted(url)
    try:
        # By name, through `sys.modules`, so nothing but a PostgreSQL start
        # ever imports the adapter or its driver.
        adapter = importlib.import_module(_POSTGRES_ADAPTER)
    except ImportError as exc:
        # psycopg without the libpq it needs fails with a plain `ImportError`
        # raised in its own module, which counts as the driver missing too.
        if not _is_missing(exc, _POSTGRES_DRIVER_MODULES):
            raise
        _refuse(_POSTGRES_DRIVER_MISSING)
    # The pool logs each failed connection attempt itself, on stderr before
    # any logging is configured, quoting libpq -- which quotes a URL it
    # cannot parse, password and all. Its loggers are silenced while the
    # store opens, so a refusal stays one line, and stay silenced after a
    # refusal, in case a pool still winding down logs once more.
    driver_loggers = {
        logger: logger.level for logger in map(logging.getLogger, sorted(_POSTGRES_DRIVER_MODULES))
    }
    for logger in driver_loggers:
        logger.setLevel(_SILENT)
    try:
        store: ExecutionStore = adapter.PostgresExecutionStore(url)
    except SchemaVersionError as exc:
        detail = _driver_detail(exc, url)
        _refuse(detail if shown in detail else f"{shown}: {detail}")
    except Exception as exc:
        # The driver's own errors cannot be named here without importing it
        # into the service; every one of them is a start that cannot go on.
        _refuse(f"cannot open the database at {shown}: {_driver_detail(exc, url)}")
    for logger, level in driver_loggers.items():
        logger.setLevel(level)
    return store


def _is_missing(exc: ImportError, modules: frozenset[str]) -> bool:
    """Whether what failed to import is one of `modules` -- top-level names
    an extra installs -- or a module within one: absent, or raising
    `ImportError` itself while it imports. Any other `ImportError` is a fault
    in this installation, not a missing extra."""
    module = exc.name
    if module is None:
        frame = exc.__traceback__
        while frame is not None and frame.tb_next is not None:
            frame = frame.tb_next
        module = frame.tb_frame.f_globals.get("__name__") if frame is not None else None
    return str(module).partition(".")[0] in modules


def _driver_detail(exc: Exception, url: str) -> str:
    """`exc`'s message on one line, with `url`'s password taken out: the
    driver spreads a connection failure over several lines, and quotes a
    URL it cannot parse."""
    detail = " ".join(redact_message(str(exc), url).split())
    return detail or type(exc).__name__


def _serve(app: FastAPI, listener: socket.socket, config: ServerConfig) -> None:
    """Run uvicorn on the already bound `listener` until it is stopped."""
    import uvicorn

    from vantage.service.errors import MAX_REPORT_BYTES

    # How much of a request's line and headers the server buffers before it
    # has them whole; past it, the request is refused before any route sees
    # it. A stored node id is read back by value, in the query string, and it
    # can be as long as a report allows: at most `MAX_REPORT_BYTES` of UTF-8,
    # each byte percent-encoded in at most three. The HTTP parser's own
    # default, 16 KiB, left a result that `/results` lists impossible to fetch.
    max_request_head_bytes = 3 * MAX_REPORT_BYTES + 64 * 1024
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            h11_max_incomplete_event_size=max_request_head_bytes,
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
    """`vantage user ...`, `vantage token ...` and `vantage project ...`
    manage users, tokens and projects, and `vantage push ...` sends the
    queued runs. Anything else serves: resolve configuration, refuse
    anything unusable, give a database of the server's own its first admin,
    warn on a wide bind, serve, then close."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] in (["user"], ["token"], ["project"]):
        from vantage.service import manage

        command = {"user": manage.user, "token": manage.token, "project": manage.project}
        raise SystemExit(command[arguments[0]](arguments[1:]))
    if arguments[:1] == ["push"]:
        try:
            from vantage.service.push import push
        except ImportError as exc:
            # The outbox is the plugin's: a pytest-vantage older than this
            # vantage, installed beside it, has none to send.
            if not _is_missing(exc, _PLUGIN_MODULES):
                raise
            _refuse(_PLUGIN_OUTBOX_MISSING)
        raise SystemExit(push(arguments[1:]))

    args = _parse_args(arguments)
    _require_the_server_extra()
    from vantage.service.app import create_app

    try:
        config = resolve_server_config(
            cli_database=args.database,
            env_database=os.environ.get("VANTAGE_DATABASE"),
            cli_host=args.host,
            cli_port=args.port,
            cli_grace_period=args.grace_period,
            home=home_directory(),
            xdg_data_home=os.environ.get("XDG_DATA_HOME"),
        )
    except ServerConfigError as exc:
        _refuse(str(exc))

    try:
        listener = _listen(config.host, config.port)
    except OSError as exc:
        _refuse(f"cannot listen on {config.host} port {config.port}: {exc}")

    try:
        store = open_store(config.database)
    except BaseException:
        listener.close()
        raise

    try:
        create_first_admin(store, config.database)
        # Warn only once the port and the database are both held: a refused
        # start makes no bind to warn about.
        warn_if_bound_wide(config.host, access_required=store.access_required())
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
    "FIRST_ADMIN",
    "DatabaseDirectoryNotWritableError",
    "create_first_admin",
    "ensure_database_directory_writable",
    "home_directory",
    "main",
    "open_store",
    "warn_if_bound_wide",
]
