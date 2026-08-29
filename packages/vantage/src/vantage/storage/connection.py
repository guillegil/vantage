"""Open the vantage database: owner-only creation (RQ-40) and a single,
idempotent schema application (RQ-29), per design.md D9.

`sqlite3.connect` creates the database file itself, at 0644 under a
permissive umask -- so a `chmod` after `connect` leaves a window in which
another user can open the file before this process ever narrows its mode.
The file is created explicitly, at 0600, before `sqlite3` ever sees the
path, which closes that window (D9's load-bearing detail).

A database from a different schema version is refused, not migrated
(ADR-0013, design.md D28): `schema.sql` stamps `meta.schema_version` as the
last statement of its own transaction, so the stamp is atomic with the
schema it describes. Reading it back is the only DDL-free way to tell "this
database predates a column this build expects" from "this database matches",
and the refusal issues no DDL of its own -- refusing is not altering, which
is what keeps RQ-29.2 literally true across a schema change.
"""

from __future__ import annotations

import getpass
import logging
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")

# A table present iff schema.sql has already been applied -- checked so a
# reopen issues no DDL statement at all (RQ-29.2), not merely a harmless one
# thanks to schema.sql's own `IF NOT EXISTS`.
_SCHEMA_SENTINEL_TABLE = "meta"

# Bumped whenever a later Phase 1 release needs a column an earlier one did
# not have (ADR-0013). `schema.sql`'s own last statement stamps this same
# value into `meta.schema_version`, atomically with the schema it describes --
# the two must move together, or `open_database` refuses its own fresh schema.
_SCHEMA_VERSION = 3


class SchemaVersionError(RuntimeError):
    """`meta.schema_version` does not match what this build requires.

    Raised by `open_database` before any DDL runs against the mismatched
    database, and after the connection that read the mismatch is already
    closed (ADR-0013) -- refusing is not altering.
    """


def open_database(path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the database at `path`.

    Creates `path`'s parent directory and an `artifacts/` sibling at 0700,
    creates the database file itself at 0600 before `sqlite3.connect` runs,
    applies `schema.sql` inside one transaction on first creation only, and
    -- on POSIX -- warns without rewriting the mode of an existing database
    an operator deliberately widened.
    """
    path = Path(path)
    parent = path.parent
    artifacts_dir = parent / "artifacts"
    is_posix = os.name == "posix"

    os.makedirs(parent, mode=0o700, exist_ok=True)
    os.makedirs(artifacts_dir, mode=0o700, exist_ok=True)

    if is_posix:
        # `makedirs`' `mode` is masked by umask -- 022 would leave 0755.
        os.chmod(parent, 0o700)
        os.chmod(artifacts_dir, 0o700)
        _create_database_file_or_warn(path)

    # `check_same_thread=False`: the server runs this handler in a
    # threadpool (D8), so several threads share one connection object.
    # `timeout=5.0`: the cross-process half of D8's concurrency net -- a
    # *second process* contending for the write lock waits rather than
    # failing instantly with `SQLITE_BUSY`.
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA foreign_keys = ON")
    # RQ-3 asks for all-or-nothing; one fsync per session is a cost nothing
    # notices (D8).
    conn.execute("PRAGMA synchronous = FULL")
    _enable_wal(conn)

    if _schema_already_applied(conn):
        _check_schema_version(conn, path)
    else:
        _apply_schema(conn)
        _stamp_creation_metadata(conn)

    if is_posix:
        _secure_wal_sidecars(path)

    return conn


def _create_database_file_or_warn(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        _warn_if_permissive(path)
    else:
        os.close(fd)


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
    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    mode = row[0] if row is not None else None
    if mode != "wal":
        _LOGGER.warning(
            "journal_mode=WAL was not applied (got %r); continuing in the fallback mode.",
            mode,
        )


def _schema_already_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_SCHEMA_SENTINEL_TABLE,),
    ).fetchone()
    return row is not None


def _apply_schema(conn: sqlite3.Connection) -> None:
    schema_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(f"BEGIN IMMEDIATE;\n{schema_sql}\nCOMMIT;")


def _parse_schema_version(raw: str | None) -> int | None:
    """Absent or non-integer both read back as `None` -- D28's "absent, or not
    an integer" branch of the refusal table, collapsed to one representation
    so `_check_schema_version` has a single comparison to make.
    """
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _check_schema_version(conn: sqlite3.Connection, path: Path) -> None:
    """Refuse a database whose `meta.schema_version` does not equal
    `_SCHEMA_VERSION` -- issuing no DDL, and closing `conn` before raising
    (ADR-0013, design.md D28). Older *and* newer are both refused: a build
    that does not know a column cannot honour whatever invariant the build
    that added it assumed, in either direction.
    """
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    found = _parse_schema_version(row[0] if row is not None else None)
    if found == _SCHEMA_VERSION:
        return

    conn.close()
    found_description = "absent" if found is None else str(found)
    raise SchemaVersionError(
        f"{path}: schema_version is {found_description}, but this build requires "
        f"schema_version {_SCHEMA_VERSION}; recreate the database (see ADR-0013)."
    )


def _stamp_creation_metadata(conn: sqlite3.Connection) -> None:
    """Best-effort `created_at`/`created_by` rows in `meta`, written after
    schema creation (design.md D28). Nothing keys off these -- unlike
    `schema_version`, which `schema.sql` itself stamps atomically with the
    schema -- so a failure here is logged and swallowed rather than raised;
    losing them to a crash costs nothing.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_by', ?)",
            (getpass.getuser(),),
        )
    except (sqlite3.Error, OSError, KeyError, ImportError):
        # `getpass.getuser()` only normalises its failures to `OSError` on
        # 3.13+; its own docstring records the change. On 3.10-3.12 -- three of
        # this project's four CI legs -- it raises `KeyError` from
        # `pwd.getpwuid(os.getuid())` when the uid has no passwd entry, and
        # `ImportError` on Windows with no `USERNAME` set. Creating a database
        # inside a container run as an unmapped uid would otherwise abort the
        # server at startup, which contradicts this function's whole contract:
        # these two rows are a convenience and losing them costs nothing.
        _LOGGER.warning("failed to stamp created_at/created_by in meta", exc_info=True)


def _secure_wal_sidecars(path: Path) -> None:
    """Verified by test rather than trusted: SQLite propagates the main
    file's mode to `-wal`/`-shm`, but if that assumption ever fails on some
    platform, this closes the gap explicitly rather than leaving it to chance.
    """
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        try:
            os.chmod(sidecar, 0o600)
        except FileNotFoundError:
            pass
