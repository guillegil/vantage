"""`vantage` startup: every refusal is one `vantage: ...` line and exit
status 1, a bad setting is refused before anything is created, and the
store is closed once the server stops.

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
from pathlib import Path
from typing import Any

import pytest
from vantage.service import cli
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

    assert served["app"].state.grace_period == 60.0


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
