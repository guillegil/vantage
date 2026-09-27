"""The plugin's two failure paths: the server cannot be reached at all, or
something goes wrong while reporting to one that can. Proven end to end
wherever only a real socket can prove the behaviour, and as focused unit
tests wherever the mechanism is a pure function or a decorator.

`_StubServer` stands in for `vantage_test_server.py`'s real `vantage`
server wherever a test is about a server that behaves badly (closes without
responding, never responds, sends garbage back) rather than about the real
ingestion endpoint.
"""

from __future__ import annotations

import itertools
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import warnings
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_vantage import vcs
from pytest_vantage.boundary import (
    VantageWarning,
    accumulation_isolated,
    fault_isolated,
    liveness_isolated,
    warn,
)
from pytest_vantage.plugin import _preflight_reachable

# At collection, not inside the tests that use it: `pytester` restores
# `sys.modules` after each test, dropping a module a test imported first,
# and a later `monkeypatch.setattr("pytest_vantage.recorder....")` would then
# patch that dropped copy while the plugin imports a fresh one.
from pytest_vantage.recorder import Recorder
from pytest_vantage.transport import Capabilities, fetch_capabilities, send
from vantage.service.errors import RejectionError
from vantage_test_server import VantageTestServer, wait_for_file

_PASSING_TEST = "def test_it():\n    assert True\n"
_A_SESSION_RUN_TO_ITS_END = SimpleNamespace(shouldfail=False, shouldstop=False)


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
    the loop, so a test can hand the plugin's client code exactly the
    malformed or absent response a well-behaved server never would.

    Accepts connections in a loop, each handled on its own thread, because a
    single session opens several: the preflight, the capability probe, and
    the start and finish reports.
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


def _read_request(conn: socket.socket) -> tuple[str, bytes]:
    """Read one whole HTTP request and return its request line and body.
    `("", b"")` for the bare TCP preflight, which connects and closes
    without sending a byte.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return "", b""
        data += chunk
    head, _separator, body = data.partition(b"\r\n\r\n")
    request_line, *header_lines = head.decode("latin-1").split("\r\n")
    length = 0
    for line in header_lines:
        name, _colon, value = line.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value)
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            break
        body += chunk
    return request_line, body


def _http_response(status: str, body: bytes = b"", headers: tuple[str, ...] = ()) -> bytes:
    lines = [f"HTTP/1.1 {status}", f"Content-Length: {len(body)}", "Connection: close", *headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def _acknowledgement_of(report_body: bytes) -> bytes:
    """The real ingestion route's answer to `report_body`: `201` with an
    acknowledgement naming the run the report carried.
    """
    run_id = json.loads(report_body)["run"]["id"]
    ack = json.dumps({"run_id": run_id, "status": "created", "ignored": []}).encode()
    return _http_response("201 Created", ack, ("Content-Type: application/json",))


_LIFECYCLE_ADVERTISED = _http_response(
    "200 OK", b'{"session_lifecycle": true}', ("Content-Type: application/json",)
)


def _accept_then_close(conn: socket.socket) -> None:
    """Accepts the connection and closes it without responding: no bytes
    back at all, not even a partial header.
    """


def _accept_and_hang(conn: socket.socket) -> None:
    """Accepts the connection and never responds. The sleep is far
    longer than any timeout a test configures -- the client's own
    ``--vantage-timeout`` is what must end this, never this function
    returning on its own.
    """
    time.sleep(30)


def _respond_with_unbounded_body(conn: socket.socket) -> None:
    """Never stops writing on its own, never sends a `Content-Length` and
    never closes -- an unbounded ``response.read()`` on the client side
    would have nothing to stop it but the server closing, which this
    handler deliberately never does. Only the client's own
    `MAX_RESPONSE_BYTES` cap can end this exchange from its side; the
    handler exits only once that makes the client stop reading and the
    connection breaks underneath it.
    """
    conn.recv(65536)
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
    chunk = b"x" * 65536
    while True:
        conn.sendall(chunk)


def _respond_with_non_json_body(conn: socket.socket) -> None:
    conn.recv(65536)
    body = b"not-json"
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )


def _respond_with_bare_500(conn: socket.socket) -> None:
    conn.recv(65536)
    conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")


def _drip_response(interval: float, byte_count: int) -> Callable[[socket.socket], None]:
    """Answers correctly -- the lifecycle advertised to a GET, an
    acknowledgement to a report -- but sends the answer's first `byte_count`
    bytes one at a time, `interval` seconds apart. Every byte arrives well
    inside a per-operation socket timeout; the whole answer arrives long
    after any deadline a test sets.
    """

    def _handler(conn: socket.socket) -> None:
        request_line, body = _read_request(conn)
        if not request_line:
            return
        answer = (
            _LIFECYCLE_ADVERTISED if request_line.startswith("GET") else _acknowledgement_of(body)
        )
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for index in range(byte_count):
            conn.sendall(answer[index : index + 1])
            time.sleep(interval)
        conn.sendall(answer[byte_count:])

    return _handler


# --- Unit: the preflight probe itself -------------------------------------


def test_preflight_reachable_is_false_on_connection_refused() -> None:
    assert _preflight_reachable(_closed_port_address(), timeout=1.0) is False


def test_preflight_reachable_is_false_on_unresolvable_host() -> None:
    assert (
        _preflight_reachable("http://this-host-does-not-exist.invalid:8765", timeout=1.0) is False
    )


def test_preflight_is_bounded_while_the_name_is_still_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The socket timeout does not cover name resolution, so a resolver that
    never answers would hold session start for its own timeout. The probe
    gives up at its deadline whatever it is waiting on.
    """
    release = threading.Event()

    def _unanswered_lookup(*args: object, **kwargs: object) -> list[object]:
        release.wait(timeout=10)
        raise socket.gaierror("no answer")

    monkeypatch.setattr(socket, "getaddrinfo", _unanswered_lookup)
    try:
        started = time.monotonic()
        reachable = _preflight_reachable("http://vantage.internal:9", timeout=0.3)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert reachable is False
    assert elapsed < 2.0


