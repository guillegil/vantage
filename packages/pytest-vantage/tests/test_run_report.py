"""RQ-1 (a run entry per invocation) and RQ-31 (its timestamps), proven
end-to-end (design.md D2a): a real `vantage` server (the `vantage_server`
fixture in `vantage_test_server.py`) and a real subprocess pytest invocation
(`pytester.runpytest_subprocess`), never a mock of either side of the HTTP
boundary.

Also carries the registration test for task 6.3/6.4 (whether `Recorder` is
wired into `pytest_configure`) and the timestamp-formatting unit tests for
task 6.1/6.2 -- both prerequisites the end-to-end scenarios above depend on,
kept in this file rather than split out because `design.md`'s own file table
lists only this one new test file for D2a.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest
from vantage.core.domain.execution import Execution
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture

_PASSING_TEST = "def test_it():\n    assert True\n"
_SLOW_TEST = "import time\n\n\ndef test_slow():\n    time.sleep(5)\n"


def _wait_for_execution(server: VantageTestServer, *, timeout: float = 15.0) -> Execution:
    """Poll `server` until its first run entry has landed, or raise after
    `timeout` seconds -- a bounded observable condition, not a fixed sleep
    (task 5.1's own instruction: a fixed sleep before a signal is how a
    test becomes flaky on a loaded CI runner). Used to prove a kill or a
    liveness query happens strictly after `pytest_sessionstart`'s
    start-write has been accepted, never before it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executions = server.executions()
        if executions:
            return executions[0]
        time.sleep(0.02)
    raise TimeoutError(f"no run entry appeared within {timeout}s")


# --- Unit: fixed-width ISO-8601 timestamps (design.md D1) -------------------
#
# `datetime.isoformat()` alone omits the microsecond component when it is
# exactly zero, which would make the width variable -- lexicographic order
# would then stop being chronological order for a session that happens to
# finish on an exact second. Neither the end-to-end scenarios below nor the
# server's pydantic parsing would catch that regression (pydantic's datetime
# parser tolerates variable width), so it needs a direct, function-local
# test of its own.


def test_isoformat_utc_is_fixed_width_even_at_zero_microseconds() -> None:
    from pytest_vantage.recorder import isoformat_utc

    moment = datetime(2026, 8, 15, 9, 14, 2, 0, tzinfo=timezone.utc)

    formatted = isoformat_utc(moment)

    assert formatted == "2026-08-15T09:14:02.000000+00:00"
    assert len(formatted) == len("2026-08-15T09:14:02.481930+00:00")


def test_isoformat_utc_preserves_nonzero_microseconds() -> None:
    from pytest_vantage.recorder import isoformat_utc

    moment = datetime(2026, 8, 15, 9, 14, 2, 481930, tzinfo=timezone.utc)

    formatted = isoformat_utc(moment)

    assert formatted == "2026-08-15T09:14:02.481930+00:00"


# --- The start-write (task 2.3/2.4, design.md D32) --------------------------


