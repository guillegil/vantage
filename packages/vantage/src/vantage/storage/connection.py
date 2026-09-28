"""Open the vantage database: owner-only file creation and a single,
idempotent schema application.

`sqlite3.connect` creates a missing database file itself, at 0644 under a
permissive umask, so a `chmod` afterwards leaves a window in which another
user can open it. The file is therefore created at 0600 before `sqlite3`
ever sees the path. Only what this module creates is made owner-only: an
existing directory or database file keeps the mode its owner chose, and
SQLite gives the `-wal` and `-shm` files the database file's own mode, so
they follow whichever it has.

A database from a different schema version is refused, not migrated, and so
is a file that already holds some other schema. `_apply_schema` stamps
`meta.schema_version`, and `meta.origin` -- whether pytest-vantage's local
store made the database -- inside the same transaction that creates the
tables.
Everything that decides a refusal only reads, and write-ahead logging --
which is persistent, and rewrites the file's header -- is switched on only
once the database is known to be this build's, so a refused database is
left exactly as it was found.
"""

from __future__ import annotations

import getpass
import logging
import os
import sqlite3
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.storage.version import _SCHEMA_VERSION, SchemaVersionError

_LOGGER = logging.getLogger(__name__)

_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")

# A table present iff schema.sql has already been applied -- checked so a
# reopen issues no DDL statement at all, not merely a harmless one thanks to
# schema.sql's own `IF NOT EXISTS`.
_SCHEMA_SENTINEL_TABLE = "meta"

# `OR IGNORE` keeps a second process racing to create the same fresh
# database from failing on the row the first one stamped.
_STAMP_SCHEMA_VERSION = "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)"

# Who made the database: `local` for pytest-vantage's local store, `server`
# for anything else. Only the transaction that creates the tables writes
# it, so opening an existing database with either flag never changes it,
# and of two processes creating one file, the first one's value stays.
_STAMP_ORIGIN = "INSERT OR IGNORE INTO meta (key, value) VALUES ('origin', ?)"

# The project of the runs that name none, which every database has from its
# creation: written with the stamp, not with the best-effort creation
# metadata, since reports depend on it.
_CREATE_DEFAULT_PROJECT = "INSERT OR IGNORE INTO project (name, created_at) VALUES (?, ?)"

# How long a connection waits for another's lock before giving up.
_BUSY_TIMEOUT_SECONDS = 5.0
_WAL_RETRY_INTERVAL_SECONDS = 0.01


def isoformat_utc(moment: datetime) -> str:
    """The text every timestamp is stored as: fixed-width ISO-8601 UTC,
    `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`, the format the plugin sends.

    Converted to UTC first, whatever offset the caller's value carries, and
    always with six fractional digits -- plain `isoformat()` drops them when
    they are zero -- so every value has one width and text order is
    chronological order. Not `strftime`, whose `%Y` does not pad a year
    below 1000 on every platform.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def open_database(path: Path, *, local: bool = False) -> sqlite3.Connection:
    """Open (creating if absent) the database at `path`. `local` says that
    pytest-vantage's local store is opening it, which `meta.origin` records
    if this call creates it.

    Creates a missing parent directory at 0700 and a missing database file at
    0600 before `sqlite3.connect` runs, applies `schema.sql` inside one
    transaction on first creation only, and -- on POSIX -- warns without
    rewriting the mode of an existing database an operator deliberately
    widened, once the database has been accepted. Any failure after
    connecting closes the connection before the error propagates.
    """
    path = Path(path)
    parent = path.parent
    is_posix = os.name == "posix"

    # An existing directory -- a home directory, a shared checkout, `/tmp` --
    # is never re-moded: its owner chose the mode, and `chmod` on one owned
    # by another user fails outright. A created one is never wider than
    # 0700, since the umask can only take bits away.
    try:
        os.makedirs(parent, mode=0o700)
    except FileExistsError:
        pass

    created = _create_database_file(path) if is_posix else False

    # `check_same_thread=False`: the store may be called from any thread, so
    # several can share this one connection; `SqliteExecutionStore`'s lock
    # serialises them. `timeout`: a second process contending for the
    # write lock waits rather than failing instantly with `SQLITE_BUSY`.
    conn = sqlite3.connect(
        str(path), isolation_level=None, check_same_thread=False, timeout=_BUSY_TIMEOUT_SECONDS
    )
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # A committed session survives a power loss; one fsync per session is
        # not noticeable.
        conn.execute("PRAGMA synchronous = FULL")

        objects = _schema_objects(conn)
        if _SCHEMA_SENTINEL_TABLE in objects:
            _check_schema_version(conn, path)
        elif objects:
            raise SchemaVersionError(
                f"{path} holds tables but no schema_version stamp, so it is not a vantage "
                "database; nothing was added to it. Choose a different path."
            )
        else:
            _apply_schema(conn, local=local)
            _stamp_creation_metadata(conn)
        _enable_wal(conn)
    except BaseException:
        # Closing rolls back a half-applied schema and releases its write
        # lock now, not whenever the traceback that references `conn` is
        # collected.
        conn.close()
        raise

    # Only now: a refused database gets its one-line refusal, not a warning
    # that recording will continue first.
    if is_posix and not created:
        _warn_if_permissive(path)
    return conn


def _create_database_file(path: Path) -> bool:
    """Create `path` at 0600 and return True, or return False if it exists."""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _warn_if_permissive(path: Path) -> None:
    """Report, never rewrite -- an operator may have widened the mode on purpose."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        _LOGGER.warning(
            "%s has permissive mode %s (expected 0600); recording will continue.",
            path,
            oct(mode),
        )


