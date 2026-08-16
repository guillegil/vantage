"""RQ-1 (a run entry per invocation) and RQ-31 (its timestamps), proven
end-to-end (design.md D2a): a real `vantage` server (the `vantage_server`
fixture in `conftest.py`) and a real subprocess pytest invocation
(`pytester.runpytest_subprocess`), never a mock of either side of the HTTP
boundary.

Also carries the registration test for task 6.3/6.4 (whether `Recorder` is
wired into `pytest_configure`) and the timestamp-formatting unit tests for
task 6.1/6.2 -- both prerequisites the end-to-end scenarios above depend on,
kept in this file rather than split out because `design.md`'s own file table
lists only this one new test file for D2a.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest
from vantage_test_server import VantageTestServer

_PASSING_TEST = "def test_it():\n    assert True\n"


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


# --- Registration (task 6.3/6.4) --------------------------------------------


@pytest.mark.req("RQ-1")
def test_recorder_registered_only_when_vantage_flag_is_present(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration tracks activation alone at this point in the rollout.

    Task 6.4's own text describes the recorder as registered "after a
    successful preflight" -- but the preflight is PR12's task 6.8 and does
    not exist yet. This PR's deliberate resolution (stated in full in the
    apply-progress report) is: register unconditionally once activation
    succeeds, and let PR12 insert the preflight gate in front of this same
    call. This test proves exactly the condition PR11 owns -- mirrors 5.1's
    differential shape (one run with the option, one without, asserting a
    difference) applied to registration instead of a tree comparison.
    """
    from pytest_vantage.recorder import Recorder

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)

    active = pytester.parseconfigure("--vantage")
    inactive = pytester.parseconfigure()

    assert any(isinstance(plugin, Recorder) for plugin in active.pluginmanager.get_plugins())
    assert not any(
        isinstance(plugin, Recorder) for plugin in inactive.pluginmanager.get_plugins()
    )


# --- End-to-end (task 6.1, RQ-1 + RQ-31) ------------------------------------


@pytest.mark.req("RQ-1")
@pytest.mark.req("RQ-31")
def test_completed_session_writes_one_row_with_ordered_timestamps(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
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


@pytest.mark.req("RQ-1")
def test_second_invocation_gets_a_distinct_identifier(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    pytester.makepyfile(test_sample=_PASSING_TEST)
    args = ("--vantage", f"--vantage-server={vantage_server.address}")

    pytester.runpytest_subprocess(*args).assert_outcomes(passed=1)
    pytester.runpytest_subprocess(*args).assert_outcomes(passed=1)

    executions = vantage_server.executions()
    assert len(executions) == 2
    assert executions[0].identity.value != executions[1].identity.value


@pytest.mark.req("RQ-1")
def test_zero_test_collection_still_writes_one_row(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    assert len(vantage_server.executions()) == 1


@pytest.mark.req("RQ-1")
def test_failed_collection_still_writes_one_row(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    pytester.makepyfile(test_broken="import this_module_does_not_exist_anywhere_at_all\n")

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    assert len(vantage_server.executions()) == 1


@pytest.mark.req("RQ-31")
def test_sigint_leaves_start_time_and_null_end_time(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """`pytest`'s `wrap_session` calls `pytest_sessionfinish` from a
    `finally` with `ExitCode.INTERRUPTED` (design.md D7) -- the report IS
    sent, with `finished_at` null and `interrupted` true. Needs a raw
    `Popen` (`pytester.popen`, not `runpytest_subprocess`) because the
    signal has to be delivered to a still-running child process.
    """
    pytester.makepyfile(
        test_slow=(
            "import time\n\n\ndef test_slow():\n    time.sleep(5)\n"
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


# --- End-to-end xdist (task 6.5, RQ-1 + RQ-27) ------------------------------


@pytest.mark.req("RQ-1")
@pytest.mark.req("RQ-27")
def test_xdist_run_leaves_exactly_one_run_entry(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """Ties 5.3's unit-level xdist guard to a real `-n 4` subprocess run:
    four workers plus the controller all execute `pytest_configure`, and
    only the controller may end up with a registered `Recorder`.
    """
    pytester.makepyfile(
        test_many="\n".join(f"def test_{i}():\n    assert True\n" for i in range(8))
    )

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "4"
    )

    result.assert_outcomes(passed=8)
    assert len(vantage_server.executions()) == 1
