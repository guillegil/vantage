"""`vantage` startup: every refusal is one `vantage: ...` line and exit
status 1, a bad setting is refused before anything is created, and the
store is closed once the server stops. Also the pieces `main` composes: the
writable-directory check, the wide-bind warning, and the grace period
`create_app` builds.

`uvicorn.Server.run` is replaced in every test that calls `main` in
process -- these tests are about what `main` does around serving -- and so
is `_listen`, by a stand-in that records the address asked for and binds an
ephemeral loopback port instead, so no test holds 8765 or a wide address.
Both are patched by dotted path, `uvicorn.Server.run` on uvicorn itself,
since `cli.py` imports uvicorn only once it serves. Some tests run the real
command in a subprocess: to stop it the way a service manager does, and to
run it where the `server` extra's modules cannot be imported.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import logging
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
import pytest
import uvicorn
from memory_store import InMemoryExecutionStore
from vantage.core.config.database import redacted
from vantage.service import cli
from vantage.service.app import create_app
from vantage.service.cli import (
    DatabaseDirectoryNotWritableError,
    ensure_database_directory_writable,
    warn_if_bound_wide,
)
from vantage.storage.postgres import PostgresExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage.storage.version import _SCHEMA_VERSION, SchemaVersionError

# `postgres_url` and `create_postgres_database`, for the tests of the real
# PostgreSQL adapter.
pytest_plugins = ["postgres_fixtures"]

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

    monkeypatch.setattr("uvicorn.Server.run", _run)
    return served


def _refuse_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("main started serving a configuration it should refuse")

    monkeypatch.setattr("uvicorn.Server.run", _run)


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
def test_a_database_url_of_another_scheme_is_refused_and_creates_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    err = _refusal(capsys, ["--database", "postgresql+psycopg://vantage:s3cret@db/vantage"])

    assert err.startswith("vantage: --database: a postgresql+psycopg:// URL")
    assert "s3cret" not in err
    assert list(tmp_path.iterdir()) == []


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
    assert "authenticates" in warnings[0]


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
    monkeypatch.setattr("uvicorn.Server.run", lambda *_a, **_k: None)
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

    monkeypatch.setattr("uvicorn.Server.run", _run)

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


@contextlib.contextmanager
def _running_vantage(database: Path | str) -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    """The real `vantage` command, serving `database` -- a SQLite path or a
    PostgreSQL URL -- on a free loopback port, and the base URL of its API.
    Killed on the way out if it is still running."""
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
        yield proc, base
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        assert proc.stderr is not None
        proc.stderr.close()


def _post_json(url: str, body: object) -> int:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return int(response.status)


def _start_report(run_id: str) -> dict[str, Any]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-08-15T09:14:02.481930+00:00",
            "finished_at": None,
            "exit_status": None,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }


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
    with _running_vantage(database) as (proc, base):
        assert _post_json(f"{base}/runs", _start_report("a" * 32)) == 201

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)

    assert sorted(path.name for path in database.parent.iterdir()) == ["v.db"]
    backup = tmp_path / "backup.db"
    backup.write_bytes(database.read_bytes())
    with contextlib.closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("SELECT id FROM run").fetchall() == [("a" * 32,)]


@pytest.mark.parametrize(
    "database", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]
)
def test_a_result_with_a_long_node_id_can_be_read_back_by_it(
    tmp_path: Path, request: pytest.FixtureRequest, database: str
) -> None:
    """A result is read by its node id, in the query string, and pytest
    never shortens a parametrize id. Percent-encoded, this one is over half
    a megabyte of request line, which the HTTP parser's own 16 KiB bound
    refused before any route saw it, while `/results` listed it. In
    PostgreSQL it is also far past what one index entry can hold."""
    node_id = "tests/" + "é/" * 60_000 + "test_a.py::test_x"
    result = {
        "node_id": node_id,
        "file_path": node_id.partition("::")[0],
        "class_name": None,
        "function_name": "test_x",
        "param_id": None,
        "outcome": "passed",
        "duration": None,
        "started_at": None,
        "finished_at": None,
        "setup_outcome": None,
        "call_outcome": None,
        "teardown_outcome": None,
        "setup_duration": None,
        "call_duration": None,
        "teardown_duration": None,
        "worker_id": None,
    }
    run_id = "b" * 32
    target = tmp_path / "v.db" if database == "sqlite" else request.getfixturevalue("postgres_url")
    with _running_vantage(target) as (_proc, base):
        assert _post_json(f"{base}/runs", {**_start_report(run_id), "results": [result]}) == 201
        query = urllib.parse.urlencode({"node_id": node_id})
        assert len(query) > 500_000

        with urllib.request.urlopen(f"{base}/runs/{run_id}/result?{query}", timeout=10) as got:  # noqa: S310
            assert json.loads(got.read())["node_id"] == node_id
        with urllib.request.urlopen(f"{base}/tests/history?{query}", timeout=10) as got:  # noqa: S310
            assert [item["run_id"] for item in json.loads(got.read())["items"]] == [run_id]


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
        warn_if_bound_wide("127.0.0.1", access_required=False)

    assert caplog.records == []


def test_non_loopback_host_warns_naming_missing_authentication(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Binding wider than loopback, with no user to require a token of,
    warns and names what is missing and how to add it."""
    with caplog.at_level(logging.WARNING):
        warn_if_bound_wide("0.0.0.0", access_required=False)  # noqa: S104

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("authenticates" in message for message in messages)
    assert any("vantage user add" in message for message in messages)
    assert any("0.0.0.0" in message for message in messages)  # noqa: S104