@pytest.mark.req(id="RQ-1")
def test_session_start_sends_a_report_with_no_results_matching_the_finish_writes_started_at(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pytest_sessionstart` sends a report with `finished_at: null`, no
    `results`, and the session's `run_id`; `_started_at` is captured once in
    `__init__`, so the value the start-write sends is the identical value
    the finish-write sends later (design.md D32's "identical `started_at`"
    claim) -- not a second, independent `datetime.now()` call.

    `send` is patched to capture every call rather than actually perform it,
    so both requests' exact bodies are directly inspectable; the preflight
    itself still runs for real against `vantage_server`, proving activation.
    """
    sent: list[dict[str, object]] = []

    def _capture(address: str, report: dict[str, object], *, timeout: float) -> None:
        sent.append(report)

    monkeypatch.setattr("pytest_vantage.recorder.send", _capture)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert len(sent) == 2
    start_report, finish_report = sent
    assert start_report["run"]["finished_at"] is None  # type: ignore[index]
    assert "results" not in start_report
    assert finish_report["run"]["finished_at"] is not None  # type: ignore[index]
    assert start_report["run"]["id"] == finish_report["run"]["id"]  # type: ignore[index]
    assert start_report["run"]["started_at"] == finish_report["run"]["started_at"]  # type: ignore[index]


@pytest.mark.req(id="RQ-21")
def test_start_write_uses_the_liveness_timeout_not_the_report_timeout(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """design.md D31: the start-write is bounded by
    `resolve_liveness_timeout(report_timeout)`, not the finish-write's own
    `resolve_report_timeout` value -- a stall at t=0, before the first test
    runs, is the exact "slow or stuck?" failure ADR-9 names. Configuring
    `--vantage-timeout=5.0` makes the two bounds genuinely differ (`min(2.0,
    5.0) == 2.0`), so a regression that reused the report timeout for both
    requests is caught rather than accidentally passing.
    """
    timeouts: list[float] = []

    def _capture(address: str, report: dict[str, object], *, timeout: float) -> None:
        timeouts.append(timeout)

    monkeypatch.setattr("pytest_vantage.recorder.send", _capture)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-timeout=5.0"
    )

    result.assert_outcomes(passed=1)
    assert timeouts == [2.0, 5.0]


# --- Registration (task 6.3/6.4) --------------------------------------------


@pytest.mark.req(id="RQ-1")
def test_recorder_registered_only_when_vantage_flag_is_present(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """Registration tracks activation AND reachability (design.md D6).

    PR11 registered `Recorder` unconditionally once activation succeeded,
    with no reachability gate -- disclosed there as this PR's job (task
    6.8). Now that the preflight exists, `--vantage` against a genuinely
    reachable server (`vantage_server`, real) is what proves the same
    "activation implies registration" half; the inactive half is unchanged.
    `test_failure_paths.py::test_recorder_is_not_registered_when_the_preflight_fails`
    is this test's mirror image -- activation present, nothing reachable.
    """
    from pytest_vantage.recorder import Recorder

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)

    active = pytester.parseconfigure("--vantage", f"--vantage-server={vantage_server.address}")
    inactive = pytester.parseconfigure()

    assert any(isinstance(plugin, Recorder) for plugin in active.pluginmanager.get_plugins())
    assert not any(isinstance(plugin, Recorder) for plugin in inactive.pluginmanager.get_plugins())


# --- End-to-end (task 6.1, RQ-1 + RQ-31) ------------------------------------


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-31")
def test_completed_session_writes_one_row_with_ordered_timestamps(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1)
    executions = vantage_server.executions()
    assert len(executions) == 1
    (execution,) = executions
    assert execution.finished_at is not None
    assert execution.finished_at > execution.started_at


@pytest.mark.req(id="RQ-1")
def test_second_invocation_gets_a_distinct_identifier(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    pytester.makepyfile(test_sample=_PASSING_TEST)
    args = ("--vantage", f"--vantage-server={vantage_server.address}")

    pytester.runpytest_subprocess(*args).assert_outcomes(passed=1)
    pytester.runpytest_subprocess(*args).assert_outcomes(passed=1)

    executions = vantage_server.executions()
    assert len(executions) == 2
    assert executions[0].identity.value != executions[1].identity.value


@pytest.mark.req(id="RQ-1")
def test_zero_test_collection_still_writes_one_row(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    assert len(vantage_server.executions()) == 1


@pytest.mark.req(id="RQ-1")
def test_failed_collection_still_writes_one_row(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    pytester.makepyfile(test_broken="import this_module_does_not_exist_anywhere_at_all\n")

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    # The precondition, asserted rather than assumed: without it this passes
    # just as well against a session that collected cleanly, and the scenario
    # is specifically about the one that did not.
    assert result.ret == pytest.ExitCode.INTERRUPTED
    assert len(vantage_server.executions()) == 1


@pytest.mark.req(id="RQ-31")
def test_sigint_leaves_start_time_and_null_end_time(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """`pytest`'s `wrap_session` calls `pytest_sessionfinish` from a
    `finally` with `ExitCode.INTERRUPTED` (design.md D7) -- the report IS
    sent, with `finished_at` null and `interrupted` true. Needs a raw
    `Popen` (`pytester.popen`, not `runpytest_subprocess`) because the
    signal has to be delivered to a still-running child process.
    """
    pytester.makepyfile(test_slow=("import time\n\n\ndef test_slow():\n    time.sleep(5)\n"))

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
    # Give the child time to pass configure/collection and enter the sleep
    # before interrupting it -- interrupting too early (still in configure)
    # would not exercise `wrap_session`'s interrupted-session path at all.
    time.sleep(1.0)
    process.send_signal(signal.SIGINT)
    process.wait(timeout=15)

    executions = vantage_server.executions()
    assert len(executions) == 1
    (execution,) = executions
    assert execution.finished_at is None
    assert execution.interrupted is True


# --- The scenarios the task list forgot (Phase 5) ---------------------------


@pytest.mark.req(id="RQ-1")
def test_a_still_running_session_already_has_a_run_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """`run-recording`'s "A still-running session already has a run entry"
    (RQ-1.5): the database is queried WHILE the child is still executing,
    before it finishes or is killed -- proving the row exists because of
    the start-write alone, not merely that it exists once the session is
    over, which every other RQ-1 test in this file already proves.

    Needs a raw `Popen` (`pytester.popen`), not `runpytest_subprocess`,
    because the child must still be alive when the server is queried.
    `stdin=subprocess.DEVNULL` for the same 3.10 `communicate()` reason
    `test_sigint_leaves_start_time_and_null_end_time` above carries.
    """
    pytester.makepyfile(test_slow=_SLOW_TEST)

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
        stdin=subprocess.DEVNULL,
    )
    try:
        execution = _wait_for_execution(vantage_server)

        # The assertion of record: the child is still executing its 5-second
        # test when the row is observed.
        assert process.poll() is None
        assert execution.started_at is not None
        assert execution.finished_at is None
    finally:
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=15)


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-31")
def test_sigkilled_session_leaves_a_start_time_null_end_time_and_no_interrupt_reason(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """`run-recording`'s "A SIGKILL'd session's entry is present" (RQ-1.6)
    and `run-recording`'s "SIGKILL'd session carries no interrupt reason"
    (RQ-31.3), asserted together to make the contrast legible: SIGINT
    (`test_sigint_leaves_start_time_and_null_end_time` above) is a signal
    Python observes, so that entry carries `interrupted=True`. SIGKILL
    cannot be caught, blocked or handled at all -- the process stops
    between two instructions, and no code of this project ever runs to
    record a reason. This test proves the negative directly: the run entry
    the start-write already left behind stays exactly as it was, holding a
    start time and a null end time, `interrupted=False`, and no
    `interrupt_reason`.

    SIGKILL, never SIGTERM or SIGINT, sent only after
    `_wait_for_execution` confirms the start-write has already landed --
    proving the kill happens after `pytest_sessionstart`, not before it.
    """
    pytester.makepyfile(test_slow=_SLOW_TEST)

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
        stdin=subprocess.DEVNULL,
    )
    _wait_for_execution(vantage_server)

    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=15)

    executions = vantage_server.executions()
    assert len(executions) == 1
    (execution,) = executions
    assert execution.started_at is not None
    assert execution.finished_at is None
    assert execution.interrupted is False
    assert execution.interrupt_reason is None


# --- Activity-driven beats (design.md D30, task 4.18) -----------------------


@pytest.mark.req(id="RQ-25")
def test_a_suite_exceeding_one_heartbeat_interval_sends_at_least_one_heartbeat(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`session-liveness`'s "A long suite's last contact advances during
    execution" scenario -- `_BEAT_INTERVAL_SECONDS` shrunk to make every
    `pytest_runtest_logreport` call a beat opportunity, proving the wiring
    fires for real rather than merely never being due within the suite's
    real wall-clock duration."""
    beats: list[str] = []

    def _capture(address: str, run_id: str, *, timeout: float) -> None:
        beats.append(run_id)

    monkeypatch.setattr("pytest_vantage.recorder.send_heartbeat", _capture)
    monkeypatch.setattr("pytest_vantage.recorder._BEAT_INTERVAL_SECONDS", 0.0)
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(3))
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=3)
    assert len(beats) >= 1


@pytest.mark.req(id="RQ-25")
def test_a_suite_exceeding_one_heartbeat_interval_advances_the_servers_last_contact(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`session-liveness`'s "A long suite's last contact advances during
    execution" scenario, exercised for real: `send_heartbeat` is left
    UNPATCHED, so the whole chain -- the plugin decides to beat, POSTs over
    real HTTP, the route calls `touch_last_contact` -- runs end to end
    against `vantage_server`, closing the gap the test above leaves open (it
    monkeypatches `send_heartbeat` itself away and only ever observes its
    own stub list, never the server).

    Only `_BEAT_INTERVAL_SECONDS` is driven down here, the same technique
    the test above already uses -- the production default (30.0) is never
    touched, so a suite still "exceeds one heartbeat interval" without
    sleeping through 30 real seconds.

    The server's own `touch_last_contact` is wrapped, not replaced: the real
    implementation still runs on every call, so `last_contact_at` is genuine
    store state, not a captured client-side call. The wrapper's only job is
    to read the store's value for this run BEFORE the first real heartbeat
    overwrites it -- exactly the value the start-write alone recorded --
    so the final assertion compares two real reads of the store, not a
    stub's argument list. If the wire between `send_heartbeat` and this
    route breaks (wrong path, wrong method, wrong run id), the wrapped
    method is never called at all and `baseline_by_run` stays empty.

    Verified by mutation (task 6.1): breaking
    `transport._HEARTBEAT_PATH_SUFFIX` to `/HEARTBEAT-TYPO` makes this test
    fail; reverting restores green. See `apply-progress` for both runs.
    """
    monkeypatch.setattr("pytest_vantage.recorder._BEAT_INTERVAL_SECONDS", 0.0)

    baseline_by_run: dict[str, datetime] = {}
    real_touch_last_contact = vantage_server.store.touch_last_contact

    def _spy_touch_last_contact(execution_id: str, contacted_at: datetime) -> bool:
        if execution_id not in baseline_by_run:
            # The value `_last_contact` holds right now is exactly what the
            # start-write's insert branch set (design.md D27) -- nothing
            # else can have touched it before this, the first real
            # heartbeat call this run has received.
            baseline_by_run[execution_id] = vantage_server.store._last_contact[  # noqa: SLF001
                execution_id
            ]
        return real_touch_last_contact(execution_id, contacted_at)

    monkeypatch.setattr(vantage_server.store, "touch_last_contact", _spy_touch_last_contact)
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(3))
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=3)
    execution = _wait_for_execution(vantage_server)
    run_id = execution.identity.value
    assert run_id in baseline_by_run, (
        "no heartbeat ever reached the server's touch_last_contact -- "
        "the plugin -> HTTP -> route -> store chain never completed"
    )
    last_contact_at = vantage_server.store._last_contact[run_id]  # noqa: SLF001
    assert last_contact_at > baseline_by_run[run_id]


@pytest.mark.req(id="RQ-25")
def test_a_fast_suite_emits_no_heartbeat(
    monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    """RQ-25's measured profile -- 1,000 tests at ~10 ms each is a
    ~10-second suite, comfortably inside one `_BEAT_INTERVAL_SECONDS`
    (30.0) window. Proven directly against `Recorder._maybe_beat`'s timing
    guard, called 1,000 times in a tight loop right after construction,
    rather than spawning 1,000 real subprocess tests -- the real elapsed
    wall-clock time for that loop is a small fraction of a second, well
    under the interval, exactly like RQ-25's own measured suite.

    `_maybe_beat` is wrapped in `@liveness_isolated` (design.md D30), which
    swallows whatever the beat path raises and turns it into a
    `VantageWarning` instead of propagating -- so `beats == []` alone
    cannot distinguish "correctly suppressed, no attempt made" from "the
    timing guard is broken and every attempt raised, silently, latched
    after the first failure" (W3). Asserting the suite stayed warning-free
    as well closes that gap: deleting `_last_beat_at` leaves `beats == []`
    unchanged but emits exactly one `VantageWarning` -- confirmed by
    reverting the fix and observing this assertion, and only this one,
    fail.
    """
    from pytest_vantage.recorder import Recorder

    beats: list[str] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send_heartbeat",
        lambda *args, **kwargs: beats.append("beat"),
    )

    class _ConfigDouble:
        def getoption(self, name: str, default: object = None) -> object:
            return None

    recorder = Recorder(_ConfigDouble(), "http://127.0.0.1:1", 1.0)  # type: ignore[arg-type]
    for _ in range(1000):
        recorder._maybe_beat()

    assert beats == []
    assert len(recwarn) == 0, [str(w.message) for w in recwarn.list]


# --- End-to-end xdist (task 6.5, RQ-1 + RQ-27) ------------------------------


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-27")
def test_xdist_run_leaves_exactly_one_run_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """Ties 5.3's unit-level xdist guard to a real `-n 4` subprocess run:
    four workers plus the controller all execute `pytest_configure`, and
    only the controller may end up with a registered `Recorder`.

    RQ-27's "without xdist" matrix leg installs no `pytest-xdist` at all, so
    `-n 4` is not a recognised pytest option in that environment. This test
    is specifically about xdist's dedup behaviour and has nothing to assert
    when xdist is absent; skip rather than fail so PR13's CI matrix reports
    an honest "not applicable" instead of a false negative.
    """
    pytest.importorskip("xdist")
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(8))
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "4"
    )

    result.assert_outcomes(passed=8)
    assert len(vantage_server.executions()) == 1
