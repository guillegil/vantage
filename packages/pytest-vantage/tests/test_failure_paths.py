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

import itertools
import socket
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Callable

import pytest
from pytest_vantage.boundary import VantageWarning, _warn, fault_isolated, liveness_isolated
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


def _accept_and_hang(conn: socket.socket) -> None:
    """RQ-21's "accepts and never responds" scenario. The sleep is far
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


# --- Unit: the fault-isolation decorator itself (RQ-21 "every hook is
# fault-isolated") ------------------------------------------------------------
#
# `Recorder`'s own hook bodies offer no seam to make BOTH
# `pytest_report_header` and `pytest_sessionfinish` raise independently
# without monkeypatching the already-decorated method itself, which would
# bypass the very code this scenario exists to prove. A direct unit test
# against the decorator proves the same contract for real, and does so
# without depending on which of `Recorder`'s hooks happens to run first.


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


@pytest.mark.req(id="RQ-21")
def test_fault_isolated_catches_exception_and_latches_after_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings_seen: list[str] = []
    monkeypatch.setattr(
        "pytest_vantage.boundary._warn",
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


# --- Unit: `liveness_isolated`, the second, non-latching-onto-`_disabled`
# decorator (design.md D29) --------------------------------------------------
#
# `fault_isolated`'s own tests above are untouched -- its name, behaviour and
# message are unchanged by the `_isolated(flag, description)` factory this
# decorator is built from. These tests prove the new path is independent in
# both directions: a liveness failure never sets `_disabled`, and the two
# flags never read or write each other's state.


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
        "pytest_vantage.boundary._warn",
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
    monkeypatch.setattr("pytest_vantage.boundary._warn", lambda config, message: None)
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


# --- Unit: `_warn`'s fallback chain ------------------------------------------


def test_warn_emits_a_vantage_warning_by_default() -> None:
    with pytest.warns(VantageWarning, match="something went wrong"):
        _warn(None, "vantage: something went wrong")  # type: ignore[arg-type]


def test_warn_falls_back_to_the_terminal_reporter_when_warnings_are_errors() -> None:
    """`filterwarnings = ["error"]` (or `-W error`) turns
    `warnings.warn(VantageWarning(...))` into a raised exception instead of
    an ordinary warning -- design.md D6 requires the message to still reach
    the user rather than let a reporting failure crash the session that
    exact way. `warnings.catch_warnings` + `simplefilter("error")` recreates
    that configuration directly, independent of how this suite's own
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
        _warn(_ConfigDouble(), "vantage: something went wrong")  # type: ignore[arg-type]

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
        _warn(_ConfigDouble(), "vantage: something went wrong")  # type: ignore[arg-type]

    assert "vantage: something went wrong" in capsys.readouterr().err


# --- RQ-37: the server cannot be reached at all (task 6.7) -------------------


@pytest.mark.req(id="RQ-37")
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


@pytest.mark.req(id="RQ-37")
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


@pytest.mark.req(id="RQ-37")
def test_recorder_is_not_registered_when_the_preflight_fails(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytest_vantage.recorder import Recorder

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    # `pytest.warns` rather than a bare call: the preflight failing here is
    # the whole point of the scenario, so asserting the warning is part of
    # the proof -- and it stops this in-process `parseconfigure` from
    # leaking a `VantageWarning` into the summary of the suite running it.
    with pytest.warns(VantageWarning, match="cannot reach"):
        config = pytester.parseconfigure("--vantage", f"--vantage-server={_closed_port_address()}")

    assert not any(isinstance(plugin, Recorder) for plugin in config.pluginmanager.get_plugins())


@pytest.mark.req(id="RQ-37")
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
    example.com would make a unit test depend on the network (RQ-28).
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


@pytest.mark.req(id="RQ-37")
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


@pytest.mark.req(id="RQ-37")
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
        # `stdin` explicitly, because `pytester.popen` otherwise opens a pipe
        # and closes it -- and on Python 3.10 `communicate()` still calls
        # `self.stdin.flush()` on that closed file, raising
        # `ValueError: flush of closed file`. Later versions guard it. Found
        # by the RQ-27 matrix on its first real CI run: this passed on 3.13
        # locally and failed on 3.10 only. `DEVNULL` leaves nothing to flush.
        stdin=subprocess.DEVNULL,
    )
    # Give the child time to pass configure (preflight included) and enter
    # the test before pulling the server out from under it.
    time.sleep(1.0)
    vantage_server.stop()
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 0
    assert (stdout.decode() + stderr.decode()).count("VantageWarning:") == 1


