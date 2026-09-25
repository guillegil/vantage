"""Recording a run: one run entry per invocation, with its timestamps.

The end-to-end tests use a real `vantage` server (the `vantage_server`
fixture in `vantage_test_server.py`) and a real subprocess pytest
invocation, never a mock of either side of the HTTP boundary. The file also
covers the start-write, `Recorder` registration, VCS and metadata wiring,
heartbeats, the report budget and timestamp formatting.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_vantage import vcs
from pytest_vantage.recorder import Recorder
from vantage.core.domain.execution import Execution
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture

_PASSING_TEST = "def test_it():\n    assert True\n"
_SLOW_TEST = "import time\n\n\ndef test_slow():\n    time.sleep(5)\n"


def _wait_for_execution(server: VantageTestServer, *, timeout: float = 15.0) -> Execution:
    """Poll `server` until its first run entry has landed, or raise after
    `timeout` seconds. A bounded wait on an observable condition rather than
    a fixed sleep, which is flaky on a loaded CI runner. Used to act only
    after `pytest_sessionstart`'s start-write has been accepted.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executions = server.executions()
        if executions:
            return executions[0]
        time.sleep(0.02)
    raise TimeoutError(f"no run entry appeared within {timeout}s")


def _wait_for_file(path: Path, *, timeout: float = 15.0) -> None:
    """Poll until `path` exists, or raise after `timeout` seconds: the same
    bounded wait as `_wait_for_execution`, on a marker the child writes."""
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} did not appear within {timeout}s")
        time.sleep(0.01)


class _ConfigDouble:
    rootpath = "unused"

    def getoption(self, name: str, default: object = None) -> object:
        return None


def _offline_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Recorder, list[dict[str, object]]]:
    """A `Recorder` driven directly, hook by hook, whose every report is
    captured instead of sent. VCS capture is neutralised rather than
    spawning `git` for something these tests do not exercise.
    """
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send", lambda address, report, *, timeout: sent.append(report)
    )
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())
    recorder = Recorder(
        _ConfigDouble(),  # type: ignore[arg-type]
        "http://127.0.0.1:1",
        1.0,
        lifecycle_available=True,
    )
    return recorder, sent


# --- Unit: fixed-width ISO-8601 timestamps ----------------------------------
#
# A variable-width timestamp breaks lexicographic ordering. Neither the
# end-to-end tests nor the server's pydantic parsing (which tolerates variable
# width) would catch that regression, so it is tested directly.


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


# --- The start-write -------------------------------------------------------