def test_non_loopback_host_with_users_emits_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A database with a user makes the server require a token, so there is
    nothing missing to warn about."""
    with caplog.at_level(logging.WARNING):
        warn_if_bound_wide("0.0.0.0", access_required=True)  # noqa: S104

    assert caplog.records == []


def test_a_wide_start_on_a_database_with_users_does_not_warn(
    tmp_path: Path,
    served: dict[str, Any],
    listened: list[tuple[str, int, socket.socket]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "v.db"
    store = SqliteExecutionStore(database)
    store.create_user("alice", admin=True, created_at=datetime.now(timezone.utc))
    store.close()

    with caplog.at_level(logging.WARNING, logger=cli.__name__):
        cli.main(["--database", str(database), "--host", "0.0.0.0"])  # noqa: S104

    assert served["sockets"]
    assert [r.getMessage() for r in caplog.records if r.name == cli.__name__] == []


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


# --- The server extra ---------------------------------------------------------
#
# FastAPI, Starlette and uvicorn come with the `server` extra. These run the
# command in an interpreter where the named modules cannot be imported, as
# in an install of `vantage` without the extra.

_WITHOUT = """
import sys

for name in sys.argv[1].split(","):
    sys.modules[name] = None
del sys.argv[1]

from vantage.service.cli import main

main()
"""
_SERVER_EXTRA_MISSING = "vantage: serving needs the server extra: pip install 'vantage[server]'\n"


def _run_without(
    modules: Sequence[str], argv: Sequence[str], *, home: Path
) -> subprocess.CompletedProcess[str]:
    """The command, with `modules` unimportable and `home` as the home
    directory, so a default database would land there."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"VANTAGE_DATABASE", "XDG_DATA_HOME"}
    }
    return subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", _WITHOUT, ",".join(modules), *argv],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**environment, "HOME": str(home)},
    )