# --- RQ-21: something goes wrong while reporting (task 6.9) -----------------


@pytest.mark.req(id="RQ-21")
def test_reporting_error_preserves_passing_exit_status_and_warns_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
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

    # Two independent failures now, not one: the patched `send` raises for the
    # start-write in `pytest_sessionstart` as well as for the finish-write.
    # They are isolated by different decorators on purpose (design.md D29), so
    # each warns once. Only the finish-write's warning reaches `RunResult` --
    # a warning raised as early as `pytest_sessionstart` escapes an in-process
    # run's capture and surfaces in THIS session instead, which is the same
    # phenomenon `_combined_output`'s docstring records for `pytest_configure`.
    # `pytest.warns` asserts that escaping warning rather than letting it leak
    # into the suite's summary, where it would train a reader to ignore it.
    with pytest.warns(VantageWarning, match="session liveness"):
        result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert _combined_output(result).count("VantageWarning:") == 1


@pytest.mark.req(id="RQ-21")
def test_reporting_error_preserves_failing_exit_status_and_warns_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
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


@pytest.mark.req(id="RQ-21")
def test_failing_start_write_warns_once_and_the_session_still_completes(
    pytester: pytest.Pytester,
) -> None:
    """design.md D32: the start-write is `@liveness_isolated`, not
    `@fault_isolated` -- its own failure must never cost the session its
    ordinary exit status. `_StubServer` fails only its SECOND accepted
    connection (the start-write) and answers correctly from the THIRD
    onward (the finish-write): the FIRST connection is the bare TCP
    preflight, which needs no response at all to succeed.

    `runpytest_subprocess`, not in-process `runpytest`: a `VantageWarning`
    raised this early (`pytest_sessionstart`, before the first test runs)
    prints straight to the real stderr via Python's default
    `warnings.showwarning` in an in-process run -- the same reason
    `_combined_output`'s own docstring gives for `pytest_configure`'s
    preflight warnings -- so only a subprocess run's piped stderr reliably
    captures it here.
    """
    connections_seen = itertools.count(1)

    def _fail_second_connection_only(conn: socket.socket) -> None:
        connection_number = next(connections_seen)
        if connection_number < 3:
            return  # 1: the preflight (no response needed); 2: the start-write (fails)
        conn.recv(65536)
        body = b'{"run_id": "x", "status": "created", "ignored": []}'
        conn.sendall(
            b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )

    with _StubServer(_fail_second_connection_only) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert _combined_output(result).count("VantageWarning:") == 1


@pytest.mark.req(id="RQ-21")
def test_server_accepts_then_closes_without_responding(pytester: pytest.Pytester) -> None:
    with _StubServer(_accept_then_close) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    # Two warnings, not one (design.md D29): `_accept_then_close` misbehaves
    # for every connection it accepts, so both the start-write and the
    # finish-write fail against it -- one liveness warning, one reporting
    # warning. That is correct and must not collapse into one.
    assert _combined_output(result).count("VantageWarning:") == 2


@pytest.mark.req(id="RQ-21")
def test_server_accepts_and_never_answers_finishes_within_timeout_plus_five_seconds(
    pytester: pytest.Pytester,
) -> None:
    with _StubServer(_accept_and_hang) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        started = time.monotonic()
        result = pytester.runpytest_subprocess(
            "--vantage",
            f"--vantage-server={server.address}",
            "--vantage-timeout=1.0",
            timeout=15,
        )
        elapsed = time.monotonic() - started

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert elapsed < 1.0 + 5.0
    # Two warnings, not one (design.md D29) -- see
    # `test_server_accepts_then_closes_without_responding` above.
    assert _combined_output(result).count("VantageWarning:") == 2


