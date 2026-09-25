"""E2E result capture under xdist -- proves the real subprocess/worker
wiring, not just the unit-level guard covered by `test_xdist_guard.py`
(`pytest_configure` returning before registration when `config.workerinput`
is present).
"""

from __future__ import annotations

import pytest
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture

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
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
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
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
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
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
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