@pytest.mark.parametrize(
    "missing",
    [["fastapi", "starlette", "uvicorn"], ["uvicorn"], ["fastapi"], ["starlette"]],
    ids=["all", "uvicorn", "fastapi", "starlette"],
)
def test_serving_without_the_server_extra_is_one_line_naming_it(
    tmp_path: Path, missing: list[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()

    completed = _run_without(missing, [], home=home)

    assert completed.returncode == 1, completed.stderr
    assert completed.stderr == _SERVER_EXTRA_MISSING
    assert list(home.iterdir()) == []


def test_the_server_extra_is_checked_before_anything_is_bound_or_created(tmp_path: Path) -> None:
    """Named settings, even unusable ones, are not looked at first: the
    missing extra is what there is to fix."""
    database_dir = tmp_path / "db"
    port = _free_loopback_port()

    completed = _run_without(
        ["uvicorn"],
        ["--database", str(database_dir / "v.db"), "--port", str(port), "--grace-period", "0"],
        home=tmp_path,
    )

    assert completed.stderr == _SERVER_EXTRA_MISSING
    assert not database_dir.exists()
    with contextlib.closing(_REAL_LISTEN("127.0.0.1", port)):
        pass


def test_help_needs_no_server_extra(tmp_path: Path) -> None:
    completed = _run_without(["fastapi", "starlette", "uvicorn"], ["--help"], home=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage: vantage")


@pytest.mark.usefixtures("never_served")
def test_an_import_failure_that_is_not_the_server_extra_is_not_blamed_on_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken installation of the service itself is a fault to see in
    full, not advice to install what is already there."""
    app = "vantage.service.app"

    class _BrokenApp(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(
            self,
            fullname: str,
            path: Sequence[str] | None,
            target: types.ModuleType | None = None,
        ) -> importlib.machinery.ModuleSpec | None:
            return importlib.util.spec_from_loader(fullname, self) if fullname == app else None

        def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
            return None

        def exec_module(self, module: types.ModuleType) -> None:
            raise ImportError("cannot import name 'Gone'", name="vantage.service.routes")

    monkeypatch.delitem(sys.modules, app)
    monkeypatch.setattr(sys, "meta_path", [_BrokenApp(), *sys.meta_path])

    with pytest.raises(ImportError, match="Gone"):
        cli.main(["--database", str(tmp_path / "v.db")])


# --- PostgreSQL -----------------------------------------------------------------
#
# `main` imports `vantage.storage.postgres` by name, only for a PostgreSQL URL.
# These tests put a stand-in module under that name, so they check what
# `main` does with the adapter's constructor and its failures, not the
# adapter itself.

_ADAPTER = "vantage.storage.postgres"
# Made up, to look for in what `main` prints; percent-encoded, as libpq
# requires of a `?`.
_PASSWORD = "s3cr%3Ft"  # noqa: S105
_DECODED_PASSWORD = "s3cr?t"  # noqa: S105
_URL = f"postgresql://vantage:{_PASSWORD}@db.example:5432/vantage"
_SHOWN = "postgresql://vantage:***@db.example:5432/vantage"
# The loggers of psycopg and of its pool, which `main` silences.
_DRIVER_LOGGERS = ("psycopg", "psycopg_pool")


class _StandInAdapter:
    """What `main` finds as `vantage.storage.postgres`: its
    `PostgresExecutionStore` records each URL it is given, runs
    `during_open`, which may raise, and opens an in-memory store."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.stores: list[InMemoryExecutionStore] = []
        self.during_open: Callable[[str], None] | None = None

    def open(self, url: str, *, max_connections: int = 10) -> InMemoryExecutionStore:
        self.urls.append(url)
        if self.during_open is not None:
            self.during_open(url)
        store = InMemoryExecutionStore()
        self.stores.append(store)
        return store


@pytest.fixture
def postgres_adapter(monkeypatch: pytest.MonkeyPatch) -> Iterator[_StandInAdapter]:
    """The stand-in adapter; the driver's loggers, which a refused start
    leaves silenced, are put back afterwards."""
    adapter = _StandInAdapter()
    module = types.ModuleType(_ADAPTER)
    setattr(module, "PostgresExecutionStore", adapter.open)
    monkeypatch.setitem(sys.modules, _ADAPTER, module)
    levels = {name: logging.getLogger(name).level for name in _DRIVER_LOGGERS}
    yield adapter
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def test_a_postgresql_url_opens_the_postgresql_adapter_and_serves_it(
    served: dict[str, Any], postgres_adapter: _StandInAdapter
) -> None:
    cli.main(["--database", _URL])

    assert postgres_adapter.urls == [_URL]
    assert served["app"].state.store is postgres_adapter.stores[0]


def test_vantage_database_can_name_a_postgresql_database(
    monkeypatch: pytest.MonkeyPatch, served: dict[str, Any], postgres_adapter: _StandInAdapter
) -> None:
    monkeypatch.setenv("VANTAGE_DATABASE", _URL)

    cli.main([])

    assert postgres_adapter.urls == [_URL]


@pytest.mark.usefixtures("served", "postgres_adapter")
def test_a_postgresql_start_creates_nothing_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Taken for a path, the URL would become directories under the
    working directory, beginning with `postgresql:`."""
    monkeypatch.chdir(tmp_path)

    cli.main(["--database", _URL])

    assert list(tmp_path.iterdir()) == []


class _FailingImport(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds `vantage.storage.postgres` before anything else does, and fails
    to import it the way `fail` does."""

    def __init__(self, fail: Callable[[], object]) -> None:
        self._fail = fail

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        return importlib.util.spec_from_loader(fullname, self) if fullname == _ADAPTER else None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        self._fail()


def _adapter_import_fails(monkeypatch: pytest.MonkeyPatch, fail: Callable[[], object]) -> None:
    monkeypatch.delitem(sys.modules, _ADAPTER, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_FailingImport(fail), *sys.meta_path])


def _raise_in_psycopg() -> None:
    """How psycopg without a libpq fails: a plain `ImportError` raised in
    its own `psycopg.pq` module."""
    code = compile('raise ImportError("no pq wrapper available.")', "psycopg/pq.py", "exec")
    exec(code, {"__name__": "psycopg.pq"})  # noqa: S102


@pytest.mark.usefixtures("never_served")
@pytest.mark.parametrize("driver", ["psycopg", "psycopg_pool", "libpq"])
def test_a_missing_driver_is_one_line_naming_the_extra(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, driver: str
) -> None:
    if driver == "libpq":
        _adapter_import_fails(monkeypatch, _raise_in_psycopg)
    else:
        monkeypatch.setitem(sys.modules, driver, None)
        _adapter_import_fails(monkeypatch, lambda: importlib.import_module(driver))

    err = _refusal(capsys, ["--database", _URL])

    assert err == "vantage: PostgreSQL needs the postgres extra: pip install 'vantage[postgres]'\n"


@pytest.mark.usefixtures("never_served")
def test_an_import_failure_that_is_not_the_driver_is_not_blamed_on_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken installation of the adapter itself is a fault to see in
    full, not advice to install what is already there."""

    def _fail() -> None:
        raise ImportError("cannot import name 'Gone'", name="vantage.storage.version")

    _adapter_import_fails(monkeypatch, _fail)

    with pytest.raises(ImportError, match="Gone"):
        cli.main(["--database", _URL])


def _raising(exc: Exception) -> Callable[[str], None]:
    def _fail(url: str) -> None:
        raise exc

    return _fail


_REFUSALS = {
    "connection": (
        RuntimeError(f"connection to {_URL} failed:\n\tpassword {_DECODED_PASSWORD!r} refused"),
        f"vantage: cannot open the database at {_SHOWN}: connection to {_SHOWN} failed: "
        "password '***' refused\n",
    ),
    "schema-version": (
        SchemaVersionError("schema_version is 5, but this build requires schema_version 6"),
        f"vantage: {_SHOWN}: schema_version is 5, but this build requires schema_version 6\n",
    ),
    "schema-version-naming-the-url": (
        SchemaVersionError(f"{_SHOWN} holds tables but no schema_version stamp"),
        f"vantage: {_SHOWN} holds tables but no schema_version stamp\n",
    ),
    "no-message": (
        RuntimeError(),
        f"vantage: cannot open the database at {_SHOWN}: RuntimeError\n",
    ),
}


@pytest.mark.usefixtures("never_served")
@pytest.mark.parametrize(("failure", "line"), _REFUSALS.values(), ids=_REFUSALS)
def test_a_database_that_cannot_be_used_is_one_line_naming_the_redacted_url(
    capsys: pytest.CaptureFixture[str],
    postgres_adapter: _StandInAdapter,
    failure: Exception,
    line: str,
) -> None:
    postgres_adapter.during_open = _raising(failure)

    assert _refusal(capsys, ["--database", _URL]) == line


def _connect(url: str) -> None:
    """The real driver's failure, before any store would be built."""
    psycopg.connect(url, connect_timeout=5).close()
    raise AssertionError(f"something answered at {url}")


_DRIVER_FAILURES = {
    "bad-escape": ("s3cr%zzt", "percent-encoded"),
    # libpq takes the password to end at the first `@`, and quotes it alone.
    "bad-escape-before-an-at": ("s3cr%zzt@h0st", "percent-encoded"),
    # ... and the rest for a host, which it cuts at the first `:`.
    "at-and-colon": ("s3cr@h0st:p0rt", "resolve host"),
    "refused": ("s3cret", "Connection refused"),
}


@pytest.mark.usefixtures("never_served")
@pytest.mark.parametrize(("password", "said"), _DRIVER_FAILURES.values(), ids=_DRIVER_FAILURES)
def test_the_drivers_own_message_is_quoted_on_one_line_without_the_password(
    capsys: pytest.CaptureFixture[str], postgres_adapter: _StandInAdapter, password: str, said: str
) -> None:
    """libpq quotes a percent-escape it cannot decode, password and all,
    splits a password holding an unencoded `@` into fields it names one at
    a time, and spreads a refused connection over two lines."""
    port = _free_loopback_port()
    url = f"postgresql://vantage:{password}@127.0.0.1:{port}/vantage"
    postgres_adapter.during_open = _connect

    err = _refusal(capsys, ["--database", url])

    assert err.startswith(
        f"vantage: cannot open the database at postgresql://vantage:***@127.0.0.1:{port}/vantage: "
    )
    for piece in re.split("[@:]", password):
        assert piece not in err
    assert said in err


_OPENED_BY_A_POOL = """
import sys
import types

from psycopg_pool import ConnectionPool
from vantage.service import cli


def PostgresExecutionStore(url, *, max_connections=10):
    pool = ConnectionPool(url, min_size=1, max_size=max_connections, open=False)
    pool.open(wait=True, timeout=1.0)
    return pool


adapter = types.ModuleType("vantage.storage.postgres")
adapter.PostgresExecutionStore = PostgresExecutionStore
sys.modules[adapter.__name__] = adapter
cli.main(sys.argv[1:])
"""


def test_the_pools_own_logging_adds_nothing_to_a_refusal() -> None:
    """psycopg's pool logs every failed attempt to connect, and with no
    logging configured yet Python prints that on stderr, quoting libpq --
    here, the password it cannot decode. A real pool, run where nothing
    else configures logging, as the command runs."""
    port = _free_loopback_port()
    password = "s3cr%zzt"  # noqa: S105
    url = f"postgresql://vantage:{password}@127.0.0.1:{port}/vantage"
    listen = ["--port", str(_free_loopback_port())]

    completed = subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", _OPENED_BY_A_POOL, "--database", url, *listen],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    assert completed.stderr.startswith(
        f"vantage: cannot open the database at postgresql://vantage:***@127.0.0.1:{port}/vantage: "
    )
    assert completed.stderr.count("\n") == 1, completed.stderr
    assert password not in completed.stderr


def test_a_start_that_goes_on_hears_from_the_driver_again(
    caplog: pytest.LogCaptureFixture, served: dict[str, Any], postgres_adapter: _StandInAdapter
) -> None:
    """Silenced only while the store opens: a connection the pool loses
    later is still reported."""

    def _warn(url: str) -> None:
        logging.getLogger("psycopg.pool").warning("while opening")

    postgres_adapter.during_open = _warn

    cli.main(["--database", _URL])
    logging.getLogger("psycopg.pool").warning("while serving")

    assert [r.getMessage() for r in caplog.records if r.name.startswith("psycopg")] == [
        "while serving"
    ]


# --- PostgreSQL, the real adapter -------------------------------------------
#
# The command itself, in a process of its own as a service manager runs it,
# against a database on the server `VANTAGE_TEST_POSTGRES_URL` names.


def _refused(database: str) -> str:
    """The one line the real command prints refusing `database`, having
    exited 1 without serving."""
    command = "from vantage.service.cli import main; main()"
    listen = ["--port", str(_free_loopback_port())]
    completed = subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", command, "--database", database, *listen],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 1, completed.stderr
    assert completed.stderr.count("\n") == 1, completed.stderr
    return completed.stderr


def _password(url: str) -> str | None:
    return urllib.parse.unquote(urllib.parse.urlsplit(url).password or "") or None


def _with_password(url: str, password: str) -> str:
    parts = urllib.parse.urlsplit(url)
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
    user = parts.username or "postgres"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{user}:{urllib.parse.quote(password, safe='')}@{host}{port}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def _execute(url: str, statement: str, params: Sequence[object] | None = None) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(statement, params)


def _database_connections(admin_url: str, url: str) -> int:
    database = urllib.parse.urlsplit(url).path.lstrip("/")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (database,)
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.postgres
@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is how POSIX service managers stop one")
def test_a_server_on_postgresql_keeps_what_it_stored_and_hangs_up_when_stopped(
    postgres_url: str, postgres_admin_url: str
) -> None:
    run_id = "c" * 32
    with _running_vantage(postgres_url) as (proc, base):
        assert _post_json(f"{base}/runs", _start_report(run_id)) == 201
        with urllib.request.urlopen(f"{base}/runs/{run_id}", timeout=10) as got:  # noqa: S310
            assert json.loads(got.read())["id"] == run_id

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
        assert proc.stderr is not None
        logged = proc.stderr.read().decode()

    password = _password(postgres_url)
    assert password is None or password not in logged
    # A backend leaves pg_stat_activity a moment after its client hangs up.
    deadline = time.monotonic() + 10
    while _database_connections(postgres_admin_url, postgres_url):
        assert time.monotonic() < deadline, "the stopped server left connections open"
        time.sleep(0.05)
    store = PostgresExecutionStore(postgres_url)
    try:
        assert store.get_execution(run_id) is not None
    finally:
        store.close()


def _foreign_schema(url: str) -> str:
    _execute(url, "CREATE SCHEMA vantage; CREATE TABLE vantage.things (id int)")
    return "the vantage schema holds objects but no schema_version stamp"


def _other_version(url: str) -> str:
    PostgresExecutionStore(url).close()
    other = _SCHEMA_VERSION - 1
    _execute(url, "UPDATE vantage.meta SET value = %s WHERE key = 'schema_version'", [str(other)])
    return f"the vantage schema's schema_version is {other}, but this build requires"


@pytest.mark.postgres
@pytest.mark.parametrize("make", [_foreign_schema, _other_version], ids=["foreign", "version"])
def test_a_postgresql_schema_this_build_cannot_use_is_one_line_naming_the_redacted_url(
    postgres_url: str, make: Callable[[str], str]
) -> None:
    said = make(postgres_url)

    line = _refused(postgres_url)

    assert line.startswith(f"vantage: {redacted(postgres_url)}: {said}"), line
    password = _password(postgres_url)
    assert password is None or password not in line


@pytest.mark.postgres
def test_a_postgresql_login_refused_is_one_line_without_the_password(postgres_url: str) -> None:
    wrong = "wr0ng-s3cr3t"  # noqa: S105
    url = _with_password(postgres_url, wrong)
    try:
        psycopg.connect(url, connect_timeout=5).close()
    except psycopg.OperationalError:
        pass
    else:
        pytest.skip("the server lets this role in without checking its password")

    line = _refused(url)

    assert line.startswith(f"vantage: cannot open the database at {redacted(url)}: "), line
    assert "password authentication failed" in line
    assert wrong not in line


@pytest.mark.postgres
def test_a_postgresql_database_not_in_utf8_is_one_line_naming_the_redacted_url(
    create_postgres_database: Callable[..., str],
) -> None:
    url = create_postgres_database(encoding="LATIN1")

    assert _refused(url) == (
        f"vantage: cannot open the database at {redacted(url)}: "
        "the database's encoding is LATIN1; vantage needs a UTF8 database\n"
    )


def test_an_unreachable_postgresql_server_is_one_line_without_the_password() -> None:
    # Bound but never listening, and held for the whole test: every connection
    # is refused at once, and no other process can take the port meanwhile
    # and answer slowly, which reads as a timeout instead.
    with contextlib.closing(socket.socket()) as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        url = f"postgresql://vantage:{_PASSWORD}@127.0.0.1:{port}/vantage"

        line = _refused(url)

    assert line.startswith(f"vantage: cannot open the database at {redacted(url)}: "), line
    assert "Connection refused" in line
    assert _PASSWORD not in line
    assert _DECODED_PASSWORD not in line