def test_preflight_reachable_is_true_when_something_listens() -> None:
    with _StubServer(_accept_then_close) as server:
        assert _preflight_reachable(server.address, timeout=1.0) is True


# --- Unit: the fault-isolation decorator itself ---------------------------
#
# `Recorder`'s hooks offer no seam to make both `pytest_report_header` and
# `pytest_sessionfinish` raise without monkeypatching the decorated method
# itself, which would bypass the code under test. A direct unit test against
# the decorator proves the same contract, independent of hook order.


class _Instrumented:
    def __init__(self, config: object) -> None:
        self._config = config
        self._disabled = False
        self.calls = 0

    @fault_isolated
    def raises(self) -> None:
        self.calls += 1
        raise RuntimeError("boom")

    @fault_isolated
    def raises_keyboard_interrupt(self) -> None:
        raise KeyboardInterrupt


def test_fault_isolated_catches_exception_and_latches_after_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings_seen: list[str] = []
    monkeypatch.setattr(
        "pytest_vantage.boundary.warn",
        lambda config, message: warnings_seen.append(message),
    )
    instance = _Instrumented(config=None)

    instance.raises()
    instance.raises()

    # The second call never reached the raising body at all -- the latch,
    # not a second catch, is what keeps this at one warning.
    assert instance.calls == 1
    assert len(warnings_seen) == 1


def test_fault_isolated_never_catches_keyboard_interrupt() -> None:
    instance = _Instrumented(config=None)

    with pytest.raises(KeyboardInterrupt):
        instance.raises_keyboard_interrupt()


# --- Unit: `liveness_isolated`, which latches its own flag -----------------
#
# Both decorators come from the same `_isolated(flag, description)` factory.
# These tests prove the two paths are independent in both directions: a
# liveness failure never sets `_disabled`, and the two flags never read or
# write each other's state.


class _DualInstrumented:
    def __init__(self, config: object) -> None:
        self._config = config
        self._disabled = False
        self._liveness_disabled = False
        self.calls = 0

    @fault_isolated
    def reporting_raises(self) -> None:
        self.calls += 1
        raise RuntimeError("reporting boom")

    @liveness_isolated
    def liveness_raises(self) -> None:
        self.calls += 1
        raise RuntimeError("liveness boom")


def test_liveness_isolated_latches_its_own_flag_and_leaves_disabled_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings_seen: list[str] = []
    monkeypatch.setattr(
        "pytest_vantage.boundary.warn",
        lambda config, message: warnings_seen.append(message),
    )
    instance = _DualInstrumented(config=None)

    instance.liveness_raises()
    instance.liveness_raises()

    # The latch, not a second catch, is what keeps this at one warning --
    # same shape as `fault_isolated`'s own latching test above.
    assert instance.calls == 1
    assert len(warnings_seen) == 1
    assert "error while reporting session liveness" in warnings_seen[0]
    assert instance._liveness_disabled is True
    assert instance._disabled is False


def test_liveness_isolated_and_fault_isolated_flags_never_read_or_set_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pytest_vantage.boundary.warn", lambda config, message: None)
    instance = _DualInstrumented(config=None)

    instance.liveness_raises()
    assert instance._liveness_disabled is True
    # A failure isolated by the liveness path must leave `_disabled`
    # untouched -- proven by exercising the reporting path afterwards and
    # seeing it still run instead of being silently skipped.
    assert instance._disabled is False
    instance.reporting_raises()
    assert instance.calls == 2

    instance.reporting_raises()
    assert instance.calls == 2  # the second call did not reach the body -- latched on its own flag
    assert instance._disabled is True
    assert instance._liveness_disabled is True  # unchanged, still latched from earlier


class _AccumulationInstrumented:
    def __init__(self, config: object) -> None:
        self._config = config
        self._disabled = False
        self._accumulation_warned = False
        self.calls = 0

    @accumulation_isolated
    def accumulate_raises(self) -> None:
        self.calls += 1
        raise RuntimeError("odd report")


def test_accumulation_isolated_warns_once_but_never_stops_running_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings_seen: list[str] = []
    monkeypatch.setattr(
        "pytest_vantage.boundary.warn",
        lambda config, message: warnings_seen.append(message),
    )
    instance = _AccumulationInstrumented(config=None)

    instance.accumulate_raises()
    instance.accumulate_raises()

    assert instance.calls == 2
    assert len(warnings_seen) == 1
    assert "error while recording a test report" in warnings_seen[0]
    assert instance._disabled is False


# --- Unit: `warn`'s fallback chain ------------------------------------------


def test_warn_emits_a_vantage_warning_by_default() -> None:
    with pytest.warns(VantageWarning, match="something went wrong"):
        warn(None, "vantage: something went wrong")  # type: ignore[arg-type]


def test_warn_falls_back_to_the_terminal_reporter_when_warnings_are_errors() -> None:
    """`filterwarnings = ["error"]` (or `-W error`) turns
    `warnings.warn(VantageWarning(...))` into a raised exception instead of
    an ordinary warning -- the message must still reach the user rather
    than let a reporting failure crash the session that way.
    `warnings.catch_warnings` + `simplefilter("error")` recreates that
    configuration directly, independent of how this suite's own
    `pyproject.toml` happens to be set up.
    """
    lines: list[str] = []

    class _ReporterDouble:
        def write_line(self, message: str) -> None:
            lines.append(message)

    class _PluginManagerDouble:
        def get_plugin(self, name: str) -> object:
            assert name == "terminalreporter"
            return _ReporterDouble()

    class _ConfigDouble:
        pluginmanager = _PluginManagerDouble()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warn(_ConfigDouble(), "vantage: something went wrong")  # type: ignore[arg-type]

    assert lines == ["vantage: something went wrong"]


def test_warn_falls_back_to_stderr_when_no_terminal_reporter_is_registered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _PluginManagerDouble:
        def get_plugin(self, name: str) -> object:
            return None

    class _ConfigDouble:
        pluginmanager = _PluginManagerDouble()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warn(_ConfigDouble(), "vantage: something went wrong")  # type: ignore[arg-type]

    assert "vantage: something went wrong" in capsys.readouterr().err


