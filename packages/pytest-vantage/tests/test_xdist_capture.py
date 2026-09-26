"""E2E result capture under xdist -- proves the real subprocess/worker
wiring, not just the unit-level guard covered by `test_xdist_guard.py`
(`pytest_configure` returning before registration when `config.workerinput`
is present).
"""

from __future__ import annotations

import re

import pytest
from vantage_test_server import VantageTestServer

_SIX_TESTS = """
def test_1():
    assert True


def test_2():
    assert True


def test_3():
    assert True


def test_4():
    assert True


def test_5():
    assert True


def test_6():
    assert True
"""


def test_six_tests_under_xdist_produce_six_results_and_one_run_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Under `-n 2`, six tests distributed across two xdist workers still
    leave exactly six results and one run row -- the recorder is registered
    only on the controller, and each worker's results reach the controller
    through `pytest_runtest_logreport`, which xdist forwards -- never a
    second HTTP request from a worker.

    The CI leg that runs without xdist installs no `pytest-xdist` at all,
    so `-n 2` is not a recognised option there -- skip rather than fail, the
    same pattern `test_run_report.py`'s `-n 4` test uses.
    """
    pytest.importorskip("xdist")
    pytester.makepyfile(test_six=_SIX_TESTS)

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "2"
    )

    results = vantage_server.results()
    assert len(results) == 6
    assert len(vantage_server.executions()) == 1
    # Proves `-n 2` genuinely distributed the run across workers rather than
    # silently falling back to the controller alone -- a false GREEN this
    # test would otherwise not catch, since result/run counts alone cannot
    # tell "ran on one worker" from "xdist never engaged" apart.
    worker_ids = {result.worker_id for result in results}
    assert worker_ids == {"gw0", "gw1"}


_CRASHES_ITS_WORKER = """
import os


def test_crashes_its_worker():
    os._exit(1)
"""


@pytest.mark.parametrize("failure_text", [False, True], ids=["plain", "failure-text"])
def test_a_test_whose_worker_crashed_is_recorded_as_failed(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    failure_text: bool,
) -> None:
    """A worker that dies mid-test (a segfault, an OOM kill) makes xdist log
    that test once, with a report of no known phase, and replace the worker.
    pytest counts the test as failed, so the run records it as failed rather
    than leaving it out and every recorded result passing on a failed run.
    The crash text is its failure message, and only when failure text was
    asked for.
    """
    pytest.importorskip("xdist")
    pytester.makepyfile(test_six=_SIX_TESTS, test_crash=_CRASHES_ITS_WORKER)
    options = ["--vantage-failure-text"] if failure_text else []

    run = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", *options, "-n", "2"
    )

    assert run.ret == pytest.ExitCode.TESTS_FAILED
    assert "VantageWarning" not in run.stdout.str() + run.stderr.str()
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert execution.exit_status == 1
    results = {result.identity.function_name: result for result in vantage_server.results()}
    assert set(results) == {f"test_{n}" for n in range(1, 7)} | {"test_crashes_its_worker"}
    crashed = results.pop("test_crashes_its_worker")
    assert all(result.outcome == "passed" for result in results.values())
    assert crashed.outcome == "failed"
    assert crashed.setup_outcome == "passed"
    assert crashed.call_outcome is None
    assert crashed.teardown_outcome is None
    assert crashed.finished_at is None
    assert crashed.worker_id is not None
    if failure_text:
        assert crashed.failure is not None
        assert re.fullmatch(
            r"worker 'gw\d' crashed while running 'test_crash.py::test_crashes_its_worker'",
            crashed.failure.failure_message or "",
        )
    else:
        assert crashed.failure is None


# Fails on gw0 only. gw1 passes after a short sleep, so its passing call
# report reaches the controller after gw0's failing one.
_FAILS_ON_ONE_WORKER = """
import os
import time


def test_platform_specific():
    if os.environ["PYTEST_XDIST_WORKER"] == "gw0":
        raise AssertionError("fails on gw0 only")
    time.sleep(0.5)
"""


def test_dist_each_keeps_a_failure_seen_on_one_worker(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Under `--dist each` every worker runs every test. The server keeps one
    result per node id, so the plugin sends the most severe execution, whole:
    the failure `--dist each` exists to find, with the worker it happened on,
    rather than whichever report arrived last."""
    pytest.importorskip("xdist")
    pytester.makepyfile(test_each=_FAILS_ON_ONE_WORKER)

    run = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "2", "--dist", "each"
    )

    run.assert_outcomes(passed=1, failed=1)
    (execution,) = vantage_server.executions()
    assert execution.exit_status == 1
    (result,) = vantage_server.results()
    assert result.outcome == "failed"
    assert result.call_outcome == "failed"
    assert result.worker_id == "gw0"


_FAILING_SUBTEST = """
def test_with_subtests(subtests):
    for i in range(3):
        with subtests.test(i=i):
            assert i != 2, "i must not be two"
"""


@pytest.mark.skipif(
    not hasattr(pytest, "Subtests"), reason="the subtests fixture is built into pytest from 9"
)
def test_a_failing_subtest_forwarded_by_a_worker_keeps_its_evidence(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Subtest reports cross the worker-to-controller hop as their own
    report type, evidence included, so a worker-run failing subtest is
    recorded the same as a local one."""
    pytest.importorskip("xdist")
    pytester.makepyfile(test_fixture=_FAILING_SUBTEST)

    pytester.runpytest_subprocess(
        "--vantage",
        f"--vantage-server={vantage_server.address}",
        "--vantage-failure-text",
        "-n",
        "2",
    )

    (result,) = vantage_server.results()
    assert result.worker_id is not None
    assert result.outcome == "failed"
    assert result.failure is not None
    assert result.failure.failure_type == "AssertionError"


_PRINTS_IN_EVERY_PHASE = """
import pytest


@pytest.fixture
def noisy():
    print("SETUP-OUT")
    yield
    print("TEARDOWN-OUT")


def test_it_prints(noisy):
    print("CALL-OUT")
"""


def test_captured_output_forwarded_by_workers_is_stored_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Captured sections cross the worker-to-controller hop with the
    report, so each phase's output is still stored exactly once when a
    worker ran the test."""
    pytest.importorskip("xdist")
    pytester.makepyfile(test_output=_PRINTS_IN_EVERY_PHASE)

    pytester.runpytest_subprocess(
        "--vantage",
        f"--vantage-server={vantage_server.address}",
        "--vantage-failure-text",
        "-n",
        "2",
    )

    (result,) = vantage_server.results()
    assert result.worker_id is not None
    assert result.captured.stdout == "SETUP-OUT\nCALL-OUT\nTEARDOWN-OUT\n"


def test_six_tests_without_xdist_also_produce_six_results(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """The control for the xdist test above: the SAME six tests, run
    without `-n` at all, must still yield six results. This is the ONLY
    test in this file that would catch an over-aggressive dedup filter --
    one written only against the `-n 2` case (for example, keying on
    `worker_id` being non-null) would pass `test_six_tests_under_xdist_...`
    above and then silently drop every result the moment xdist is absent,
    because there is no `workerinput` to key on at all in that case.
    """
    pytester.makepyfile(test_six=_SIX_TESTS)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    assert len(vantage_server.results()) == 6
