"""The plugin's only door to the `vantage` package, for the local modes.

The plugin's own code never opens the vantage database: a run stored
locally is handed to `vantage.local`, which runs the server's own ingestion
in-process, so a run stored here and one sent to a server are validated and
written by the same code. `pytest-vantage` never declares `vantage`, so it
is imported here only, inside the functions, and only once a local mode is
configured: a session recording to a server alone never imports it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path


class LocalStorageUnavailableError(Exception):
    """`vantage.local` cannot be imported; the message says what to do."""


class LocalStoreFailedError(Exception):
    """The run could not be stored; the message is one line."""


def require(mode: str) -> None:
    """Raise `LocalStorageUnavailableError` unless `vantage.local` imports,
    naming `mode`, the setting that needs it."""
    try:
        import vantage.local  # noqa: F401 -- importing it is the whole check
    except ModuleNotFoundError as exc:
        if exc.name in ("vantage", "vantage.local"):
            raise LocalStorageUnavailableError(
                f"vantage: --vantage-mode {mode} stores runs locally and needs the vantage "
                "package: pip install vantage"
            ) from None
        raise LocalStorageUnavailableError(
            f"vantage: --vantage-mode {mode} stores runs locally and could not load "
            f"the vantage package: {exc}"
        ) from None
    except Exception as exc:  # a broken install; never BaseException
        raise LocalStorageUnavailableError(
            f"vantage: --vantage-mode {mode} stores runs locally and could not load "
            f"the vantage package: {exc}"
        ) from None


def default_database_path() -> Path:
    """The `vantage` command's own default database path."""
    from vantage.local import default_database_path as vantage_default

    return Path(vantage_default())


def store_reports(database: Path, reports: Sequence[Mapping[str, object]]) -> None:
    """Store `reports`, one session's in send order, in the SQLite database
    at `database`, or raise `LocalStoreFailedError`."""
    from vantage.local import LocalStoreError
    from vantage.local import store_reports as vantage_store

    try:
        vantage_store(database, reports)
    except LocalStoreError as exc:
        raise LocalStoreFailedError(str(exc)) from None
    except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
        raise LocalStoreFailedError(f"{type(exc).__name__}: {exc}") from None


__all__ = [
    "LocalStorageUnavailableError",
    "LocalStoreFailedError",
    "default_database_path",
    "require",
    "store_reports",
]
