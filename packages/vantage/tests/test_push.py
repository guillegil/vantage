"""`vantage push`: sends the runs the plugin queued, per server, with the
plugin's own outbox and sender, prints one line per server, and exits 0
only when nothing is left waiting for the servers it sent to. It must run
where the `server` extra is not installed.

The outbox and its sender are the plugin's (`pytest_vantage.outbox`); here
a stand-in takes that name in `sys.modules`, with the same interface, so
these tests are about what the command does with it: which queue it opens,
which servers it sends to, with what deadline, and what it says.
"""

from __future__ import annotations

import math
import subprocess
import sys
import textwrap
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import vantage.service
from vantage.service import cli

_OUTBOX_MODULE = "pytest_vantage.outbox"
_PUSH_MODULE = "vantage.service.push"


@dataclass(frozen=True)
class _SendSummary:
    server: str
    sent: int
    dropped: tuple[str, ...]
    waiting: int
    stopped: str | None
    unreadable: tuple[str, ...] = ()


@dataclass
class _Queue:
    """What the stand-in outbox holds and what was asked of it."""

    entries: dict[str, list[str]] = field(default_factory=dict)
    unreachable: set[str] = field(default_factory=set)
    refused: set[str] = field(default_factory=set)
    damaged: set[str] = field(default_factory=set)
    failing: dict[str, BaseException] = field(default_factory=dict)
    fails_to_open: Exception | None = None
    databases: list[Path] = field(default_factory=list)
    opened: list[Path] = field(default_factory=list)
    closed: int = 0
    sends: list[tuple[str, float, float]] = field(default_factory=list)

    def module(self) -> types.ModuleType:
        queue = self

        class Outbox:
            def __init__(self, path: Path) -> None:
                if queue.fails_to_open is not None:
                    raise queue.fails_to_open
                queue.opened.append(path)

            def waiting(self, server: str | None = None) -> int:
                if server is None:
                    return sum(map(len, queue.entries.values()))
                return len(queue.entries.get(server, []))

            def servers(self) -> tuple[str, ...]:
                return tuple(server for server, runs in queue.entries.items() if runs)

            def close(self) -> None:
                queue.closed += 1

        def outbox_path(database: Path) -> Path:
            queue.databases.append(database)
            return database.with_name(database.name + "-outbox")

        def send_queued(
            outbox: Outbox, server: str, *, timeout: float, budget: float
        ) -> _SendSummary:
            queue.sends.append((server, timeout, budget))
            if server in queue.failing:
                raise queue.failing[server]
            runs = queue.entries.setdefault(server, [])
            if server in queue.unreachable:
                return _SendSummary(server, 0, (), len(runs), f"{server} is unreachable")
            sent = [run for run in runs if run not in queue.refused | queue.damaged]
            dropped = tuple(run for run in runs if run in queue.refused)
            unreadable = tuple(run for run in runs if run in queue.damaged)
            runs.clear()
            return _SendSummary(server, len(sent), dropped, 0, None, unreadable)

        module = types.ModuleType(_OUTBOX_MODULE)
        module.Outbox = Outbox  # type: ignore[attr-defined]
        module.SendSummary = _SendSummary  # type: ignore[attr-defined]
        module.outbox_path = outbox_path  # type: ignore[attr-defined]
        module.send_queued = send_queued  # type: ignore[attr-defined]
        return module


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[_Queue]:
    """The stand-in outbox, and a home directory under `tmp_path`, so the
    default database is there. `vantage.service.push` binds the outbox's
    names when it is imported, so it is imported afresh against the
    stand-in and forgotten afterwards."""
    queue = _Queue()
    monkeypatch.setitem(sys.modules, _OUTBOX_MODULE, queue.module())
    monkeypatch.delitem(sys.modules, _PUSH_MODULE, raising=False)
    monkeypatch.delattr(vantage.service, "push", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    yield queue
    sys.modules.pop(_PUSH_MODULE, None)
    if hasattr(vantage.service, "push"):
        delattr(vantage.service, "push")


def _default_database(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".local" / "share" / "vantage" / "vantage.db"


def _queued(queue: _Queue, database: Path, **entries: list[str]) -> None:
    """`entries`, by server, in an outbox that exists beside `database`."""
    database.parent.mkdir(parents=True, exist_ok=True)
    database.with_name(database.name + "-outbox").touch()
    queue.entries.update({f"http://{name}:8765": runs for name, runs in entries.items()})


def _push(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, list[str], str]:
    """Exit status, the lines printed, and what went to stderr."""
    with pytest.raises(SystemExit) as exited:
        cli.main(["push", *argv])
    captured = capsys.readouterr()
    code = exited.value.code
    assert isinstance(code, int)
    return code, captured.out.splitlines(), captured.err


def test_every_queue_is_sent_to_its_own_server_with_one_line_each(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = _default_database(tmp_path)
    _queued(queue, database, alpha=["r1", "r2"], beta=["r3"])

    code, lines, err = _push(capsys)

    assert (code, err) == (0, "")
    assert lines == [
        "vantage: sent 2 queued runs to http://alpha:8765 (0 waiting)",
        "vantage: sent 1 queued run to http://beta:8765 (0 waiting)",
    ]
    assert queue.databases == [database]
    assert queue.opened == [database.with_name("vantage.db-outbox")]
    assert queue.closed == 1


def test_each_run_gets_the_timeout_and_the_queue_no_budget_beyond_it(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1"])

    _push(capsys)
    _queued(queue, _default_database(tmp_path), alpha=["r2"])
    _push(capsys, "--timeout", "2.5")

    assert queue.sends == [
        ("http://alpha:8765", 10.0, math.inf),
        ("http://alpha:8765", 2.5, math.inf),
    ]


def test_to_sends_only_the_runs_queued_for_that_address(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1"], beta=["r2", "r3"])

    code, lines, _err = _push(capsys, "--to", "http://beta:8765")

    assert code == 0
    assert lines == ["vantage: sent 2 queued runs to http://beta:8765 (0 waiting)"]
    assert queue.entries["http://alpha:8765"] == ["r1"]


def test_to_an_address_nothing_is_queued_for_sends_nothing(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Addresses compare exactly, as configured: a trailing slash is
    another server."""
    _queued(queue, _default_database(tmp_path), alpha=["r1"])

    code, lines, _err = _push(capsys, "--to", "http://alpha:8765/")

    assert code == 0
    assert lines == ["vantage: nothing queued for http://alpha:8765/"]
    assert queue.sends == []


def test_a_server_still_unreachable_leaves_its_queue_and_exits_1(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1", "r2"], beta=["r3"])
    queue.unreachable.add("http://alpha:8765")

    code, lines, _err = _push(capsys)

    assert code == 1
    assert lines == [
        "vantage: sent 0 queued runs to http://alpha:8765, "
        "then stopped: http://alpha:8765 is unreachable (2 waiting)",
        "vantage: sent 1 queued run to http://beta:8765 (0 waiting)",
    ]


def test_runs_the_server_refused_are_named_and_not_waiting(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1", "r2", "r3"])
    queue.refused.update({"r1", "r3"})

    code, lines, _err = _push(capsys)

    assert code == 0
    assert lines == [
        "vantage: sent 1 queued run to http://alpha:8765, dropped 2 it refused (r1, r3) (0 waiting)"
    ]


def test_runs_that_could_not_be_read_back_are_named_and_not_waiting(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1", "r2", "r3"])
    queue.refused.add("r1")
    queue.damaged.add("r2")

    code, lines, _err = _push(capsys)

    assert code == 0
    assert lines == [
        "vantage: sent 1 queued run to http://alpha:8765, dropped 1 it refused (r1), "
        "dropped 1 that could not be read back (r2) (0 waiting)"
    ]


def test_only_the_targeted_server_decides_the_exit_status(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1"], beta=["r2"])
    queue.unreachable.add("http://alpha:8765")

    code, _lines, _err = _push(capsys, "--to", "http://beta:8765")

    assert code == 0


def test_a_sender_that_fails_costs_its_server_one_line_and_not_the_rest(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1", "r2"], beta=["r3"])
    queue.failing["http://alpha:8765"] = OSError("disk I/O error\nwhile claiming")

    code, lines, err = _push(capsys)

    assert code == 1
    assert err == ""
    assert lines == [
        "vantage: could not send the runs queued for http://alpha:8765: "
        "disk I/O error while claiming (2 waiting)",
        "vantage: sent 1 queued run to http://beta:8765 (0 waiting)",
    ]
    assert queue.closed == 1


def test_ctrl_c_stops_sending_in_one_line_with_the_interrupted_status(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No traceback: the run being sent is back in the queue, and what was
    sent before is already off it."""
    _queued(queue, _default_database(tmp_path), alpha=["r1"], beta=["r2"], gamma=["r3"])
    queue.failing["http://beta:8765"] = KeyboardInterrupt()

    code, lines, err = _push(capsys)

    assert code == 130
    assert lines == ["vantage: sent 1 queued run to http://alpha:8765 (0 waiting)"]
    assert err == "vantage: interrupted; the runs not sent are still queued\n"
    assert [server for server, _timeout, _budget in queue.sends] == [
        "http://alpha:8765",
        "http://beta:8765",
    ]
    assert queue.closed == 1


def test_database_names_the_database_the_queue_sits_beside(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "elsewhere" / "runs.db"
    _queued(queue, database, alpha=["r1"])

    code, _lines, _err = _push(capsys, "--database", str(database))

    assert code == 0
    assert queue.opened == [tmp_path / "elsewhere" / "runs.db-outbox"]


def test_the_default_database_follows_xdg_data_home(
    queue: _Queue,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _queued(queue, tmp_path / "xdg" / "vantage" / "vantage.db", alpha=["r1"])

    _push(capsys)

    assert queue.databases == [tmp_path / "xdg" / "vantage" / "vantage.db"]


def test_no_queue_file_is_nothing_queued_and_creates_nothing(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines, _err = _push(capsys)

    outbox = _default_database(tmp_path).with_name("vantage.db-outbox")
    assert code == 0
    assert lines == [f"vantage: nothing queued (no queue at {outbox})"]
    assert queue.opened == []
    assert not (tmp_path / "home").exists()


def test_an_empty_queue_is_nothing_queued(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path))

    code, lines, _err = _push(capsys)

    assert (code, lines) == (0, ["vantage: nothing queued"])
    assert queue.closed == 1


def _refusal(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    code, lines, err = _push(capsys, *argv)
    assert code == 1
    assert lines == []
    assert err.startswith("vantage: ")
    assert err.count("\n") == 1, err
    return err


def test_a_queue_that_cannot_be_opened_is_one_line(
    queue: _Queue, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _queued(queue, _default_database(tmp_path), alpha=["r1"])
    queue.fails_to_open = OSError("permission denied")

    err = _refusal(capsys)

    assert "permission denied" in err
    assert "vantage.db-outbox" in err


def test_a_postgresql_database_is_refused(
    queue: _Queue, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _refusal(capsys, "--database", "postgresql://vantage@db.example/vantage")

    assert "SQLite" in err
    assert queue.databases == []


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_a_timeout_that_is_not_a_positive_duration_is_refused(
    queue: _Queue, capsys: pytest.CaptureFixture[str], timeout: str
) -> None:
    err = _refusal(capsys, "--timeout", timeout)

    assert "--timeout" in err


def test_no_home_directory_asks_for_database(
    queue: _Queue, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", _no_home)

    err = _refusal(capsys)

    assert "--database" in err


@pytest.fixture
def push_imported_afresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """`vantage.service.push` imported again by the next `vantage push`, so
    what it imports is looked up again."""
    monkeypatch.delitem(sys.modules, _PUSH_MODULE, raising=False)
    monkeypatch.delattr(vantage.service, "push", raising=False)


@pytest.mark.usefixtures("push_imported_afresh")
def test_a_plugin_without_an_outbox_is_one_line_naming_the_upgrade(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pytest-vantage older than this vantage, installed beside it."""
    monkeypatch.setitem(sys.modules, _OUTBOX_MODULE, None)

    code, out, err = _push(capsys)

    assert (code, out) == (1, [])
    assert err == (
        "vantage: push sends pytest-vantage's outbox, and the pytest-vantage installed "
        "here has none: pip install --upgrade pytest-vantage\n"
    )


@pytest.mark.usefixtures("push_imported_afresh")
def test_any_other_import_failure_is_raised_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken installation, not an older plugin."""
    monkeypatch.setitem(sys.modules, "vantage.local", None)

    with pytest.raises(ImportError) as raised:
        cli.main(["push"])

    assert raised.value.name == "vantage.local"


# --- Without the server extra ----------------------------------------------------

_WITHOUT_THE_SERVER_EXTRA = textwrap.dedent(
    """
    import sys
    import types

    for name in ("fastapi", "starlette", "uvicorn"):
        sys.modules[name] = None

    # The stand-in outbox: one run queued for one server, sent when asked.
    class Summary:
        def __init__(self, server):
            self.server, self.sent, self.dropped, self.waiting, self.stopped = (
                server, 1, (), 0, None
            )

    class Outbox:
        def __init__(self, path):
            pass

        def servers(self):
            return ("http://alpha:8765",)

        def waiting(self, server=None):
            return 1

        def close(self):
            pass

    outbox = types.ModuleType("pytest_vantage.outbox")
    outbox.Outbox = Outbox
    outbox.SendSummary = Summary
    outbox.outbox_path = lambda database: database.with_name(database.name + "-outbox")
    outbox.send_queued = lambda box, server, *, timeout, budget: Summary(server)
    sys.modules[outbox.__name__] = outbox

    from vantage.service.cli import main

    try:
        main(["push", *sys.argv[1:]])
    finally:
        loaded = sorted(name for name in sys.modules if name.startswith("vantage.service."))
        print("loaded:", " ".join(loaded), file=sys.stderr)
    """
)


def _without_the_server_extra(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", _WITHOUT_THE_SERVER_EXTRA, *argv],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_push_runs_without_the_server_extra(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"
    database.with_name("vantage.db-outbox").touch()

    completed = _without_the_server_extra("--database", str(database))

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "vantage: sent 1 queued run to http://alpha:8765 (0 waiting)\n"
    # Only the command and the push itself: nothing that serves.
    assert completed.stderr == "loaded: vantage.service.cli vantage.service.push\n"


def test_push_help_needs_no_server_extra() -> None:
    completed = _without_the_server_extra("--help")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage: vantage push")