# --- Threat matrix "Untrusted response" (task 6.11/6.12) --------------------
#
# No numbered requirement drives this row directly -- the same convention
# `test_address_validation.py` already uses for a threat-matrix test with no
# requirement of its own.


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
    # warning, never an exception" proof. Two warnings, not one (design.md
    # D29) -- see `test_server_accepts_then_closes_without_responding` above.
    assert _combined_output(result).count("VantageWarning:") == 2


def test_non_json_response_is_a_warning_not_a_crash(pytester: pytest.Pytester) -> None:
    with _StubServer(_respond_with_non_json_body) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    # Two warnings, not one (design.md D29) -- see
    # `test_server_accepts_then_closes_without_responding` above.
    assert _combined_output(result).count("VantageWarning:") == 2


def test_bare_500_response_is_a_warning_not_a_crash(pytester: pytest.Pytester) -> None:
    with _StubServer(_respond_with_bare_500) as server:
        pytester.makepyfile(test_sample=_PASSING_TEST)
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    # Two warnings, not one (design.md D29) -- see
    # `test_server_accepts_then_closes_without_responding` above.
    assert _combined_output(result).count("VantageWarning:") == 2


# --- Activity-driven beats (design.md D30, task 4.16/4.17) ------------------


@pytest.mark.req(id="RQ-21")
def test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """design.md D30: `_last_beat_at` is assigned before the send, so a
    failing send is not retried on the very next report -- and `_maybe_beat`
    is `@liveness_isolated`, which latches after its first failure. Across
    five tests (five beat opportunities, forced by a near-zero
    `_BEAT_INTERVAL_SECONDS`), that is exactly one warning, never one per
    beat -- and `accumulate` running first (D30) means every test's result
    still reaches the finish-write, whose own `send` is unpatched, so the
    real `vantage_server` ends up with all five.

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


@pytest.mark.req(id="RQ-21")
def test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total(
    pytester: pytest.Pytester,
) -> None:
    """design.md D29: the start-write and the heartbeat are both wrapped by
    `liveness_isolated`, sharing `_liveness_disabled`. When the start-write
    fails against a server that fails every connection, the liveness path
    latches before the first heartbeat is ever attempted -- forcing a beat
    opportunity on every test (via the same near-zero
    `_BEAT_INTERVAL_SECONDS` conftest trick) must not add a third warning on
    top of the one liveness warning and the one reporting warning this
    server already produces (mirrors
    `test_server_accepts_then_closes_without_responding` above, with the
    beat opportunity made real rather than merely absent by timing)."""
    pytester.makeconftest(
        "import pytest_vantage.recorder as recorder\nrecorder._BEAT_INTERVAL_SECONDS = 0.0\n"
    )
    with _StubServer(_accept_then_close) as server:
        pytester.makepyfile(
            test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(5))
        )
        result = pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")

    result.assert_outcomes(passed=5)
    assert result.ret == 0
    assert _combined_output(result).count("VantageWarning:") == 2


@pytest.mark.req(id="RQ-21")
def test_every_recorder_hook_is_fault_isolated() -> None:
    """RQ-21 says *every* hook is fault-isolated, and the two that exist are.

    Nothing else proves the rule rather than the instances: a third hook
    added later without the decorator would leave the suite green and break
    the requirement silently, because no test enumerates them. This one
    does, so the failure lands on whoever adds the hook.

    `functools.wraps` is what makes `__wrapped__` the reliable marker --
    `fault_isolated` applies it, so an undecorated hook is exactly the
    attribute that lacks it.
    """
    from pytest_vantage.recorder import Recorder

    hooks = [name for name in dir(Recorder) if name.startswith("pytest_")]

    assert hooks, "no pytest_* hooks found on Recorder -- the check would pass vacuously"
    undecorated = [name for name in hooks if not hasattr(getattr(Recorder, name), "__wrapped__")]
    assert undecorated == [], f"Recorder hooks missing @fault_isolated: {undecorated}"