# --- The server cannot be reached at all -----------------------------------


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


def test_recorder_is_not_registered_when_the_preflight_fails(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    # `pytest.warns` rather than a bare call: the preflight failing here is
    # the whole point of the test, so asserting the warning is part of
    # the proof -- and it stops this in-process `parseconfigure` from
    # leaking a `VantageWarning` into the summary of the suite running it.
    with pytest.warns(VantageWarning, match="cannot reach"):
        config = pytester.parseconfigure("--vantage", f"--vantage-server={_closed_port_address()}")

    assert not any(isinstance(plugin, Recorder) for plugin in config.pluginmanager.get_plugins())


def test_preflight_falls_back_to_the_scheme_default_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An address with no explicit port -- `http://example.com`, which is
    what a user types -- must probe 80, and its https form 443.

    `urlparse(...).port` is `None` for those, so the scheme default is the
    only thing standing between a normal address and a connect to port 0.
    Getting it wrong fails the preflight, warns "cannot reach", and silently
    records nothing for an address that was perfectly good. Every other test
    in this file builds an address with an explicit port, so nothing else
    exercises this line.

    `socket.create_connection` is intercepted rather than dialled: the
    assertion is about which port is chosen, and reaching the real
    example.com would make a unit test depend on the network.
    """
    attempted: list[tuple[str, int]] = []

    def _capture(address: tuple[str, int], timeout: float | None = None) -> socket.socket:
        attempted.append(address)
        raise ConnectionRefusedError

    monkeypatch.setattr("pytest_vantage.plugin.socket.create_connection", _capture)

    _preflight_reachable("http://example.com", 1.0)
    _preflight_reachable("https://example.com", 1.0)
    _preflight_reachable("http://example.com:8765", 1.0)

    assert attempted == [("example.com", 80), ("example.com", 443), ("example.com", 8765)]


def test_server_dropped_mid_session_preserves_exit_status_and_warns_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """The preflight and the start-write reach the server; it is stopped
    while the test runs, so the finish-write fails and the failure surfaces
    at report time through `fault_isolated`, not through a second
    preflight.

    The child's test signals that it has started and then waits for the
    parent's go-ahead, so the server is gone exactly between the two
    writes, however slowly the child starts.
    """
    entered = pytester.path / "entered"
    proceed = pytester.path / "proceed"
    pytester.makepyfile(
        test_waits=(
            "import pathlib\nimport time\n\n\n"
            "def test_waits():\n"
            f"    pathlib.Path({str(entered)!r}).touch()\n"
            "    deadline = time.monotonic() + 15\n"
            f"    while not pathlib.Path({str(proceed)!r}).exists():\n"
            "        assert time.monotonic() < deadline\n"
            "        time.sleep(0.01)\n"
        )
    )

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
        # `pytester.popen` otherwise opens a stdin pipe and closes it, and
        # before Python 3.13 `communicate()` still flushes that closed file,
        # raising `ValueError`. `DEVNULL` leaves nothing to flush.
        stdin=subprocess.DEVNULL,
    )
    try:
        wait_for_file(entered)
        vantage_server.stop()
        proceed.touch()
        stdout, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    output = stdout.decode() + stderr.decode()
    assert process.returncode == 0
    assert output.count("VantageWarning:") == 1
    assert "error while reporting: " in output


# --- Something goes wrong while reporting ---------------------------------


def test_reporting_error_preserves_passing_exit_status_and_warns_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process (`pytester.runpytest`), not a subprocess: forcing a
    generic internal error -- not a network failure -- needs the monkeypatch
    to land in the same process pytest actually runs the session in.
    Patches `pytest_vantage.recorder.send`, the name `Recorder` actually
    calls, not `pytest_vantage.transport.send` -- `recorder.py` imports the
    function by name at module load, so patching the origin module's
    attribute after that binding has already happened would have no effect.
    """

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pytest_vantage.recorder.send", _raise)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    # Two independent failures: the patched `send` raises for the start-write
    # as well as the finish-write, and the two are isolated by different
    # decorators, so each warns once. Only the finish-write's warning reaches
    # `RunResult`; one raised as early as `pytest_sessionstart` escapes an
    # in-process run's capture into THIS session (see `_combined_output`).
    # `pytest.warns` asserts it rather than letting it leak into the summary.
    with pytest.warns(VantageWarning, match="session liveness"):
        result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert _combined_output(result).count("VantageWarning:") == 1


def test_reporting_error_preserves_failing_exit_status_and_warns_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pytest_vantage.recorder.send", _raise)
    pytester.makepyfile(test_sample="def test_it():\n    assert False\n")

    # See the sibling above: the start-write's warning escapes an in-process
    # run's capture, so it is asserted here rather than left to leak.
    with pytest.warns(VantageWarning, match="session liveness"):
        result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(failed=1)
    assert result.ret == 1
    assert _combined_output(result).count("VantageWarning:") == 1


class _UnavailableError(RejectionError):
    """A refusal the store is made to raise; the server answers it with 503."""

    status_code = 503
    error = "unavailable"


def test_a_failing_start_write_warns_once_silences_the_heartbeats_and_keeps_every_result(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that advertises the lifecycle but refuses the start-write
    -- and so knows no run any heartbeat could name -- costs one warning.
    The start-write is `@liveness_isolated`, not `@fault_isolated`: its
    failure latches `_liveness_disabled`, which the beats share, so five
    beat opportunities (a zero `_BEAT_INTERVAL_SECONDS`) send no heartbeat
    at all. The finish-write alone then records the whole run, every
    result included, and the exit status is untouched.

    `runpytest_subprocess`, not in-process `runpytest`: a `VantageWarning`
    raised as early as `pytest_sessionstart` escapes an in-process run's
    capture (see `_combined_output`). The server runs in this process, so
    its store can still be made to refuse the first write.
    """
    real_record_session = vantage_server.store.record_session
    writes = itertools.count()

    def _refuse_the_first_write(*args: Any, **kwargs: Any) -> bool:
        if next(writes) == 0:
            raise _UnavailableError("storage is unavailable")
        return real_record_session(*args, **kwargs)

    monkeypatch.setattr(vantage_server.store, "record_session", _refuse_the_first_write)
    pytester.makeconftest(
        "import pytest_vantage.recorder as recorder\nrecorder._BEAT_INTERVAL_SECONDS = 0.0\n"
    )
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(5))
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=5)
    assert result.ret == 0
    output = _combined_output(result)
    assert output.count("VantageWarning:") == 1
    assert "error while reporting session liveness: HTTP Error 503" in output
    assert vantage_server.requests == [
        ("GET", "/api/v1/capabilities"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/runs"),
    ]
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert execution.exit_status == 0
    assert len(vantage_server.results()) == 5


def _assert_the_probe_and_the_report_each_warned(output: str, failure: str) -> None:
    """A server that fails every request fails the capability probe first,
    which turns the start-write and heartbeats off with one warning, and
    then the finish-write, with a second. Both are matched by their text,
    naming `failure` (a regular expression), so one warning cannot stand in
    for the other.
    """
    assert output.count("VantageWarning:") == 2
    assert re.search(rf"the capability probe to \S+ failed \((?:{failure})", output), output
    assert re.search(rf"error while reporting: (?:{failure})", output), output


def test_server_accepts_then_closes_without_responding(pytester: pytest.Pytester) -> None:
    with _StubServer(_accept_then_close) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    # Closing at once races the client's request, so the client sees the
    # answer missing, the connection reset, or its own write fail.
    _assert_the_probe_and_the_report_each_warned(
        _combined_output(result),
        "Remote end closed connection without response|.*Connection reset by peer|<urlopen error",
    )


def test_server_accepts_and_never_answers_finishes_within_timeout_plus_five_seconds(
    pytester: pytest.Pytester,
) -> None:
    with _StubServer(_accept_and_hang) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        started = time.monotonic()
        result = pytester.runpytest_subprocess(
            "--vantage",
            f"--vantage-server={server.address}",
            "--vantage-timeout=0.3",
            timeout=15,
        )
        elapsed = time.monotonic() - started

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert elapsed < 0.3 + 5.0
    # The request's deadline and its socket timeout expire together; either
    # may be the one that reports it.
    _assert_the_probe_and_the_report_each_warned(
        _combined_output(result), "no complete answer within 0.3s|timed out"
    )


# --- Untrusted responses ---------------------------------------------------


def test_oversized_response_is_bounded_and_does_not_hang(pytester: pytest.Pytester) -> None:
    with _StubServer(_respond_with_unbounded_body) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess(
            "--vantage", f"--vantage-server={server.address}", timeout=15
        )

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    # The truncated 64 KiB chunk of the unbounded body is not valid JSON
    # either, so this doubles as the "malformed acknowledgement is a
    # warning, never an exception" proof.
    _assert_the_probe_and_the_report_each_warned(_combined_output(result), "Expecting value")


def test_non_json_response_is_a_warning_not_a_crash(pytester: pytest.Pytester) -> None:
    with _StubServer(_respond_with_non_json_body) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    _assert_the_probe_and_the_report_each_warned(_combined_output(result), "Expecting value")


def test_bare_500_response_is_a_warning_not_a_crash(pytester: pytest.Pytester) -> None:
    with _StubServer(_respond_with_bare_500) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    _assert_the_probe_and_the_report_each_warned(
        _combined_output(result), "HTTP Error 500: Internal Server Error"
    )


_A_REPORT: dict[str, object] = {"run": {"id": "a" * 32}}


@pytest.mark.parametrize(
    "answer",
    [
        b'{"items": [], "has_more": false}',
        b'{"run_id": "' + b"b" * 32 + b'", "status": "created", "ignored": []}',
        b'{"run_id": "' + b"a" * 32 + b'", "status": "acknowledged"}',
        b"[]",
    ],
    ids=["run-list", "another-run", "unknown-status", "not-an-object"],
)
def test_a_2xx_answer_that_does_not_acknowledge_the_report_raises(answer: bytes) -> None:
    """Only an acknowledgement of the run just sent counts as delivered.
    Any other JSON a 2xx carries -- a proxy's page, another endpoint's
    answer -- would otherwise pass for success and lose the session with no
    warning.
    """

    def _answer(conn: socket.socket) -> None:
        if _read_request(conn)[0]:
            conn.sendall(_http_response("200 OK", answer, ("Content-Type: application/json",)))

    with _StubServer(_answer) as server, pytest.raises(ValueError, match="without acknowledging"):
        send(server.address, _A_REPORT, timeout=1.0)


# --- Redirects, environment proxies and trickled answers --------------------


def _redirecting_handler(status: int, targets_seen: list[str]) -> Callable[[socket.socket], None]:
    """Answers every request under `/old` with a `status` redirect to the
    same path without the prefix, the shape of a canonical-host or
    http-to-https redirect. The redirect target answers the way the real
    server would a GET -- the capability route advertises the lifecycle,
    the run list is JSON -- so following the redirect would look like
    success.
    """

    def _handler(conn: socket.socket) -> None:
        request_line, _body = _read_request(conn)
        if not request_line:
            return
        target = request_line.split(" ")[1]
        targets_seen.append(target)
        if target.startswith("/old/"):
            location = target.removeprefix("/old")
            conn.sendall(_http_response(f"{status} Redirect", headers=(f"Location: {location}",)))
        elif target == "/api/v1/capabilities":
            conn.sendall(_LIFECYCLE_ADVERTISED)
        else:
            body = b'{"items": [], "has_more": false}'
            conn.sendall(_http_response("200 OK", body, ("Content-Type: application/json",)))

    return _handler


@pytest.mark.parametrize("status", [301, 302, 303])
def test_a_redirected_report_is_a_warning_never_a_silent_get(
    pytester: pytest.Pytester, status: int
) -> None:
    """urllib re-issues a POST answered 301/302/303 as a bodiless GET, which
    the run list answers 2xx: the session would be lost with no warning.
    No redirect is followed, so the report fails loudly instead.
    """
    targets_seen: list[str] = []
    with _StubServer(_redirecting_handler(status, targets_seen)) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess(
            "--vantage", f"--vantage-server={server.address}/old"
        )

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert f"error while reporting: HTTP Error {status}" in _combined_output(result)
    assert [target for target in targets_seen if not target.startswith("/old/")] == []


def test_the_capability_probe_does_not_follow_a_redirect() -> None:
    targets_seen: list[str] = []
    with _StubServer(_redirecting_handler(302, targets_seen)) as server:
        assert not fetch_capabilities(f"{server.address}/old", timeout=1.0)

    assert targets_seen == ["/old/api/v1/capabilities"]


def test_a_redirect_to_another_scheme_is_never_followed() -> None:
    """The address passed an http/https check; a `Location: ftp://...` must
    not take the report anywhere that check never saw.
    """
    ftp_connections: list[bool] = []
    with _StubServer(lambda conn: ftp_connections.append(True)) as ftp_listener:
        location = f"Location: ftp://127.0.0.1:{ftp_listener.port}/ack.json"

        def _redirect_to_ftp(conn: socket.socket) -> None:
            if _read_request(conn)[0]:
                conn.sendall(_http_response("302 Found", headers=(location,)))

        with _StubServer(_redirect_to_ftp) as server:
            with pytest.raises(urllib.error.HTTPError, match="302"):
                send(server.address, _A_REPORT, timeout=1.0)

    assert ftp_connections == []


@pytest.fixture
def environment_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """An `http_proxy` in the environment pointing at a proxy that records
    every request and answers `502`, as a remote proxy asked for the
    client's loopback would. Yields the request lines it received.
    """
    requests_seen: list[str] = []

    def _bad_gateway(conn: socket.socket) -> None:
        request_line, _body = _read_request(conn)
        if request_line:
            requests_seen.append(request_line)
            conn.sendall(_http_response("502 Bad Gateway"))

    with _StubServer(_bad_gateway) as proxy:
        for name in ("no_proxy", "NO_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "VANTAGE_SERVER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("http_proxy", proxy.address)
        monkeypatch.setenv("https_proxy", proxy.address)
        # urllib's default opener reads the proxy variables once, when first
        # built; dropping any cached one lets a regression back to it see
        # this proxy.
        monkeypatch.setattr(urllib.request, "_opener", None)
        yield requests_seen


def test_reports_take_the_preflights_direct_route_not_an_environment_proxy(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    environment_proxy: list[str],
) -> None:
    """The preflight connects directly, so the requests must too: through
    the proxy, the probe and the report would fail against a server the
    preflight just reached, after handing the failure text to the proxy.
    """
    pytester.makepyfile(test_sample="def test_it():\n    assert 'PROXY-MARKER' == ''\n")

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result.assert_outcomes(failed=1)
    assert result.ret == 1
    assert "VantageWarning" not in _combined_output(result)
    assert len(vantage_server.executions()) == 1
    assert environment_proxy == []


def test_send_is_bounded_by_its_timeout_as_a_whole() -> None:
    """A per-operation socket timeout restarts with every byte received, so
    an answer trickled one byte at a time is bounded by nothing. The
    timeout is a deadline on the whole exchange instead.
    """
    # Every byte arrives well inside the timeout; the whole answer takes 4s.
    with _StubServer(_drip_response(interval=0.05, byte_count=80)) as server:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            send(server.address, _A_REPORT, timeout=0.3)
        elapsed = time.monotonic() - started

    assert elapsed < 2.0


def test_a_refused_report_releases_its_connection() -> None:
    """A non-2xx answer arrives as an `HTTPError` that still holds the open
    response. The connection is released at once, not left open until the
    exception is garbage-collected.
    """
    released: list[bool] = []
    watched = threading.Event()

    def _refuse_then_watch(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")
        conn.settimeout(2.0)
        try:
            released.append(conn.recv(1) == b"")
        except TimeoutError:
            released.append(False)
        watched.set()

    with _StubServer(_refuse_then_watch) as server:
        with pytest.raises(urllib.error.HTTPError) as refusal:
            send(server.address, _A_REPORT, timeout=5.0)
        assert watched.wait(timeout=5.0)

    assert refusal.value.code == 500
    assert released == [True]


def test_a_server_that_trickles_its_answers_cannot_hold_the_session(
    pytester: pytest.Pytester,
) -> None:
    """Every request of the session is trickled, each byte well inside the
    socket timeout: the probe and the finish-write are each abandoned at
    their deadline, and the abandoned finish-write is a warning, not a
    silent delay.
    """
    with _StubServer(_drip_response(interval=0.05, byte_count=80)) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        started = time.monotonic()
        result = pytester.runpytest_subprocess(
            "--vantage",
            f"--vantage-server={server.address}",
            "--vantage-timeout=0.3",
            timeout=30,
        )
        elapsed = time.monotonic() - started

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert elapsed < 2 * 0.3 + 5.0
    assert "error while reporting: no complete answer within 0.3s" in _combined_output(result)


# --- Activity-driven heartbeats --------------------------------------------


def test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """`_maybe_beat` is `@liveness_isolated`, which latches after its first
    failure, so a failing heartbeat is never attempted again. Across five
    tests (a beat due on every report, forced by a zero
    `_BEAT_INTERVAL_SECONDS`), that is exactly one warning, never one per
    beat -- and `accumulate` running first means every test's result
    still reaches the finish-write, whose own `send` is unpatched, so the
    real `vantage_server` ends up with all five. Beat spacing is
    `test_run_report.py::test_heartbeats_are_one_interval_apart`.

    A `conftest.py` written into the pytester's own directory, not
    `monkeypatch`: this test needs `runpytest_subprocess` (an in-process
    `runpytest` would let a `pytest_sessionstart`-era warning escape into
    THIS session's own capture, the same phenomenon
    `_combined_output`'s docstring names), and `monkeypatch` cannot reach
    into a genuinely separate process.
    """
    pytester.makeconftest(
        "import pytest_vantage.recorder as recorder\n"
        "recorder._BEAT_INTERVAL_SECONDS = 0.0\n"
        "def _fail_heartbeat(*args, **kwargs):\n"
        "    raise RuntimeError('boom')\n"
        "recorder.send_heartbeat = _fail_heartbeat\n"
    )
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(5))
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=5)
    assert result.ret == 0
    assert _combined_output(result).count("VantageWarning:") == 1
    assert len(vantage_server.results()) == 5


# --- Capability advertisement: fail closed, degrade for older servers -----


def _respond_capability_with(body: bytes) -> Callable[[socket.socket], None]:
    """A 200 response carrying `body` verbatim as the capability probe's
    answer -- the fail-closed cases below vary only the body.
    """

    def _handler(conn: socket.socket) -> None:
        conn.recv(65536)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )

    return _handler


def _respond_capability_404(conn: socket.socket) -> None:
    """An older server's answer: no `/api/v1/capabilities` route at all, so
    it answers `404`. That is the signal, not a transport failure.
    """
    conn.recv(65536)
    body = b"Not Found"
    conn.sendall(
        b"HTTP/1.1 404 Not Found\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )


_NOT_ADVERTISED = "does not advertise the session lifecycle"
_PROBE_FAILED = "the capability probe to"

# Each case is a way a naive capability check could quietly fail open, with
# the diagnosis it must produce: a negative answer from the server, or a
# probe that got no usable answer at all.
_FAIL_CLOSED_CASES: list[tuple[str, Callable[[socket.socket], None], str]] = [
    ("route-missing", _respond_capability_404, _NOT_ADVERTISED),
    ("explicit-false", _respond_capability_with(b'{"session_lifecycle": false}'), _NOT_ADVERTISED),
    ("key-missing", _respond_capability_with(b"{}"), _NOT_ADVERTISED),
    ("malformed-json", _respond_capability_with(b"{not-json"), _PROBE_FAILED),
    ("wrong-json-type", _respond_capability_with(b'["session_lifecycle", true]'), _PROBE_FAILED),
    ("empty-body", _respond_capability_with(b""), _PROBE_FAILED),
    ("http-500", _respond_with_bare_500, _PROBE_FAILED),
    ("hangs-past-liveness-timeout", _accept_and_hang, _PROBE_FAILED),
    ("drips-past-liveness-timeout", _drip_response(interval=0.1, byte_count=40), _PROBE_FAILED),
]


@pytest.mark.parametrize(
    ("case_id", "handle_connection", "diagnosis"),
    _FAIL_CLOSED_CASES,
    ids=[case[0] for case in _FAIL_CLOSED_CASES],
)
def test_fetch_capabilities_fails_closed_on_every_non_positive_answer(
    case_id: str, handle_connection: Callable[[socket.socket], None], diagnosis: str
) -> None:
    """Only an explicit `{"session_lifecycle": true}` may enable the
    lifecycle; every other answer turns it off without raising. The reason
    tells a server that said no apart from a probe that failed -- telling
    the user to upgrade a server that is merely unreachable or misaddressed
    sends them the wrong way -- and names the probed URL.
    """
    with _StubServer(handle_connection) as server:
        capabilities = fetch_capabilities(server.address, timeout=0.3)

    assert not capabilities
    assert capabilities.problem is not None
    assert diagnosis in capabilities.problem
    assert f"{server.address}/api/v1/capabilities" in capabilities.problem


def test_fetch_capabilities_returns_true_for_the_one_explicit_positive_answer() -> None:
    """The fail-closed cases above prove every negative answer degrades;
    this proves the positive answer is not also accidentally degraded."""
    with _StubServer(_respond_capability_with(b'{"session_lifecycle": true}')) as server:
        assert fetch_capabilities(server.address, timeout=1.0) == Capabilities(True)


def test_the_probe_warning_names_the_url_an_address_with_a_path_doubles_into(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """`/api/v1` on the address doubles into the probed path, so a current
    server answers `404`. The warning names the URL actually probed, which
    shows the mistake in the address, not only the address itself.
    """
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}/api/v1"
    )

    result.assert_outcomes(passed=1)
    output = _combined_output(result)
    assert f"GET {vantage_server.address}/api/v1/api/v1/capabilities answered 404" in output


def _capturing_handler(
    requests_seen: list[tuple[str, bytes]],
) -> Callable[[socket.socket], None]:
    """Records the request line and body of every connection this stub
    server accepts, in order, and answers as an older `vantage` would: the
    bare TCP preflight gets nothing back (none is needed), the capability
    probe is answered `404`, and anything else -- the finish-write -- is
    acknowledged `201 Created`.
    """

    def _handler(conn: socket.socket) -> None:
        request_line, body = _read_request(conn)
        requests_seen.append((request_line, body))
        if not request_line:
            return  # the bare TCP preflight: connects, sends nothing, closes
        if request_line.startswith("GET /api/v1/capabilities"):
            conn.sendall(_http_response("404 Not Found", b"Not Found"))
            return
        conn.sendall(_acknowledgement_of(body))

    return _handler


def test_capability_probe_404_sends_no_start_write_and_no_heartbeat(
    pytester: pytest.Pytester,
) -> None:
    """A server that answers the capability probe `404` (an older `vantage`)
    records the session with no start-write and no heartbeat. A heartbeat is
    made due on every report, across three tests, so one would be sent if
    the gate failed. Exactly three connections are opened -- the bare
    preflight, the capability probe, and the finish-write -- never a fourth
    for a start-write or a heartbeat. The finish-write's JSON body has
    exactly the ordinary report shape, with no lifecycle field added by the
    degraded path.
    """
    requests_seen: list[tuple[str, bytes]] = []
    pytester.makeconftest(
        "import pytest_vantage.recorder as recorder\nrecorder._BEAT_INTERVAL_SECONDS = 0.0\n"
    )
    with _StubServer(_capturing_handler(requests_seen)) as server:
        pytester.makepyfile(
            test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(3))
        )
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=3)
    assert result.ret == 0
    assert [request_line for request_line, _body in requests_seen] == [
        "",
        "GET /api/v1/capabilities HTTP/1.1",
        "POST /api/v1/runs HTTP/1.1",
    ]
    payload = json.loads(requests_seen[2][1])
    assert set(payload) == {"run", "results", "vcs"}
    assert set(payload["run"]) == {
        "id",
        "started_at",
        "finished_at",
        "exit_status",
        "interrupted",
        "interrupt_reason",
    }
    assert payload["run"]["finished_at"] is not None


def test_capability_probe_404_warns_once_and_still_records_the_result(
    pytester: pytest.Pytester,
) -> None:
    """The degradation warns exactly once, on the liveness latch, naming the
    address -- and does not disable result recording: the finish-write's
    `results` array still carries the one test that ran.
    """
    requests_seen: list[tuple[str, bytes]] = []
    with _StubServer(_capturing_handler(requests_seen)) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

        result.assert_outcomes(passed=1)
        assert result.ret == 0
        output = _combined_output(result)
        assert output.count("VantageWarning:") == 1
        assert (
            f"{server.address} does not advertise the session lifecycle "
            f"(GET {server.address}/api/v1/capabilities answered 404)"
        ) in output

    payload = json.loads(requests_seen[2][1])
    assert len(payload["results"]) == 1


def test_capability_probe_is_bounded_by_the_liveness_timeout_not_the_report_timeout(
    pytester: pytest.Pytester,
) -> None:
    """The capability probe is bounded by `resolve_liveness_timeout`, never
    the larger report timeout: a probe that hung until the report timeout
    would put that cost in front of every session. The finish-write answers
    normally, so only a probe wrongly bounded by the 10-second report
    timeout would make this run long. The child's liveness cap is cut from
    2.0 to 0.3 seconds, so the right bound is not waited out in full.
    """
    pytester.makeconftest(
        "import pytest_vantage.config as config\nconfig._MAX_SHORT_TIMEOUT = 0.3\n"
    )
    connections_seen = itertools.count(1)

    def _hang_the_capability_probe_only(conn: socket.socket) -> None:
        connection_number = next(connections_seen)
        if connection_number == 1:
            return  # the bare TCP preflight: no response needed
        if connection_number == 2:
            time.sleep(30)  # the capability probe: must trip well before this
            return
        _request_line, body = _read_request(conn)
        conn.sendall(_acknowledgement_of(body))

    with _StubServer(_hang_the_capability_probe_only) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        started = time.monotonic()
        result = pytester.runpytest_subprocess(
            "--vantage",
            f"--vantage-server={server.address}",
            "--vantage-timeout=10.0",
            timeout=15,
        )
        elapsed = time.monotonic() - started

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert elapsed < 0.3 + 5.0


def test_recorder_skips_start_write_and_heartbeat_when_lifecycle_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pytest_sessionstart` and `_maybe_beat` both return immediately when
    `lifecycle_available=False`, without ever calling `send` or
    `send_heartbeat`.
    """
    calls: list[str] = []
    monkeypatch.setattr("pytest_vantage.recorder.send", lambda *a, **k: calls.append("send"))
    monkeypatch.setattr(
        "pytest_vantage.recorder.send_heartbeat",
        lambda *a, **k: calls.append("send_heartbeat"),
    )
    monkeypatch.setattr("pytest_vantage.recorder._BEAT_INTERVAL_SECONDS", 0.0)
    # This test is about the lifecycle-degraded gate, not vcs capture --
    # neutralise it rather than spawning a real `git` subprocess for
    # something this test does not exercise.
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    class _ConfigDouble:
        rootpath = "unused"

        def getoption(self, name: str, default: object = None) -> object:
            return None

    recorder = Recorder(
        _ConfigDouble(),  # type: ignore[arg-type]
        "http://127.0.0.1:1",
        1.0,
        lifecycle_available=False,
    )

    with pytest.warns(VantageWarning, match="does not advertise the session lifecycle"):
        recorder.pytest_sessionstart()
    recorder._maybe_beat()

    assert calls == []


# --- VCS capture isolation -------------------------------------------------


def test_git_failure_disables_nothing_else(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git failure disables nothing else in the same session.
    `vcs.capture` never raises on its own, so patching it to raise tests the
    wrapper around it -- a non-latching path, neither `fault_isolated` nor
    `liveness_isolated`. Neither latch flag may be set, and the session must
    survive with nulls, not merely without a crash: every result and the
    run row itself must still land.
    """
    original_init = Recorder.__init__
    created: list[Recorder] = []

    def _spy_init(self: Recorder, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        created.append(self)

    monkeypatch.setattr("pytest_vantage.recorder.Recorder.__init__", _spy_init)

    def _raise(rootpath: object) -> vcs.VcsSnapshot:
        raise RuntimeError("git exploded")

    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", _raise)
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(3))
    )

    # The escaped exception is exactly what `_capture_vcs` warns about once
    # -- an in-process run's own `pytest_configure`-era warning escapes THIS
    # session's capture, the same phenomenon `_combined_output`'s docstring
    # names above, so it is asserted here rather than left to leak.
    with pytest.warns(VantageWarning, match="could not read the git repository"):
        result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=3)
    assert result.ret == 0
    assert len(created) == 1
    recorder = created[0]
    # Not merely "did not crash" -- neither latch this failure could have
    # touched is set, proving it used neither existing isolation path.
    assert recorder._disabled is False
    assert recorder._liveness_disabled is False
    executions = vantage_server.executions()
    assert len(executions) == 1
    assert len(vantage_server.results()) == 3


@pytest.mark.slow
@pytest.mark.usefixtures("git_confined_to_basetemp")
def test_hung_git_does_not_delay_session(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung git is bounded by the whole-capture budget. A fake `git` on
    `PATH` sleeps well past it, wired through a real `Recorder` -- unlike
    `test_vcs.py`'s capture-level test, this proves the budget bounds
    `Recorder.__init__` itself, and that the run is still reported with all
    five snapshot fields null.

    `vcs.capture` reads the budget at call time and `runpytest` runs the
    session in this process, so a half-second budget stands in for the
    five-second one without waiting it out.
    """
    real_budget = vcs._CAPTURE_BUDGET_SECONDS
    budget = 0.5
    monkeypatch.setattr(vcs, "_CAPTURE_BUDGET_SECONDS", budget)
    shim_dir = pytester.path / "shim"
    shim_dir.mkdir()
    shim_path = shim_dir / "git"
    shim_path.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    shim_path.chmod(0o755)
    monkeypatch.setenv("PATH", str(shim_dir))

    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout: sent.append(report),
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)

    started = time.monotonic()
    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")
    elapsed = time.monotonic() - started

    result.assert_outcomes(passed=1)
    # No shorter than the patched budget: the capture ran the fake git and
    # waited the budget out, rather than finding no git and returning at
    # once. Shorter than the real budget: a capture that waited on the real
    # budget -- the patch no longer reaching it -- spends all of it before
    # the session goes on, while the patched one leaves 4.5 seconds for
    # the rest of a one-test session on a loaded machine.
    assert budget <= elapsed < real_budget
    assert sent, "no report captured -- the session never reached the finish-write"
    finish_report = sent[-1]
    assert finish_report["vcs"] == {
        "commit": None,
        "branch": None,
        "commit_subject": None,
        "dirty": None,
        "root": None,
    }


def test_every_recorder_hook_is_under_the_isolation_meant_for_it() -> None:
    """Every `pytest_*` hook on `Recorder` is wrapped by an isolation
    decorator, and by the right one: only the hooks that report the run --
    to the server, and what the finish did at the end of the terminal output
    -- share `_disabled`, so neither liveness nor an odd test report can
    switch the finish-write off. The hooks are enumerated, so one
    added later without a decorator, or under the wrong one, fails here.
    """
    isolation = {
        name: getattr(getattr(Recorder, name), "isolation_flag", None)
        for name in dir(Recorder)
        if name.startswith("pytest_")
    }

    assert isolation == {
        "pytest_collectreport": "_accumulation_warned",
        "pytest_configure_node": "_accumulation_warned",
        "pytest_keyboard_interrupt": "_accumulation_warned",
        "pytest_report_header": "_disabled",
        "pytest_runtest_logreport": "_accumulation_warned",
        "pytest_sessionfinish": "_disabled",
        "pytest_sessionstart": "_liveness_disabled",
        "pytest_terminal_summary": "_disabled",
        "pytest_testnodedown": "_accumulation_warned",
        "pytest_unconfigure": "_disabled",
    }
    assert getattr(Recorder._maybe_beat, "isolation_flag", None) == "_liveness_disabled"


def test_an_unrecordable_test_report_never_disables_the_finish_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report the plugin cannot record -- here one with no node id at
    all -- warns once and costs that report alone: later reports are still
    recorded, and the finish-write still goes out with them.
    """
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send", lambda address, report, *, timeout: sent.append(report)
    )
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    class _ConfigDouble:
        rootpath = "unused"

    class _Report:
        def __init__(self, when: str, outcome: str = "passed") -> None:
            self.nodeid = "test_sample.py::test_it"
            self.when = when
            self.outcome = outcome
            self.duration = 0.0

    recorder = Recorder(
        _ConfigDouble(),  # type: ignore[arg-type]
        "http://127.0.0.1:1",
        1.0,
        lifecycle_available=True,
    )

    with pytest.warns(VantageWarning, match="error while recording a test report") as warned:
        recorder.pytest_runtest_logreport(object())  # type: ignore[arg-type]
        recorder.pytest_runtest_logreport(object())  # type: ignore[arg-type]
    for when in ("setup", "call", "teardown"):
        recorder.pytest_runtest_logreport(_Report(when))  # type: ignore[arg-type]
    recorder.pytest_sessionfinish(session=_A_SESSION_RUN_TO_ITS_END, exitstatus=0)  # type: ignore[arg-type]

    assert len(warned) == 1
    assert recorder._disabled is False
    assert len(sent) == 1
    assert [entry["node_id"] for entry in sent[0]["results"]] == ["test_sample.py::test_it"]  # type: ignore[attr-defined]


def test_a_result_that_cannot_be_built_warns_once_and_costs_only_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reports that cannot be built into a result -- here a teardown with no
    setup before it -- lose that one test its result. The finish-write
    still carries every other result, and one warning says how many were
    lost, so the gap in the recorded run is never silent.
    """
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send", lambda address, report, *, timeout: sent.append(report)
    )
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    class _ConfigDouble:
        rootpath = "unused"

    def _report(node_id: str, when: str) -> pytest.TestReport:
        return pytest.TestReport(
            nodeid=node_id,
            location=("test_sample.py", 0, node_id),
            keywords={},
            outcome="passed",
            longrepr=None,
            when=when,  # type: ignore[arg-type]
        )

    recorder = Recorder(
        _ConfigDouble(),  # type: ignore[arg-type]
        "http://127.0.0.1:1",
        1.0,
        lifecycle_available=True,
    )
    recorder.pytest_runtest_logreport(_report("test_sample.py::test_orphan", "teardown"))
    for when in ("setup", "call", "teardown"):
        recorder.pytest_runtest_logreport(_report("test_sample.py::test_it", when))

    with pytest.warns(VantageWarning) as warned:
        recorder.pytest_sessionfinish(session=_A_SESSION_RUN_TO_ITS_END, exitstatus=0)  # type: ignore[arg-type]

    assert [str(w.message) for w in warned] == ["vantage: 1 test result(s) could not be recorded"]
    assert recorder._disabled is False
    (report,) = sent
    assert [entry["node_id"] for entry in report["results"]] == ["test_sample.py::test_it"]  # type: ignore[attr-defined]
