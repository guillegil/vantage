"""E2E result capture under xdist (RQ-12, design.md D19 layer 1) -- proves
the real subprocess/worker wiring, not just the unit-level guard already
covered by `test_xdist_guard.py` (`pytest_configure` returning before
registration when `config.workerinput` is present).

`test_five_hundred_results...` in Phase 5/8 never ran under `-n`; this is
the first place the whole plugin -> HTTP -> server hop is exercised with
real xdist workers in the loop.
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


@pytest.mark.req(id="RQ-12")
def test_six_tests_under_xdist_produce_six_results_and_one_run_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-12.1 (six results) and RQ-12.3 (exactly one run entry): under
    `-n 2`, six tests distributed across two xdist workers still leave
    exactly six results and one run row -- the recorder is registered only
    on the controller (D19 layer 1, already proven at the unit level in
    `test_xdist_guard.py`), and each worker's results reach the controller
    through `pytest_runtest_logreport`, which xdist forwards -- never a
    second HTTP request from a worker.

    RQ-27's "without xdist" CI matrix leg installs no `pytest-xdist` at
    all, so `-n 2` is not a recognised option there -- skip rather than
    fail, the same pattern `test_run_report.py`'s `-n 4` test already uses.
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


@pytest.mark.req(id="RQ-12")
def test_six_tests_without_xdist_also_produce_six_results(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-12.2, the non-optional control (design.md D19: "RQ-12 criterion 2
    is not optional"). The SAME six tests, run without `-n` at all, must
    still yield six results. This is the ONLY test in this file that would
    catch an over-aggressive dedup filter -- one written only against the
    `-n 2` case (for example, keying on `worker_id` being non-null) would
    pass `test_six_tests_under_xdist_...` above and then silently drop every
    result the moment xdist is absent, because there is no `workerinput` to
    key on at all in that case.
    """
    pytester.makepyfile(test_six=_SIX_TESTS)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    assert len(vantage_server.results()) == 6
