"""`vantage` startup: every refusal is one `vantage: ...` line and exit
status 1, a bad setting is refused before anything is created, and the
store is closed once the server stops. Also the pieces `main` composes: the
writable-directory check, the wide-bind warning, and the grace period
`create_app` builds.

`uvicorn.run` is replaced throughout -- these tests are about what `main`
does around serving, and none of them binds a socket. It is patched by
dotted path rather than through `cli.uvicorn`, because `cli.py`'s `__all__`
does not re-export its imports and mypy flags reaching through the module.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from vantage.service import cli
from vantage.service.app import create_app
from vantage.service.cli import (
    DatabaseDirectoryNotWritableError,
    ensure_database_directory_writable,
    warn_if_bound_wide,
)
from vantage.storage.memory import InMemoryExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore

# Root ignores directory mode bits; Windows ACLs need a different check.
_needs_enforced_mode_bits = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX directory mode bits that this process cannot bypass",
)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stands in for `uvicorn.run` and records the app `main` handed it."""
    served: dict[str, Any] = {}

    def _run(app: object, **_kwargs: object) -> None:
        served["app"] = app

    monkeypatch.setattr("vantage.service.cli.uvicorn.run", _run)
    return served


@pytest.fixture
def never_served(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("main started serving a configuration it should refuse")

    monkeypatch.setattr("vantage.service.cli.uvicorn.run", _run)


def _refusal(capsys: pytest.CaptureFixture[str], argv: list[str]) -> str:
    """Run `main`, assert it refused with one clean line, and return that line."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("vantage: ")
    assert err.count("\n") == 1, err
    return err


@pytest.mark.usefixtures("never_served")
@pytest.mark.parametrize(
    ("setting", "flag"),
    [
        (["--grace-period", "0"], "--grace-period"),
        (["--port", "70000"], "--port"),
        (["--host", ""], "--host"),
    ],
    ids=["grace-period", "port", "empty-host"],
)
def test_an_unusable_setting_is_refused_before_anything_is_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], setting: list[str], flag: str
) -> None:
    database_dir = tmp_path / "db"

    err = _refusal(capsys, ["--database", str(database_dir / "v.db"), *setting])

    assert flag in err
    assert not database_dir.exists()


@_needs_enforced_mode_bits
@pytest.mark.usefixtures("never_served")
@pytest.mark.parametrize(
    ("mode", "below"),
    [(0o500, "v.db"), (0o500, "sub/v.db"), (0o000, "sub/v.db")],
    ids=["read-only-parent", "read-only-ancestor", "unsearchable-ancestor"],
)
def test_a_database_under_an_unusable_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: int, below: str
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=mode)
    try:
        err = _refusal(capsys, ["--database", str(locked / below)])
    finally:
        locked.chmod(0o700)  # so tmp_path's own fixture cleanup can remove it

    assert str(locked) in err


@pytest.mark.usefixtures("never_served")
def test_a_database_from_another_schema_version_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "v.db"
    SqliteExecutionStore(database).close()
    with contextlib.closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")

    err = _refusal(capsys, ["--database", str(database)])

    assert "schema_version is 1" in err


@pytest.mark.usefixtures("never_served")
def test_a_path_sqlite_cannot_open_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory where the database file should be: sqlite3's own error,
    not a traceback."""
    database = tmp_path / "v.db"
    database.mkdir()

    err = _refusal(capsys, ["--database", str(database)])

    assert str(database) in err


@pytest.mark.usefixtures("never_served")
def test_a_refused_start_does_not_warn_about_a_bind_it_never_makes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "v.db"
    database.mkdir()

    with caplog.at_level(logging.WARNING, logger=cli.__name__):
        _refusal(capsys, ["--host", "0.0.0.0", "--database", str(database)])  # noqa: S104

    assert [r.getMessage() for r in caplog.records if r.name == cli.__name__] == []


@pytest.mark.usefixtures("served")
def test_main_reads_the_database_path_from_vantage_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "from-env" / "v.db"
    monkeypatch.setenv("VANTAGE_DATABASE", str(database))
    # Were the variable ignored, the default would land here, not in $HOME.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    cli.main([])

    assert database.exists()
    assert not (tmp_path / "xdg").exists()


def test_main_carries_the_resolved_grace_period_into_the_app(
    tmp_path: Path, served: dict[str, Any]
) -> None:
    """The seam between a resolved config and a running app, which neither
    half's own test can see: if `main` drops `grace_period_seconds=` from the
    `create_app` call, `--grace-period 60` silently runs at the 900-second
    default.
    """
    cli.main(["--database", str(tmp_path / "v.db"), "--grace-period", "60"])

    assert served["app"].state.grace_period == timedelta(seconds=60)


@pytest.mark.parametrize("stops_with", [None, SystemExit(3)], ids=["returns", "exits"])
def test_main_closes_the_store_when_the_server_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stops_with: SystemExit | None
) -> None:
    """uvicorn exits with status 3 when it cannot bind, so the store must be
    closed on that path as well as on a normal shutdown."""
    served: dict[str, Any] = {}

    def _run(app: Any, **_kwargs: object) -> None:
        served["store"] = app.state.store
        if stops_with is not None:
            raise stops_with

    monkeypatch.setattr("vantage.service.cli.uvicorn.run", _run)

    with contextlib.suppress(SystemExit):
        cli.main(["--database", str(tmp_path / "v.db")])

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        served["store"].count_executions()


@_needs_enforced_mode_bits
def test_ensure_database_directory_writable_raises_on_read_only_parent(tmp_path: Path) -> None:
    """A directory this process cannot write to fails loudly at startup
    rather than silently at the first write."""
    parent = tmp_path / "readonly"
    parent.mkdir(mode=0o500)
    try:
        with pytest.raises(DatabaseDirectoryNotWritableError, match=str(parent)):
            ensure_database_directory_writable(parent / "vantage.db")
    finally:
        parent.chmod(0o700)  # so tmp_path's own fixture cleanup can remove it


def test_ensure_database_directory_writable_passes_for_a_writable_parent(tmp_path: Path) -> None:
    ensure_database_directory_writable(tmp_path / "vantage.db")


def test_default_host_emits_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A warning on every normal start trains people to ignore the one that
    matters."""
    with caplog.at_level(logging.WARNING):
        warn_if_bound_wide("127.0.0.1")

    assert caplog.records == []


def test_non_loopback_host_warns_naming_missing_authentication(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Binding wider than loopback, with no authentication in front of the
    server, warns and names what is missing."""
    with caplog.at_level(logging.WARNING):
        warn_if_bound_wide("0.0.0.0")  # noqa: S104

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("authentication" in message for message in messages)
    assert any("0.0.0.0" in message for message in messages)  # noqa: S104


def test_create_app_defaults_grace_period_to_900_seconds() -> None:
    app = create_app(InMemoryExecutionStore())

    assert app.state.grace_period == timedelta(seconds=900)


def test_create_app_exposes_the_configured_grace_period() -> None:
    app = create_app(InMemoryExecutionStore(), grace_period_seconds=123.0)

    assert app.state.grace_period == timedelta(seconds=123)


@pytest.mark.parametrize("seconds", [0.0, -1.0, float("nan"), float("inf"), 1e14])
def test_create_app_refuses_a_grace_period_it_cannot_apply(seconds: float) -> None:
    """Refused when the app is built, not on every read of a run: nan, inf
    and 1e14 cannot become a `timedelta`, and a grace period that is not
    positive would present every unfinished run as abandoned."""
    with pytest.raises((ValueError, OverflowError)):
        create_app(InMemoryExecutionStore(), grace_period_seconds=seconds)
