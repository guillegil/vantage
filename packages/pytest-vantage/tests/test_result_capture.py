"""End-to-end result capture (design.md D16-D18, phase 8): a real subprocess
pytest invocation (`pytester.runpytest_subprocess`) against a real `vantage`
server (`vantage_server` -- `vantage_test_server.py`), proving the five
outcome shapes (RQ-4), duration measurement (RQ-5) and identity storage
(RQ-9) survive the whole hop from pytest's own hooks through the plugin,
over HTTP, and into the server's in-memory store -- never a stub of either
side of that boundary.
"""

from __future__ import annotations

import pytest
from vantage.core.domain.result import Result
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture


def _by_function_name(results: list[Result], name: str) -> Result:
    """Exactly one result whose `function_name` matches, or fail loudly --
    a `>1` match would mean two of this test file's own scenarios collided
    on a name, and a `0` match would mean the scenario was never resolved.
    """
    matches = [result for result in results if result.identity.function_name == name]
    assert len(matches) == 1, f"expected exactly one result for {name!r}, got {matches!r}"
    return matches[0]


# --- Task 8.2: the five outcome shapes, RQ-4.1-4.5, end to end --------------

_FIVE_OUTCOME_SHAPES = """
import pytest


@pytest.fixture
def broken_fixture():
    raise RuntimeError("setup blew up")


def test_setup_raises(broken_fixture):
    assert True


@pytest.mark.skip(reason="RQ-4.2")
def test_marked_skip():
    assert True


@pytest.mark.xfail(reason="RQ-4.3")
def test_xfail_that_fails():
    assert False


@pytest.mark.xfail(reason="RQ-4.4, non-strict")
def test_xfail_that_passes():
    assert True


@pytest.fixture
def raising_teardown_fixture():
    yield
    raise RuntimeError("teardown blew up")


def test_passes_but_teardown_raises(raising_teardown_fixture):
    assert True
"""


@pytest.mark.req("RQ-4")
def test_five_outcome_shapes_recorded_end_to_end(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-4.1 (setup failure -> error), RQ-4.2 (`@pytest.mark.skip` ->
    skipped), RQ-4.3 (failing xfail -> xfailed), RQ-4.4 (passing non-strict
    xfail -> xpassed), RQ-4.5 (a test whose body passed but whose teardown
    raised is NOT `passed` -- it is `error`, because the fixture's own
    failure is real information a bare `passed` would hide).
    """
    pytester.makepyfile(test_outcomes=_FIVE_OUTCOME_SHAPES)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    results = vantage_server.results()
    assert _by_function_name(results, "test_setup_raises").outcome == "error"
    assert _by_function_name(results, "test_marked_skip").outcome == "skipped"
    assert _by_function_name(results, "test_xfail_that_fails").outcome == "xfailed"
    assert _by_function_name(results, "test_xfail_that_passes").outcome == "xpassed"
    teardown_raised = _by_function_name(results, "test_passes_but_teardown_raises")
    assert teardown_raised.outcome != "passed"
    assert teardown_raised.outcome == "error"
    assert teardown_raised.teardown_outcome == "failed"


# --- Task 8.3: duration measurement, RQ-5.1/5.2, end to end -----------------

_SLOW_SETUP_FAST_BODY = """
import time

import pytest


@pytest.fixture
def slow_fixture():
    time.sleep(8)
    yield


def test_with_slow_setup(slow_fixture):
    time.sleep(0.1)
    assert True
"""


@pytest.mark.req("RQ-5")
def test_setup_and_call_durations_are_measured_independently(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-5.1: `setup_duration` and `call_duration` are pytest's own
    per-phase timings, not one lump sum -- an 8-second fixture body against
    a 0.1-second test body proves the split is real and not two aliases of
    the same number. The 8 seconds are a real, literal sleep: RQ-5.1 is a
    *measurement* of the recorded duration, and a mocked clock would not
    measure anything -- see the phase report for the wall-clock cost and
    slow-test-marker recommendation this carries.
    """
    pytester.makepyfile(test_slow_setup=_SLOW_SETUP_FAST_BODY)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    result = _by_function_name(vantage_server.results(), "test_with_slow_setup")
    assert result.setup_duration is not None
    assert result.setup_duration >= 8
    assert result.call_duration is not None
    assert result.call_duration < 1


_SETUP_FAILURE_ONLY = """
import pytest


@pytest.fixture
def broken_fixture():
    raise RuntimeError("setup blew up")


def test_it(broken_fixture):
    assert True
"""


@pytest.mark.req("RQ-5")
def test_setup_failure_leaves_call_duration_null_not_zero(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-5.2: a phase that never ran (`call`, here, because `setup` failed)
    is `None`, never `0.0` -- the forbidden idiom is `x or None`, which would
    turn a genuine `0.0` into `None` but would also happily let a phase that
    never ran default to `0.0` if the guard were ever inverted by mistake.
    This is the end-to-end hop of the same guard `test_capture.py` already
    proves at the pure-function level (design.md D17).
    """
    pytester.makepyfile(test_setup_failure_only=_SETUP_FAILURE_ONLY)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    result = _by_function_name(vantage_server.results(), "test_it")
    assert result.call_duration is None
    assert result.call_outcome is None


# --- Task 8.4: identity storage, RQ-9.1/9.2, end to end ---------------------

_TWO_TESTS_IN_ONE_FILE = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"


@pytest.mark.req("RQ-9")
def test_filtering_by_file_path_returns_every_test_defined_in_that_file(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-9.1: `file_path` alone is enough to recover every test that file
    defines. Two files, two tests each, run in the same session -- filtering
    by one file's path must return exactly its own two tests, not the other
    file's, and not a subset of its own (an over-aggressive dedup filter, the
    exact class of defect a prior review already caught once in this change,
    would silently produce a subset here).
    """
    pytester.makepyfile(test_multi_a=_TWO_TESTS_IN_ONE_FILE, test_multi_b=_TWO_TESTS_IN_ONE_FILE)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    results = vantage_server.results()
    from_file_a = {
        result.identity.function_name
        for result in results
        if result.identity.file_path == "test_multi_a.py"
    }
    from_file_b = {
        result.identity.function_name
        for result in results
        if result.identity.file_path == "test_multi_b.py"
    }
    assert from_file_a == {"test_one", "test_two"}
    assert from_file_b == {"test_one", "test_two"}


@pytest.mark.req("RQ-9")
def test_module_level_test_stores_a_null_class_name(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-9.2: a module-level test's `class_name` is `None`, never `""` --
    the two carry different meanings and the wire/storage hops must not
    collapse them. `pytest_p.py::TestInClass::test_in_class` sits alongside
    the module-level test in the same run so the assertion is discriminating
    (a `class_name` bug that always returns `None` would pass a file with
    only module-level tests, but not this one).
    """
    pytester.makepyfile(
        test_p="""
class TestInClass:
    def test_in_class(self):
        assert True


def test_module_level():
    assert True
"""
    )

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    results = vantage_server.results()
    module_level = _by_function_name(results, "test_module_level")
    in_class = _by_function_name(results, "test_in_class")
    assert module_level.identity.class_name is None
    assert in_class.identity.class_name == "TestInClass"
