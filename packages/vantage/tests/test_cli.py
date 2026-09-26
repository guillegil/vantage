"""`vantage` startup: every refusal is one `vantage: ...` line and exit
status 1, a bad setting is refused before anything is created, and the
store is closed once the server stops. Also the pieces `main` composes: the
writable-directory check, the wide-bind warning, and the grace period
`create_app` builds.

`uvicorn.Server.run` is replaced in every test that calls `main` in
process -- these tests are about what `main` does around serving -- and so
is `_listen`, by a stand-in that records the address asked for and binds an
ephemeral loopback port instead, so no test holds 8765 or a wide address.
Both are patched by dotted path rather than through `cli.uvicorn`, because
`cli.py`'s `__all__` does not re-export its imports and mypy flags reaching
through the module. One test runs the real command in a subprocess, to stop
it the way a service manager does.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from memory_store import InMemoryExecutionStore
from vantage.service import cli
from vantage.service.app import create_app
from vantage.service.cli import (
    DatabaseDirectoryNotWritableError,
    ensure_database_directory_writable,
    warn_if_bound_wide,
)
from vantage.storage.sqlite_store import SqliteExecutionStore

# Root ignores directory mode bits; Windows ACLs need a different check.
_needs_enforced_mode_bits = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX directory mode bits that this process cannot bypass",
)

_REAL_LISTEN = cli._listen


@pytest.fixture
def listened(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, socket.socket]]:
    """Stands in for `_listen`: records each `(host, port)` `main` asked to
    listen on, and binds an ephemeral loopback port instead, recording the
    socket it returned."""
    requested: list[tuple[str, int, socket.socket]] = []

    def _listen(host: str, port: int) -> socket.socket:
        sock = _REAL_LISTEN("127.0.0.1", 0)
        requested.append((host, port, sock))
        return sock

    monkeypatch.setattr("vantage.service.cli._listen", _listen)
    return requested


@pytest.fixture
def served(
    monkeypatch: pytest.MonkeyPatch, listened: list[tuple[str, int, socket.socket]]
) -> dict[str, Any]:
    """Stands in for `uvicorn.Server.run` and records the app and the
    sockets `main` handed it."""
    served: dict[str, Any] = {}

    def _run(server: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        served["app"] = server.config.app
        served["sockets"] = sockets

    monkeypatch.setattr("vantage.service.cli.uvicorn.Server.run", _run)
    return served


def _refuse_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("main started serving a configuration it should refuse")

    monkeypatch.setattr("vantage.service.cli.uvicorn.Server.run", _run)


@pytest.fixture
def never_served(
    monkeypatch: pytest.MonkeyPatch, listened: list[tuple[str, int, socket.socket]]
) -> None:
    _refuse_to_serve(monkeypatch)


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
        (["--grace-period", "1e-7"], "--grace-period"),
        (["--port", "70000"], "--port"),
        (["--host", ""], "--host"),
    ],
    ids=["grace-period", "grace-period-below-a-microsecond", "port", "empty-host"],
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


def test_main_listens_where_it_was_told_hands_uvicorn_that_socket_and_warns(
    tmp_path: Path,
    served: dict[str, Any],
    listened: list[tuple[str, int, socket.socket]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The seam between a resolved config and a running server, and the
    positive control for the refused start that must not warn: a start on a
    wide address warns that nothing authenticates the requests, and serves
    on the socket bound for the address asked for."""
    with caplog.at_level(logging.WARNING, logger=cli.__name__):
        cli.main(["--database", str(tmp_path / "v.db"), "--host", "0.0.0.0", "--port", "9000"])  # noqa: S104

    ((host, port, bound),) = listened
    assert (host, port) == ("0.0.0.0", 9000)  # noqa: S104
    assert served["sockets"] == [bound]
    warnings = [r.getMessage() for r in caplog.records if r.name == cli.__name__]
    assert len(warnings) == 1
    assert "0.0.0.0" in warnings[0]  # noqa: S104
    assert "authentication" in warnings[0]


