"""Recording a run: one run entry per invocation, with its timestamps.

The end-to-end tests use a real `vantage` server (the `vantage_server`
fixture in `vantage_test_server.py`) and a real subprocess pytest
invocation, never a mock of either side of the HTTP boundary. The file also
covers the start-write, `Recorder` registration, VCS and metadata wiring,
heartbeats and how the session ended.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import budget, transport, vcs
from pytest_vantage.config import DEFAULT_PROJECT
from pytest_vantage.recorder import _WORKER_INTERRUPT_KEY, Recorder, WorkerInterruptRelay
from vantage_test_server import (
    VantageTestServer,
    wait_for_execution,
    wait_for_file,
)
from warnings_summary import vantage_warnings

# Several tests build a broken repository in their project directory; git
# must not find one above it instead.
pytestmark = pytest.mark.usefixtures("git_confined_to_basetemp")

_PASSING_TEST = "def test_it():\n    assert True\n"
_SLOW_TEST = "import time\n\n\ndef test_slow():\n    time.sleep(5)\n"


class _ConfigDouble:
    rootpath = "unused"

    def getoption(self, name: str, default: object = None) -> object:
        return None


def _offline_recorder(
    monkeypatch: pytest.MonkeyPatch, *, project: str = DEFAULT_PROJECT
) -> tuple[Recorder, list[dict[str, object]]]:
    """A `Recorder` driven directly, hook by hook, whose every report is
    captured instead of sent. VCS capture is neutralised rather than
    spawning `git` for something these tests do not exercise.
    """
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout, token=None: sent.append(report),
    )
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())
    recorder = Recorder(
        _ConfigDouble(),  # type: ignore[arg-type]
        "http://127.0.0.1:1",
        1.0,
        lifecycle_available=True,
        project=project,
    )
    return recorder, sent


# --- The start-write -------------------------------------------------------


def test_session_start_sends_a_report_with_no_results_matching_the_finish_writes_started_at(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
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

    def _capture(
        address: str, report: dict[str, object], *, timeout: float, token: str | None = None
    ) -> None:
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
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start-write is bounded by `resolve_liveness_timeout(report_timeout)`,
    not the finish-write's report timeout: a stall before the first test
    runs must stay short. `--vantage-timeout=5.0` makes the two bounds differ
    (`min(2.0, 5.0) == 2.0`), so reusing the report timeout for both
    requests is caught.
    """
    timeouts: list[float] = []

    def _capture(
        address: str, report: dict[str, object], *, timeout: float, token: str | None = None
    ) -> None:
        timeouts.append(timeout)

    monkeypatch.setattr("pytest_vantage.recorder.send", _capture)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-timeout=5.0"
    )

    result.assert_outcomes(passed=1)
    assert timeouts == [2.0, 5.0]


# --- The project ---------------------------------------------------------------


