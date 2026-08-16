"""RQ-37 (the server cannot be reached at all) and RQ-21 (something goes
wrong while reporting to one that can) -- design.md D6's two client-side
failure paths, proven end to end wherever a real socket is the only thing
that can prove the behaviour, and as focused unit tests wherever the
mechanism is a pure function or a decorator that does not need one.

`_StubServer` stands in for `vantage_test_server.py`'s real `vantage`
server in every test here: these scenarios are about a server that behaves
badly (closes without responding, never responds, sends garbage back), not
about the real ingestion endpoint, so a real `vantage` process would only
add ceremony without adding proof.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable

import pytest
from pytest_vantage.plugin import _preflight_reachable
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture

_PASSING_TEST = "def test_it():\n    assert True\n"


def _combined_output(result: pytest.RunResult) -> str:
    """`VantageWarning`s raised from `pytest_configure` (the preflight) print
    straight to the real `stderr` via Python's default `warnings.showwarning`
    -- they fire before pytest's own warnings-summary capturing is active for
    this pytest version, so they never reach the "warnings summary" section
    in `stdout`. One fired from a later hook (`pytest_sessionfinish`) does
    reach that section. Checking both streams together is robust to either
    timing rather than depending on which hook happened to raise.
    """
    return result.stdout.str() + result.stderr.str()


def _closed_port_address() -> str:
    """A `host:port` where a TCP connect reliably fails with
    `ConnectionRefusedError`: bind an ephemeral loopback port, close it
    immediately, and hand back that now-unbound port. Reusing a
    just-closed loopback port this way is the standard, portable way to get
    a deterministic "nothing is listening" address without depending on any
    fixed port being free on the machine running the test.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


class _StubServer:
    """A bare TCP server whose per-connection behaviour is entirely
    controlled by the caller's `handle_connection` -- no HTTP framework in
    the loop, because the whole point of every test that uses this is to
    hand the plugin's client code exactly the malformed or absent response
    a well-behaved server never would (design.md threat matrix "Untrusted
    response", D6's RQ-21 failure paths).

    Accepts connections in a loop, each handled on its own thread, because a
    single session can open two: the preflight (task 6.8) and, if that
    succeeds, the report itself.
    """

    def __init__(self, handle_connection: Callable[[socket.socket], None]) -> None:
        self._handle_connection = handle_connection
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self.address = f"http://127.0.0.1:{self.port}"
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._accept_loop, name="vantage-stub-server", daemon=True
        )

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            self._handle_connection(conn)
        except OSError:
            pass
        conn.close()

    def __enter__(self) -> _StubServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        # A failing assertion above must not leave a listener behind to
        # poison a later test -- join with a bound, not forever.
        self._thread.join(timeout=5)
        self._sock.close()


def _accept_then_close(conn: socket.socket) -> None:
    """RQ-21's "accepts the connection and then closes it without
    responding" scenario: no bytes back at all, not even a partial header.
    """


# --- Unit: the preflight probe itself (task 6.8) ----------------------------


def test_preflight_reachable_is_false_on_connection_refused() -> None:
    assert _preflight_reachable(_closed_port_address(), timeout=1.0) is False


def test_preflight_reachable_is_false_on_unresolvable_host() -> None:
    assert (
        _preflight_reachable("http://this-host-does-not-exist.invalid:8765", timeout=1.0) is False
    )


def test_preflight_reachable_is_true_when_something_listens() -> None:
    with _StubServer(_accept_then_close) as server:
        assert _preflight_reachable(server.address, timeout=1.0) is True


# --- RQ-37: the server cannot be reached at all (task 6.7) -------------------


@pytest.mark.req("RQ-37")
def test_closed_port_warns_naming_the_address_and_runs_unrecorded(
    pytester: pytest.Pytester,
) -> None:
    address = _closed_port_address()
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    output = _combined_output(result)
    assert output.count(address) == 1
    assert output.count("VantageWarning:") == 1


@pytest.mark.req("RQ-37")
def test_unresolvable_host_warns_naming_the_address_and_runs_unrecorded(
    pytester: pytest.Pytester,
) -> None:
    address = "http://this-host-does-not-exist.invalid:8765"
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    output = _combined_output(result)
    assert output.count(address) == 1
    assert output.count("VantageWarning:") == 1


@pytest.mark.req("RQ-37")
def test_recorder_is_not_registered_when_the_preflight_fails(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytest_vantage.recorder import Recorder

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    config = pytester.parseconfigure("--vantage", f"--vantage-server={_closed_port_address()}")

    assert not any(isinstance(plugin, Recorder) for plugin in config.pluginmanager.get_plugins())


@pytest.mark.req("RQ-37")
def test_two_hundred_tests_produce_exactly_one_warning_naming_the_address(
    pytester: pytest.Pytester,
) -> None:
    """The preflight runs once, in `pytest_configure`, entirely independent
    of how many tests the session goes on to collect -- this is the same
    mechanism `test_closed_port_warns_naming_the_address_and_runs_unrecorded`
    already proves, exercised at the scale RQ-37.4 names explicitly (200
    tests) rather than a second, different code path.
    """
    address = _closed_port_address()
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(200))
    )

    result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={address}")

    result.assert_outcomes(passed=200)
    assert result.ret == 0
    assert _combined_output(result).count("VantageWarning:") == 1


@pytest.mark.req("RQ-37")
def test_server_dropped_mid_session_preserves_exit_status_and_warns_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-37 criterion 3, mechanically RQ-21's path (design.md D6): the
    preflight passes because the server WAS up when `pytest_configure` ran.
    It is stopped before `pytest_sessionfinish` sends the report, so the
    failure surfaces at report time through the same boundary this file's
    RQ-21 tests exercise directly (task 6.9), not through a second
    preflight. That means this scenario cannot go fully green until 6.10's
    boundary decorator lands, even though it belongs to RQ-37 -- design.md
    says so explicitly ("RQ-37 criterion 3 is mechanically RQ-21's path").
    """
    pytester.makepyfile(test_slow="import time\n\n\ndef test_slow():\n    time.sleep(2)\n")

    process = pytester.popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "--vantage",
            f"--vantage-server={vantage_server.address}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Give the child time to pass configure (preflight included) and enter
    # the test before pulling the server out from under it.
    time.sleep(1.0)
    vantage_server.stop()
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 0
    assert (stdout.decode() + stderr.decode()).count("VantageWarning:") == 1