def test_listen_binds_and_claims_the_address_it_is_given() -> None:
    sock = _REAL_LISTEN("127.0.0.1", 0)
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1"
        # Claimed, not merely bound: a second socket cannot take the port.
        with pytest.raises(OSError), contextlib.closing(_REAL_LISTEN("127.0.0.1", port)):
            pass
    finally:
        sock.close()


def test_a_port_already_in_use_is_refused_before_anything_is_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most common startup failure: another server on the port. uvicorn
    binding it would report that in several log lines, exit 3, and only
    after the database exists."""
    _refuse_to_serve(monkeypatch)
    database_dir = tmp_path / "db"
    with contextlib.closing(socket.socket()) as occupant:
        occupant.bind(("127.0.0.1", 0))
        occupant.listen()
        port = occupant.getsockname()[1]

        err = _refusal(
            capsys,
            ["--database", str(database_dir / "v.db"), "--host", "127.0.0.1", "--port", str(port)],
        )

    assert f"port {port}" in err
    assert not database_dir.exists()


@pytest.mark.usefixtures("never_served")
def test_main_needs_no_home_directory_when_the_database_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A container run as a uid with no passwd entry and no `HOME` has no
    home directory at all; only the default database path needs one."""

    def _no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr("vantage.service.cli.Path.home", _no_home)
    monkeypatch.delenv("VANTAGE_DATABASE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    err = _refusal(capsys, [])
    assert "--database" in err

    database = tmp_path / "v.db"
    monkeypatch.setattr("vantage.service.cli.uvicorn.Server.run", lambda *_a, **_k: None)
    cli.main(["--database", str(database)])
    assert database.exists()


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


@pytest.mark.usefixtures("listened")
@pytest.mark.parametrize("stops_with", [None, SystemExit(3)], ids=["returns", "exits"])
def test_main_closes_the_store_when_the_server_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stops_with: SystemExit | None
) -> None:
    """uvicorn exits with status 3 when the app fails to start, so the store
    must be closed on that path as well as on a normal shutdown."""
    served: dict[str, Any] = {}

    def _run(server: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        app: Any = server.config.app
        served["store"] = app.state.store
        if stops_with is not None:
            raise stops_with

    monkeypatch.setattr("vantage.service.cli.uvicorn.Server.run", _run)

    with contextlib.suppress(SystemExit):
        cli.main(["--database", str(tmp_path / "v.db")])

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        served["store"].count_executions()


def _free_loopback_port() -> int:
    with contextlib.closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_until_serving(proc: subprocess.Popen[bytes], base: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert proc.poll() is None, proc.communicate()[1]
        try:
            with urllib.request.urlopen(f"{base}/capabilities", timeout=1):  # noqa: S310
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    raise AssertionError("the server never answered")


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is how POSIX service managers stop one")
def test_a_server_stopped_with_sigterm_leaves_everything_in_the_database_file(
    tmp_path: Path,
) -> None:
    """systemd, docker and kubernetes stop a service with SIGTERM. uvicorn
    ends the process by raising it again once the app has shut down, so a
    store closed only after the server returns stays open, and every write
    stays in the `-wal` file beside the database: a backup that copies the
    database file alone after the stop gets no tables at all."""
    database = tmp_path / "db" / "v.db"
    port = _free_loopback_port()
    base = f"http://127.0.0.1:{port}/api/v1"
    command = "from vantage.service.cli import main; main()"
    proc = subprocess.Popen(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", command, "--database", str(database), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_until_serving(proc, base)
        report = {
            "run": {
                "id": "a" * 32,
                "started_at": "2026-08-15T09:14:02.481930+00:00",
                "finished_at": None,
                "exit_status": None,
                "interrupted": False,
                "interrupt_reason": None,
            }
        }
        request = urllib.request.Request(  # noqa: S310
            f"{base}/runs",
            data=json.dumps(report).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            assert response.status == 201

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        assert proc.stderr is not None
        proc.stderr.close()

    assert sorted(path.name for path in database.parent.iterdir()) == ["v.db"]
    backup = tmp_path / "backup.db"
    backup.write_bytes(database.read_bytes())
    with contextlib.closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("SELECT id FROM run").fetchall() == [("a" * 32,)]


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