def test_session_start_sends_a_report_with_no_results_matching_the_finish_writes_started_at(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pytest_sessionstart` sends a report with `finished_at: null`, no
    `results`, and the session's `run_id`; `_started_at` is captured once in
    `__init__`, so the value the start-write sends is the identical value
    the finish-write sends later -- not a second, independent
    `datetime.now()` call.

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


def test_start_write_uses_the_liveness_timeout_not_the_report_timeout(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start-write is bounded by `resolve_liveness_timeout(report_timeout)`,
    not the finish-write's report timeout: a stall before the first test
    runs must stay short. `--vantage-timeout=5.0` makes the two bounds differ
    (`min(2.0, 5.0) == 2.0`), so reusing the report timeout for both
    requests is caught.
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


# --- Registration ----------------------------------------------------------


def test_recorder_registered_only_when_vantage_flag_is_present(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """Registration requires activation and a reachable server: `--vantage`
    against the real `vantage_server` registers a `Recorder`, no flag
    registers none. The mirror image, activation with nothing reachable, is
    `test_failure_paths.py::test_recorder_is_not_registered_when_the_preflight_fails`.
    """
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)

    active = pytester.parseconfigure("--vantage", f"--vantage-server={vantage_server.address}")
    inactive = pytester.parseconfigure()

    assert any(isinstance(plugin, Recorder) for plugin in active.pluginmanager.get_plugins())
    assert not any(isinstance(plugin, Recorder) for plugin in inactive.pluginmanager.get_plugins())


# --- VCS and metadata wiring ------------------------------------------------


def _corrupt_git_repo(rootpath: Path) -> None:
    """A `.git` directory with a truncated `HEAD` and no objects: not a mock,
    a real repository `git` itself cannot read (it exits 128). Same shape as
    `test_vcs.py::test_corrupt_git_entry_records_nulls_and_warns_once`."""
    (rootpath / ".git").mkdir(parents=True)
    (rootpath / ".git" / "HEAD").write_text("ref: ")


def test_vcs_section_is_identical_on_both_reports(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The snapshot is captured once, in `__init__`, and never re-read, so
    the start report and the finish report serialise the identical snapshot.
    `vcs.capture` is patched to return a different snapshot on every call; a
    re-read at finish time would make the two reports disagree.
    """
    call_count = [0]

    def _fake_capture(rootpath: Path) -> vcs.VcsSnapshot:
        call_count[0] += 1
        return vcs.VcsSnapshot(
            commit=f"commit-{call_count[0]}",
            branch="main",
            commit_subject="a message",
            dirty=call_count[0] % 2 == 0,
            root=str(rootpath),
        )

    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", _fake_capture)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout: sent.append(report),
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert len(sent) == 2
    start_report, finish_report = sent
    assert start_report["vcs"] == finish_report["vcs"]
    # The FIRST capture, never a later one -- proves it was not re-read.
    assert start_report["vcs"]["commit"] == "commit-1"  # type: ignore[index]
    assert call_count[0] == 1


def test_metadata_section_is_identical_on_both_reports(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The metadata section is captured once, in `__init__`, and never
    re-read, so both reports serialise the identical section.
    `capture_metadata` is patched to return a different section on every
    call; a re-read at finish time would make the two reports disagree.
    `call_count[0] == 1` also shows the declaration and the files it names
    are read once per session, never once per test.
    """
    from pytest_vantage import metadata as metadata_module

    call_count = [0]

    def _fake_capture(config: pytest.Config, rootpath: Path) -> metadata_module.MetadataSection:
        call_count[0] += 1
        return metadata_module.MetadataSection(
            declaration=metadata_module.DECLARATION_FILENAME,
            files=(
                metadata_module.CapturedFile(
                    path="f.json",
                    format="json",
                    status="captured",
                    keys=("k",),
                    content=f"call-{call_count[0]}",
                ),
            ),
        )

    monkeypatch.setattr("pytest_vantage.recorder.metadata.capture_metadata", _fake_capture)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout: sent.append(report),
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)
    (pytester.path / "vantage-metadata.json").write_text(
        json.dumps({"version": 1, "files": [{"path": "f.json", "format": "json", "keys": ["k"]}]})
    )

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-metadata"
    )

    result.assert_outcomes(passed=1)
    assert len(sent) == 2
    start_report, finish_report = sent
    assert start_report["metadata"] == finish_report["metadata"]
    # The FIRST capture, never a later one -- proves it was not re-read.
    assert start_report["metadata"]["files"][0]["content"] == "call-1"  # type: ignore[index]
    assert call_count[0] == 1


def test_no_metadata_section_when_capture_was_not_requested(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `metadata` wire key is absent entirely -- not `null` -- on both
    reports when `--vantage-metadata` was never passed, the same
    additive-section shape `vcs`/`results` already establish."""
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout: sent.append(report),
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert len(sent) == 2
    start_report, finish_report = sent
    assert "metadata" not in start_report
    assert "metadata" not in finish_report


def test_passing_suite_exit_status_survives_unreadable_repository(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    _corrupt_git_repo(pytester.path)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1)
    assert result.ret == 0
    # The corrupt repository is actually read: it triggers exactly one
    # warning, which a vacuous pass (git never invoked) would not.
    output = result.stdout.str() + result.stderr.str()
    assert output.count("VantageWarning:") == 1
    assert "could not read the git repository" in output


def test_failing_suite_exit_status_survives_unreadable_repository(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    _corrupt_git_repo(pytester.path)
    pytester.makepyfile(test_sample="def test_it():\n    assert False\n")

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(failed=1)
    assert result.ret == 1
    output = result.stdout.str() + result.stderr.str()
    assert output.count("VantageWarning:") == 1
    assert "could not read the git repository" in output


def test_git_invocation_count_does_not_scale_with_test_count(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VCS capture runs once per session, not once per test: a session with
    five tests spawns exactly as many `git` processes as a session with one.
    `vcs.py` itself caps a single `capture()` at five invocations.
    """
    real_run = subprocess.run
    calls: list[list[str]] = []

    def _counting_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "git":
            calls.append(list(argv))
        return real_run(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr("pytest_vantage.vcs.subprocess.run", _counting_run)

    pytester.makepyfile(test_one="def test_a():\n    assert True\n")
    pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}").assert_outcomes(
        passed=1
    )
    after_one_test = len(calls)
    assert 0 < after_one_test <= 5

    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(5))
    )
    pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}").assert_outcomes(
        passed=6
    )  # test_one's test is still collected too

    # A second, independent session -- five more tests -- adds exactly one
    # more session's worth of `git` calls, never five more.
    assert len(calls) - after_one_test == after_one_test


# --- End-to-end: one run entry per invocation ------------------------------


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


def test_failure_text_reaches_the_server_field_for_field(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """The failure text the plugin captures is what the server stores,
    read back from it: the server tolerates result keys it does not know,
    so a field named differently on either side would be dropped with the
    session still recorded, and only reading it back shows the loss.
    """
    pytester.makepyfile(
        test_sample=(
            "def test_it():\n    print('OUTPUT-MARKER')\n    assert 3 == 4, 'MESSAGE-MARKER'\n"
        )
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result.assert_outcomes(failed=1)
    (stored,) = vantage_server.results()
    failure = stored.failure
    assert failure is not None
    assert failure.failure_type == "AssertionError"
    assert failure.failure_message is not None
    assert "MESSAGE-MARKER" in failure.failure_message
    assert failure.failure_path is not None
    assert failure.failure_path.endswith("test_sample.py")
    assert failure.failure_lineno == 3
    assert failure.failure_repr is not None
    assert failure.traceback is not None
    assert "MESSAGE-MARKER" in failure.traceback
    assert stored.captured.stdout is not None
    assert "OUTPUT-MARKER" in stored.captured.stdout
    assert stored.captured.stderr is not None


def test_zero_test_collection_still_writes_one_row(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    assert len(vantage_server.executions()) == 1


def test_failed_collection_still_writes_one_row(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    pytester.makepyfile(test_broken="import this_module_does_not_exist_anywhere_at_all\n")

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    # The precondition, asserted rather than assumed: without it this passes
    # just as well against a session that collected cleanly, and this test
    # is specifically about the one that did not.
    assert result.ret == pytest.ExitCode.INTERRUPTED
    (execution,) = vantage_server.executions()
    # pytest stopped the session on purpose, in order: it finished, it was
    # not interrupted, and the reason says why it stopped.
    assert execution.finished_at is not None
    assert execution.interrupted is False
    assert execution.interrupt_reason == "1 error during collection"


@pytest.mark.parametrize(
    ("args", "test_body", "finished", "interrupted", "reason"),
    [
        pytest.param(
            ("--stepwise",),
            "def test_a():\n    assert False\n\n\ndef test_b():\n    assert True\n",
            True,
            False,
            "Test failed, continuing from this test next run.",
            id="stepwise-stop",
        ),
        pytest.param(
            (),
            "import pytest\n\n\ndef test_a():\n    pytest.exit('stop here')\n",
            False,
            True,
            "stop here",
            id="pytest-exit",
        ),
    ],
)
def test_exit_status_two_is_recorded_by_what_stopped_the_session(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    args: tuple[str, ...],
    test_body: str,
    finished: bool,
    interrupted: bool,
    reason: str,
) -> None:
    """pytest ends with exit status 2 both when it stops a session on
    purpose and when the session is interrupted. `--stepwise` stopping at a
    failure ran to an orderly end; `pytest.exit()` cut the session short.
    """
    pytester.makepyfile(test_sample=test_body)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", *args
    )

    assert result.ret == pytest.ExitCode.INTERRUPTED
    (execution,) = vantage_server.executions()
    assert (execution.finished_at is not None) is finished
    assert execution.interrupted is interrupted
    assert execution.interrupt_reason == reason


def _raised(exception: BaseException) -> pytest.ExceptionInfo[BaseException]:
    try:
        raise exception
    except BaseException:
        return pytest.ExceptionInfo.from_current()


@pytest.mark.parametrize(
    ("exit_status", "stop", "finished", "interrupted", "reason"),
    [
        pytest.param(0, None, True, False, None, id="passed"),
        pytest.param(1, None, True, False, None, id="failed"),
        pytest.param(
            2,
            pytest.Session.Interrupted("1 error during collection"),
            True,
            False,
            "1 error during collection",
            id="collection-errors",
        ),
        pytest.param(2, KeyboardInterrupt(), False, True, None, id="ctrl-c"),
        pytest.param(
            2, pytest.exit.Exception("stop here"), False, True, "stop here", id="pytest-exit"
        ),
        pytest.param(
            0,
            pytest.exit.Exception("done early", returncode=0),
            False,
            True,
            "done early",
            id="pytest-exit-with-a-return-code",
        ),
        pytest.param(3, None, False, False, None, id="internal-error"),
    ],
)
def test_the_finish_report_records_how_the_session_ended(
    monkeypatch: pytest.MonkeyPatch,
    exit_status: int,
    stop: BaseException | None,
    finished: bool,
    interrupted: bool,
    reason: str | None,
) -> None:
    """Only a real interruption -- Ctrl-C or `pytest.exit()` -- is recorded
    as interrupted with no finish time. pytest's internal error has no
    orderly finish either, but nothing interrupted it. Every other ending
    has a finish time, whatever its exit status.
    """
    recorder, sent = _offline_recorder(monkeypatch)
    if stop is not None:
        recorder.pytest_keyboard_interrupt(excinfo=_raised(stop))

    recorder.pytest_sessionfinish(exitstatus=exit_status)

    run = sent[-1]["run"]
    assert run["exit_status"] == exit_status  # type: ignore[index]
    assert (run["finished_at"] is not None) is finished  # type: ignore[index]
    assert run["interrupted"] is interrupted  # type: ignore[index]
    assert run["interrupt_reason"] == reason  # type: ignore[index]


def test_sigint_leaves_start_time_and_null_end_time(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """`pytest`'s `wrap_session` calls `pytest_sessionfinish` from a
    `finally` with `ExitCode.INTERRUPTED`, so the report is still sent, with
    `finished_at` null and `interrupted` true. Needs a raw `Popen`
    (`pytester.popen`, not `runpytest_subprocess`) because the signal has to
    be delivered to a still-running child process.

    The signal goes only once the child's test has started. Earlier, it
    would land in configure or inside the start-write, where pytest never
    calls `pytest_sessionfinish` at all -- and the start row is stored before
    the start-write returns, so its appearance is no signal that it has.
    """
    entered = pytester.path / "entered"
    pytester.makepyfile(
        test_slow=(
            "import pathlib\nimport time\n\n\n"
            "def test_slow():\n"
            f"    pathlib.Path({str(entered)!r}).touch()\n"
            "    time.sleep(30)\n"
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        _wait_for_file(entered)
        process.send_signal(signal.SIGINT)
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    executions = vantage_server.executions()
    assert len(executions) == 1
    (execution,) = executions
    assert execution.finished_at is None
    assert execution.interrupted is True


# --- Sessions that have not finished ---------------------------------------


def test_sigkilled_session_leaves_a_start_time_null_end_time_and_no_interrupt_reason(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """A still-running session already has a run entry, from the start-write
    alone: the row is observed while the child is still executing its
    5-second test. SIGKILL cannot be caught, so no plugin code runs after
    it, and the row stays exactly as the start-write left it -- a start
    time, a null end time, `interrupted=False` and no `interrupt_reason`.
    Contrast SIGINT (`test_sigint_leaves_start_time_and_null_end_time`),
    which Python observes and which yields `interrupted=True`.
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        _wait_for_execution(vantage_server)
        assert process.poll() is None
    finally:
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=15)

    executions = vantage_server.executions()
    assert len(executions) == 1
    (execution,) = executions
    assert execution.started_at is not None
    assert execution.finished_at is None
    assert execution.interrupted is False
    assert execution.interrupt_reason is None


# --- Activity-driven heartbeats --------------------------------------------


def test_a_suite_exceeding_one_heartbeat_interval_advances_the_servers_last_contact(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's last-contact time advances during a long suite, end to
    end: `send_heartbeat` is left unpatched, so the plugin POSTs over real
    HTTP and the route calls `touch_last_contact` on `vantage_server`. Only
    `_BEAT_INTERVAL_SECONDS` is driven down, so the test does not sleep
    through 30 real seconds.

    The store's `touch_last_contact` is wrapped, not replaced: the wrapper
    only records the value the start-write set, before the first heartbeat
    overwrites it, so the final assertion compares two real reads of the
    store. If the path, method or run id on the wire is wrong, the wrapper
    is never called and `baseline_by_run` stays empty.
    """
    monkeypatch.setattr("pytest_vantage.recorder._BEAT_INTERVAL_SECONDS", 0.0)

    baseline_by_run: dict[str, datetime] = {}
    real_touch_last_contact = vantage_server.store.touch_last_contact

    def _spy_touch_last_contact(execution_id: str, contacted_at: datetime) -> bool:
        if execution_id not in baseline_by_run:
            # Before this run's first heartbeat, `_last_contact` holds
            # exactly what the start-write set.
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


def test_a_fast_suite_emits_no_heartbeat(
    monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    """1,000 tests at ~10 ms each is a ~10-second suite, well inside one
    30-second `_BEAT_INTERVAL_SECONDS`. Proven directly against
    `Recorder._maybe_beat`'s timing guard, called 1,000 times right after
    construction, rather than by running 1,000 real tests.

    `_maybe_beat` is `@liveness_isolated`, which turns an exception into a
    `VantageWarning`, so `beats == []` alone cannot tell "correctly
    suppressed" from "the first attempt raised and latched". Asserting that
    no warning was emitted closes that gap.
    """
    beats: list[str] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send_heartbeat",
        lambda *args, **kwargs: beats.append("beat"),
    )
    recorder, _sent = _offline_recorder(monkeypatch)
    for _ in range(1000):
        recorder._maybe_beat()

    assert beats == []
    assert len(recwarn) == 0, [str(w.message) for w in recwarn.list]


# --- End-to-end xdist ------------------------------------------------------


def test_xdist_run_leaves_exactly_one_run_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """A real `-n 4` run: four workers plus the controller all execute
    `pytest_configure`, and only the controller registers a `Recorder`, so
    exactly one run entry is written.

    Skipped when `pytest-xdist` is not installed (the CI leg without it),
    where `-n` is not a recognised option.
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


def test_an_xdist_maxfail_stop_is_recorded_as_finished(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """Under xdist, `-x` stops the session through xdist's own
    `KeyboardInterrupt` subclass, so it ends with exit status 2 where the
    same run without xdist ends with 1. Either way pytest stopped on
    purpose: the run finished and was not interrupted.
    """
    pytest.importorskip("xdist")
    pytester.makepyfile(
        test_many="def test_fails():\n    assert False\n\n\n"
        + "\n".join(f"def test_{i}():\n    assert True\n" for i in range(4))
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "2", "-x"
    )

    assert result.ret == pytest.ExitCode.INTERRUPTED
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert execution.interrupted is False