def test_every_report_of_a_run_names_its_project_and_the_header_says_which(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start report, each in-progress report carrying a slice of the
    results and the finishing report all name the project: whichever one
    reaches the server first creates the run, so each must say where it
    goes. A small cap splits the results over several reports; `send` is
    spied on and still delivers, so the server files the run as told."""
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
    monkeypatch.setattr(budget, "_REPORT_BYTES_CAP", 4_000)
    vantage_server.add_project("foo")
    sent: list[dict[str, object]] = []
    real_send = transport.send

    def _spy(
        address: str, report: dict[str, object], *, timeout: float, token: str | None = None
    ) -> None:
        sent.append(report)
        real_send(address, report, timeout=timeout, token=token)

    monkeypatch.setattr("pytest_vantage.recorder.send", _spy)
    pytester.makepyfile(
        test_many="""
import pytest


@pytest.mark.parametrize("n", range(30))
def test_p(n):
    assert True
"""
    )

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-project=foo"
    )

    result.assert_outcomes(passed=30)
    start, *slices, finish = sent
    assert slices, "the cap did not split the results"
    assert "results" not in start
    assert all(report["run"]["finished_at"] is None for report in slices)  # type: ignore[index]
    assert finish["run"]["finished_at"] is not None  # type: ignore[index]
    assert [report["project"] for report in sent] == ["foo"] * len(sent)
    run_id = start["run"]["id"]  # type: ignore[index]
    assert vantage_server.project_of(run_id) == "foo"
    assert len(vantage_server.results()) == 30
    result.stdout.fnmatch_lines(
        [f"vantage: recording run {run_id} in project foo to {vantage_server.address}"]
    )


def test_a_run_naming_no_project_says_so_and_goes_to_default(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`default` is named like any other project, in the header and in the
    report, rather than left for the server to assume."""
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    (execution,) = vantage_server.executions()
    run_id = execution.identity.value
    assert vantage_server.project_of(run_id) == DEFAULT_PROJECT
    result.stdout.fnmatch_lines(
        [f"vantage: recording run {run_id} in project default to {vantage_server.address}"]
    )


def _passed(node_id: str, when: str) -> pytest.TestReport:
    return pytest.TestReport(
        nodeid=node_id,
        location=("test_budget.py", 0, node_id),
        keywords={},
        outcome="passed",
        longrepr=None,
        when=when,  # type: ignore[arg-type]
    )


def test_the_longest_project_name_is_counted_against_the_report_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 64-character name costs every report some eighty bytes. The split
    must count them: over one result's cost of consecutive caps, every
    amount of room a full slice can leave comes round once, including the
    ones too small for the name, so a budget that forgot it overshoots
    somewhere in the sweep. Every report must fit, and together carry
    every result."""
    project = "p" * 64
    node_ids = [f"test_budget.py::test_{index:02d}" for index in range(12)]
    first_cap = 2_000
    probe, probe_sent = _offline_recorder(monkeypatch, project=project)
    for node_id in node_ids:
        for when in ("setup", "call", "teardown"):
            probe.pytest_runtest_logreport(_passed(node_id, when))
    probe.pytest_sessionfinish(session=_A_SESSION_RUN_TO_ITS_END, exitstatus=0)  # type: ignore[arg-type]
    (whole,) = probe_sent
    one_result = len(json.dumps(whole["results"][0]).encode()) + len(", ")  # type: ignore[index]

    for cap in range(first_cap, first_cap + one_result):
        monkeypatch.setattr(budget, "_REPORT_BYTES_CAP", cap)
        recorder, sent = _offline_recorder(monkeypatch, project=project)
        for node_id in node_ids:
            for when in ("setup", "call", "teardown"):
                recorder.pytest_runtest_logreport(_passed(node_id, when))

        recorder.pytest_sessionfinish(session=_A_SESSION_RUN_TO_ITS_END, exitstatus=0)  # type: ignore[arg-type]

        assert len(sent) > 1, cap
        assert all(report["project"] == project for report in sent), cap
        sizes = [len(json.dumps(report).encode()) for report in sent]
        assert max(sizes) <= cap, (cap, sizes)
        carried = [entry["node_id"] for report in sent for entry in report["results"]]  # type: ignore[attr-defined]
        assert carried == node_ids, cap


# --- Registration ----------------------------------------------------------


def test_recorder_registered_only_when_vantage_flag_is_present(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    vantage_server: VantageTestServer,
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
    a real repository `git` itself cannot read (it exits 128). The
    `truncated-head` kind of
    `test_vcs.py::test_corrupt_repository_records_nulls_and_warns_once`."""
    (rootpath / ".git").mkdir(parents=True)
    (rootpath / ".git" / "HEAD").write_text("ref: ")


def test_vcs_section_is_identical_on_both_reports(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
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
        lambda address, report, *, timeout, token=None: sent.append(report),
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
    vantage_server: VantageTestServer,
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

    def _fake_capture(
        config: pytest.Config, rootpath: Path, *, read_files: bool
    ) -> metadata_module.MetadataSection:
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
        lambda address, report, *, timeout, token=None: sent.append(report),
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


def test_declared_keys_are_sent_under_vantage_alone_and_no_file_is_read(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--vantage` alone reads the declaration for the keys it declares,
    and every report carries them; the files it names are neither read nor
    sent without `--vantage-metadata`. A declared key the session never
    reported is sent `absent` in the last report."""
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout, token=None: sent.append(report),
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)
    (pytester.path / "settings.json").write_text('{"region": "eu-west-1"}')
    keys = {"fpga.firmware": {"name": "FPGA firmware version"}}
    (pytester.path / "vantage-metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "keys": keys,
                "files": [{"path": "settings.json", "format": "json", "keys": ["region"]}],
            }
        )
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    declared = {"declaration": "vantage-metadata.json", "keys": keys, "files": []}
    absent = {"key": "fpga.firmware", "value": None, "status": "absent"}
    assert [report["metadata"] for report in sent] == [declared, {**declared, "values": [absent]}]


def test_a_metadata_capture_that_raises_costs_the_run_only_its_metadata(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`capture_metadata` warns about every problem it expects and never
    raises; anything that escapes it anyway warns once and the run is still
    recorded, with every result and without a `metadata` section, rather
    than left unrecorded by the failed `Recorder` construction.
    """

    def _raise(config: pytest.Config, rootpath: Path, *, read_files: bool) -> None:
        raise RuntimeError("synthetic metadata failure")

    monkeypatch.setattr("pytest_vantage.recorder.metadata.capture_metadata", _raise)
    sent: list[dict[str, object]] = []

    def _record_then_send(
        address: str, report: dict[str, object], *, timeout: float, token: str | None = None
    ) -> None:
        sent.append(report)
        transport.send(address, report, timeout=timeout, token=token)

    monkeypatch.setattr("pytest_vantage.recorder.send", _record_then_send)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-metadata"
    )

    result.assert_outcomes(passed=1, warnings=1)
    assert vantage_warnings(result) == [
        "vantage: error while reading vantage-metadata.json: synthetic metadata failure, "
        "the declaration is ignored"
    ]
    assert len(sent) == 2
    assert not any("metadata" in report for report in sent)
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert len(vantage_server.results()) == 1


def test_no_metadata_section_when_there_is_nothing_to_say(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `metadata` wire key is absent entirely -- not `null` -- on both
    reports when there is no declaration and the session reported nothing,
    the same additive-section shape `vcs`/`results` already establish."""
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pytest_vantage.recorder.send",
        lambda address, report, *, timeout, token=None: sent.append(report),
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
    vantage_server: VantageTestServer,
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
    vantage_server: VantageTestServer,
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
    vantage_server: VantageTestServer,
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
    vantage_server: VantageTestServer,
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
    vantage_server: VantageTestServer,
) -> None:
    pytester.makepyfile(test_sample=_PASSING_TEST)
    args = ("--vantage", f"--vantage-server={vantage_server.address}")

    pytester.runpytest_subprocess(*args).assert_outcomes(passed=1)
    pytester.runpytest_subprocess(*args).assert_outcomes(passed=1)

    executions = vantage_server.executions()
    assert len(executions) == 2
    assert executions[0].identity.value != executions[1].identity.value


def test_zero_test_collection_still_writes_one_row(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    assert len(vantage_server.executions()) == 1


def test_failed_collection_still_writes_one_row(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
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
    vantage_server: VantageTestServer,
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


_SECOND_OF_FOUR_FAILS = "\n\n\n".join(
    [
        "def test_a():\n    assert True",
        "def test_b():\n    assert False",
        "def test_c():\n    assert True",
        "def test_d():\n    assert True\n",
    ]
)


@pytest.mark.parametrize("option", ["-x", "--maxfail=1"])
def test_a_maxfail_stop_records_the_reason_pytest_gave(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    option: str,
) -> None:
    """`-x` stops pytest through its own failure path, not an interrupt, so
    no stop reaches `pytest_keyboard_interrupt`. The reason pytest printed
    is still recorded, as it is under xdist, so a run cut short by `-x`
    reads differently from a complete run of the tests it got through.
    """
    pytester.makepyfile(test_sample=_SECOND_OF_FOUR_FAILS)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", option
    )

    result.assert_outcomes(passed=1, failed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert execution.interrupted is False
    assert execution.interrupt_reason == "stopping after 1 failures"


def _raised(exception: BaseException) -> pytest.ExceptionInfo[BaseException]:
    try:
        raise exception
    except BaseException:
        return pytest.ExceptionInfo.from_current()


class _XdistInterrupted(KeyboardInterrupt):
    """Stands in for the `KeyboardInterrupt` subclass xdist stops a session
    with, for `-x` and for a worker that was interrupted alike."""


_A_SESSION_RUN_TO_ITS_END = SimpleNamespace(shouldfail=False, shouldstop=False)


@pytest.mark.parametrize(
    ("exit_status", "stop", "session", "worker_reason", "finished", "interrupted", "reason"),
    [
        pytest.param(0, None, None, None, True, False, None, id="passed"),
        pytest.param(1, None, None, None, True, False, None, id="failed"),
        pytest.param(
            1,
            None,
            SimpleNamespace(shouldfail="stopping after 1 failures", shouldstop=False),
            None,
            True,
            False,
            "stopping after 1 failures",
            id="maxfail",
        ),
        pytest.param(
            2,
            pytest.Session.Interrupted("1 error during collection"),
            None,
            None,
            True,
            False,
            "1 error during collection",
            id="collection-errors",
        ),
        pytest.param(2, KeyboardInterrupt(), None, None, False, True, None, id="ctrl-c"),
        pytest.param(
            2,
            pytest.exit.Exception("stop here"),
            None,
            None,
            False,
            True,
            "stop here",
            id="pytest-exit",
        ),
        pytest.param(
            0,
            pytest.exit.Exception("done early", returncode=0),
            None,
            None,
            False,
            True,
            "done early",
            id="pytest-exit-with-a-return-code",
        ),
        pytest.param(
            2,
            pytest.exit.Exception("x" * 5000),
            None,
            None,
            False,
            True,
            "x" * 1024,
            id="a-long-reason-is-cut",
        ),
        pytest.param(
            2,
            _XdistInterrupted("stopping after 1 failures"),
            None,
            None,
            True,
            False,
            "stopping after 1 failures",
            id="xdist-maxfail",
        ),
        pytest.param(
            2,
            _XdistInterrupted("<WorkerController gw1> received keyboard-interrupt"),
            None,
            "bye now",
            False,
            True,
            "bye now",
            id="pytest-exit-on-an-xdist-worker",
        ),
        pytest.param(
            2,
            _XdistInterrupted("<WorkerController gw1> received keyboard-interrupt"),
            None,
            "y" * 5000,
            False,
            True,
            "y" * 1024,
            id="a-long-worker-reason-is-cut",
        ),
        pytest.param(3, None, None, None, False, False, None, id="internal-error"),
    ],
)
def test_the_finish_report_records_how_the_session_ended(
    monkeypatch: pytest.MonkeyPatch,
    exit_status: int,
    stop: BaseException | None,
    session: SimpleNamespace | None,
    worker_reason: str | None,
    finished: bool,
    interrupted: bool,
    reason: str | None,
) -> None:
    """Only a real interruption -- Ctrl-C or `pytest.exit()`, in the session
    or on one of its xdist workers -- is recorded as interrupted with no
    finish time. pytest's internal error has no orderly finish either, but
    nothing interrupted it. Every other ending has a finish time, whatever
    its exit status. A reason is arbitrary text, so it is cut to 1024
    characters before it can crowd the results out of the report.
    """
    recorder, sent = _offline_recorder(monkeypatch)
    if worker_reason is not None:
        worker = SimpleNamespace(
            workeroutput={"exitstatus": 2, _WORKER_INTERRUPT_KEY: worker_reason}
        )
        recorder.pytest_testnodedown(node=worker, error=None)
    if stop is not None:
        recorder.pytest_keyboard_interrupt(excinfo=_raised(stop))

    recorder.pytest_sessionfinish(
        session=session or _A_SESSION_RUN_TO_ITS_END,  # type: ignore[arg-type]
        exitstatus=exit_status,
    )

    run = sent[-1]["run"]
    assert run["exit_status"] == exit_status  # type: ignore[index]
    assert (run["finished_at"] is not None) is finished  # type: ignore[index]
    assert run["interrupted"] is interrupted  # type: ignore[index]
    assert run["interrupt_reason"] == reason  # type: ignore[index]


def test_a_worker_interrupted_on_purpose_hands_its_reason_to_the_controller() -> None:
    """On a worker, Ctrl-C and `pytest.exit()` leave their reason in
    `workeroutput`, which xdist hands the controller; pytest's own stops,
    which xdist handles itself, leave nothing."""
    for stop, expected in [
        (pytest.exit.Exception("bye now"), {_WORKER_INTERRUPT_KEY: "bye now"}),
        (KeyboardInterrupt(), {_WORKER_INTERRUPT_KEY: ""}),
        (pytest.Session.Interrupted("1 error during collection"), {}),
    ]:
        config = SimpleNamespace(workeroutput={})
        relay = WorkerInterruptRelay(config)  # type: ignore[arg-type]

        relay.pytest_keyboard_interrupt(excinfo=_raised(stop))

        assert config.workeroutput == expected


def test_sigint_leaves_start_time_and_null_end_time(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
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
        wait_for_file(entered)
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
    vantage_server: VantageTestServer,
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
        wait_for_execution(vantage_server)
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


def _last_contact_at(server: VantageTestServer, run_id: str) -> datetime:
    detail = server.store.get_run_detail(run_id)
    assert detail is not None
    assert detail.last_contact_at is not None
    return detail.last_contact_at


def test_a_suite_exceeding_one_heartbeat_interval_advances_the_servers_last_contact(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's last-contact time advances during a long suite, end to
    end: `send_heartbeat` is left unpatched, so the plugin POSTs over real
    HTTP and the route calls `touch_last_contact` on `vantage_server`. Only
    `_BEAT_INTERVAL_SECONDS` is driven down, and one test outlasts it, so the
    test does not sleep through 30 real seconds.

    The store's `touch_last_contact` is wrapped, not replaced: the wrapper
    only records the value the start-write set, before the first heartbeat
    overwrites it, so the final assertion compares two real reads of the
    store. If the path, method or run id on the wire is wrong, the wrapper
    is never called and `baseline_by_run` stays empty. The session ran in
    this process, and its heartbeat thread ended with it.
    """
    monkeypatch.setattr("pytest_vantage.recorder._BEAT_INTERVAL_SECONDS", 0.02)

    baseline_by_run: dict[str, datetime] = {}
    real_touch_last_contact = vantage_server.store.touch_last_contact

    def _spy_touch_last_contact(execution_id: str, contacted_at: datetime) -> bool:
        if execution_id not in baseline_by_run:
            # Before this run's first heartbeat, the stored contact is
            # exactly what the start-write set.
            baseline_by_run[execution_id] = _last_contact_at(vantage_server, execution_id)
        return real_touch_last_contact(execution_id, contacted_at)

    monkeypatch.setattr(vantage_server.store, "touch_last_contact", _spy_touch_last_contact)
    pytester.makepyfile(test_long="import time\n\ndef test_it():\n    time.sleep(0.3)\n")

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    execution = wait_for_execution(vantage_server)
    run_id = execution.identity.value
    assert run_id in baseline_by_run, (
        "no heartbeat ever reached the server's touch_last_contact -- "
        "the plugin -> HTTP -> route -> store chain never completed"
    )
    assert _last_contact_at(vantage_server, run_id) > baseline_by_run[run_id]
    assert vantage_server.requests[-1] == ("POST", "/api/v1/runs")
    assert not any(thread.name == "vantage-heartbeat" for thread in threading.enumerate())


def _heartbeats(server: VantageTestServer) -> int:
    return sum(1 for _method, path in server.requests if path.endswith("/heartbeat"))


@pytest.mark.slow
def test_a_test_quieter_than_the_grace_period_keeps_its_run_alive(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """One test, fixture or collection can run longer than the server's
    grace period without reporting anything. Beats come from a timer, not
    from test reports, so the session keeps saying it is alive all through
    it and the run never reads as abandoned. Here the interval is 50 ms and
    the one test sleeps a second: beats sent only as reports arrive could
    number three at most, one per phase report."""
    pytester.makeconftest(
        "import pytest_vantage.recorder as recorder\nrecorder._BEAT_INTERVAL_SECONDS = 0.05\n"
    )
    pytester.makepyfile(test_quiet="import time\n\ndef test_it():\n    time.sleep(1.0)\n")

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1)
    assert _heartbeats(vantage_server) >= 6
    assert vantage_server.requests[-1] == ("POST", "/api/v1/runs")


def test_a_suite_shorter_than_one_interval_sends_no_heartbeat(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """The first beat is due a full 30-second interval after the start
    report, which already told the server the session is alive: a quick
    suite sends none at all, and no warning."""
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(20))
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=20)
    assert vantage_server.requests == [
        ("GET", "/api/v1/capabilities"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/runs"),
    ]
    assert vantage_warnings(result) == []


# --- End-to-end xdist ------------------------------------------------------


def test_xdist_run_leaves_exactly_one_run_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
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
    vantage_server: VantageTestServer,
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
    assert execution.interrupt_reason == "stopping after 1 failures"


def test_a_pytest_exit_on_an_xdist_worker_is_recorded_as_an_interruption(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """`pytest.exit()` in a test run by a worker ends that worker's session
    interrupted, and xdist stops the whole session with its own
    `KeyboardInterrupt` subclass, as it does for `-x`. It is still the
    user's interruption, recorded with the user's reason, exactly as without
    xdist.
    """
    pytest.importorskip("xdist")
    pytester.makepyfile(
        test_sample="import pytest\n\n\ndef test_a():\n    pass\n\n\n"
        "def test_b():\n    pytest.exit('bye now')\n"
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "2"
    )

    assert result.ret == pytest.ExitCode.INTERRUPTED
    (execution,) = vantage_server.executions()
    assert execution.finished_at is None
    assert execution.interrupted is True
    assert execution.interrupt_reason == "bye now"