def _enable_wal(conn: sqlite3.Connection) -> None:
    row = _switch_to_wal(conn)
    mode = row[0] if row is not None else None
    if mode != "wal":
        _LOGGER.warning(
            "journal_mode=WAL was not applied (got %r); continuing in the fallback mode.",
            mode,
        )


def _switch_to_wal(conn: sqlite3.Connection) -> tuple[object, ...] | None:
    """`PRAGMA journal_mode=WAL`'s answer, retried while another connection
    holds the lock it needs, for as long as the busy timeout.

    Switching needs the file to itself for an instant. When two connections
    switch a new database at once, SQLite answers one of them "database is
    locked" straight away rather than through the busy timeout, since
    waiting could deadlock; the other's switch then completes, and the
    retry finds the database in WAL already."""
    deadline = time.monotonic() + _BUSY_TIMEOUT_SECONDS
    while True:
        try:
            row: tuple[object, ...] | None = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            return row
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc) or time.monotonic() >= deadline:
                raise
        time.sleep(_WAL_RETRY_INTERVAL_SECONDS)


def _schema_objects(conn: sqlite3.Connection) -> set[str]:
    """The names of every table, index, view and trigger in the database,
    SQLite's own `sqlite_*` bookkeeping aside. Empty for a new file."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
    ).fetchall()
    return {row[0] for row in rows}


def _apply_schema(conn: sqlite3.Connection, *, local: bool) -> None:
    """Create the tables, the `default` project, the version stamp and the
    origin in one transaction, so a database never holds some without the
    others.

    `executescript` commits a pending transaction before it runs, never
    after, so `BEGIN IMMEDIATE` opens the script and the parameterised stamp
    and the `COMMIT` follow it on the same, still-open transaction.

    Two processes opening the same new file can both find it empty and both
    get here; the second waits for the first's write lock, then every
    `IF NOT EXISTS` statement and the `OR IGNORE` stamps leave the first's
    schema and origin as they are.
    """
    schema_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(f"BEGIN IMMEDIATE;\n{schema_sql}")
    conn.execute(_STAMP_SCHEMA_VERSION, (str(_SCHEMA_VERSION),))
    conn.execute(_STAMP_ORIGIN, ("local" if local else "server",))
    conn.execute(
        _CREATE_DEFAULT_PROJECT, (DEFAULT_PROJECT, isoformat_utc(datetime.now(timezone.utc)))
    )
    conn.execute("COMMIT")


def _parse_schema_version(raw: str | None) -> int | None:
    """Absent or non-integer both read back as `None`, so
    `_check_schema_version` has a single comparison to make.
    """
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _check_schema_version(conn: sqlite3.Connection, path: Path) -> None:
    """Refuse a database whose `meta.schema_version` does not equal
    `_SCHEMA_VERSION`, issuing no DDL. Older *and* newer are both refused: a
    build that does not know a column cannot honour whatever invariant the
    build that added it assumed.
    """
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    found = _parse_schema_version(row[0] if row is not None else None)
    if found == _SCHEMA_VERSION:
        return

    found_description = "absent" if found is None else str(found)
    raise SchemaVersionError(
        f"{path}: schema_version is {found_description}, but this build requires "
        f"schema_version {_SCHEMA_VERSION}; recreate the database."
    )


def _stamp_creation_metadata(conn: sqlite3.Connection) -> None:
    """Best-effort `created_at`/`created_by` rows in `meta`, written after
    schema creation. Nothing keys off these, so a failure is logged and
    swallowed rather than raised.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)",
            (isoformat_utc(datetime.now(timezone.utc)),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_by', ?)",
            (getpass.getuser(),),
        )
    except (sqlite3.Error, OSError, KeyError, ImportError):
        # Before 3.13, `getpass.getuser()` raises `KeyError` when the uid has
        # no passwd entry (a container run as an unmapped uid) and
        # `ImportError` on Windows with no `USERNAME` set; 3.13+ raises
        # `OSError`. None of them may abort server startup.
        _LOGGER.warning("failed to stamp created_at/created_by in meta", exc_info=True)
