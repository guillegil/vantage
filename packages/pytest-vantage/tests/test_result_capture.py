"""End-to-end result capture: a real subprocess pytest invocation
(`pytester.runpytest_subprocess`) against a real `vantage` server
(`vantage_server` -- `vantage_test_server.py`), proving the five outcome
shapes, duration measurement and identity storage survive the whole hop from
pytest's own hooks through the plugin, over HTTP, and into the server's
SQLite store -- never a stub of either side of that boundary.
"""

from __future__ import annotations

import os

import pytest
from vantage.core.domain.result import Result
from vantage_test_server import VantageTestServer


def _by_function_name(results: list[Result], name: str) -> Result:
    """Exactly one result whose `function_name` matches, or fail loudly --
    a `>1` match would mean two generated tests collided on a name, and a
    `0` match would mean the test was never recorded.
    """
    matches = [result for result in results if result.identity.function_name == name]
    assert len(matches) == 1, f"expected exactly one result for {name!r}, got {matches!r}"
    return matches[0]


# --- the five outcome shapes, end to end ------------------------------------

_FIVE_OUTCOME_SHAPES = """
import pytest


@pytest.fixture
def broken_fixture():
    raise RuntimeError("setup blew up")


def test_setup_raises(broken_fixture):
    assert True


@pytest.mark.skip(reason="synthetic skip")
def test_marked_skip():
    assert True


@pytest.mark.xfail(reason="synthetic xfail")
def test_xfail_that_fails():
    assert False


@pytest.mark.xfail(reason="synthetic non-strict xfail")
def test_xfail_that_passes():
    assert True


@pytest.fixture
def raising_teardown_fixture():
    yield
    raise RuntimeError("teardown blew up")


def test_passes_but_teardown_raises(raising_teardown_fixture):
    assert True
"""


def test_five_outcome_shapes_recorded_end_to_end(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Setup failure -> error, `@pytest.mark.skip` -> skipped, failing
    xfail -> xfailed, passing non-strict xfail -> xpassed, and a test whose
    body passed but whose teardown raised is NOT `passed` -- it is `error`,
    because the fixture's own failure is real information a bare `passed`
    would hide.
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


_XFAIL_RAISED_IN_SETUP = """
import pytest


@pytest.fixture
def xfails_in_setup():
    pytest.xfail("fixture says xfail")


@pytest.fixture
def broken():
    raise RuntimeError("setup is broken")


@pytest.mark.xfail(run=False, reason="never run")
def test_not_run():
    pass


def test_xfail_in_fixture(xfails_in_setup):
    pass


@pytest.mark.xfail(reason="setup is known broken")
def test_xfail_marked_setup_error(broken):
    pass


@pytest.mark.skip(reason="plain skip")
def test_plain_skip():
    pass
"""


def test_an_xfail_raised_during_setup_is_xfailed(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """`xfail(run=False)`, `pytest.xfail()` in a fixture, and an xfail-marked
    test whose fixture raises all end in a skipped setup report carrying
    `wasxfail`. pytest counts them as xfailed, and so does the record, with
    pytest's reason; a plain skip stays skipped."""
    pytester.makepyfile(test_setup_xfail=_XFAIL_RAISED_IN_SETUP)

    run = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    run.assert_outcomes(skipped=1, xfailed=3)
    results = vantage_server.results()
    for name, reason in (
        ("test_not_run", "[NOTRUN] never run"),
        ("test_xfail_in_fixture", "fixture says xfail"),
        ("test_xfail_marked_setup_error", "setup is known broken"),
    ):
        result = _by_function_name(results, name)
        assert result.outcome == "xfailed", name
        assert result.failure is not None
        assert result.failure.xfail_reason == reason
        assert result.failure.skip_reason is None
    plain_skip = _by_function_name(results, "test_plain_skip")
    assert plain_skip.outcome == "skipped"
    assert plain_skip.failure is not None
    assert plain_skip.failure.skip_reason == "Skipped: plain skip"


@pytest.mark.parametrize("strict_from", ["mark", "ini"])
def test_a_strict_xfail_that_passes_is_failed_with_pytests_reason(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    strict_from: str,
) -> None:
    """A strict xfail whose body passes is failed with no exception to
    describe. The reason pytest reports, `[XPASS(strict)] <reason>`, is the
    only statement of why, so it is stored as the failure message; no
    exception type is invented. Covers `strict=True` and the
    `xfail_strict` ini value."""
    if strict_from == "ini":
        pytester.makeini("[pytest]\nxfail_strict = true\n")
        mark = '@pytest.mark.xfail(reason="synthetic strict reason")'
    else:
        mark = '@pytest.mark.xfail(reason="synthetic strict reason", strict=True)'
    pytester.makepyfile(
        test_strict=f"import pytest\n\n\n{mark}\ndef test_passes_unexpectedly():\n    pass\n"
    )

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result = _by_function_name(vantage_server.results(), "test_passes_unexpectedly")
    assert result.outcome == "failed"
    assert result.failure is not None
    assert result.failure.failure_message == "[XPASS(strict)] synthetic strict reason"
    assert result.failure.failure_type is None


# --- duration measurement, end to end ---------------------------------------

_SLOW_SETUP_FAST_BODY = """
import time

import pytest


@pytest.fixture
def slow_fixture():
    time.sleep(0.5)
    yield


def test_with_slow_setup(slow_fixture):
    assert True
"""


@pytest.mark.slow
def test_setup_and_call_durations_are_measured_independently(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """`setup_duration` and `call_duration` are pytest's own per-phase
    timings, not one lump sum -- a half-second fixture body against an empty
    test body proves the split is real and not two aliases of the same
    number. The sleep is real because a mocked clock would not measure
    anything; hence the `slow` marker.
    """
    pytester.makepyfile(test_slow_setup=_SLOW_SETUP_FAST_BODY)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    result = _by_function_name(vantage_server.results(), "test_with_slow_setup")
    assert result.setup_duration is not None
    assert result.setup_duration >= 0.5
    assert result.call_duration is not None
    assert result.call_duration < 0.25


_SETUP_FAILURE_ONLY = """
import pytest


@pytest.fixture
def broken_fixture():
    raise RuntimeError("setup blew up")


def test_it(broken_fixture):
    assert True
"""


def test_setup_failure_leaves_call_duration_null_not_zero(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """A phase that never ran (`call`, here, because `setup` failed) is
    `None`, never `0.0`. The end-to-end hop of the same guard
    `test_capture.py` proves at the pure-function level.
    """
    pytester.makepyfile(test_setup_failure_only=_SETUP_FAILURE_ONLY)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    result = _by_function_name(vantage_server.results(), "test_it")
    assert result.call_duration is None
    assert result.call_outcome is None


# --- failure evidence, end to end -------------------------------------------

_FAILS_IN_A_HELPER = """
def helper():
    raise ValueError("synthetic failure in a helper")


def test_calls_helper():
    print("before the failure")
    helper()
"""


def test_failure_evidence_is_stored_end_to_end(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Every failure field the plugin extracts reaches storage under the
    name the server reads it by -- the server ignores unknown fields, so a
    name drifting on either side would otherwise drop it silently. The path
    is stored relative to the rootdir. Captured stderr arrives as the empty
    string, not null: the test wrote nothing there, but capture was on, so
    the field was observed."""
    pytester.makepyfile(test_evidence_e2e=_FAILS_IN_A_HELPER)

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result = _by_function_name(vantage_server.results(), "test_calls_helper")
    assert result.outcome == "failed"
    failure = result.failure
    assert failure is not None
    assert failure.failure_type == "ValueError"
    assert failure.failure_message == "ValueError: synthetic failure in a helper"
    assert failure.failure_repr == "ValueError('synthetic failure in a helper')"
    assert failure.failure_path == "test_evidence_e2e.py"
    assert failure.failure_lineno == 2
    assert failure.traceback is not None
    assert "def helper():" in failure.traceback
    assert result.captured.stdout == "before the failure\n"
    assert result.captured.stderr == ""


# --- captured output, end to end --------------------------------------------

_PRINTS_IN_EVERY_PHASE = """
import sys

import pytest


@pytest.fixture
def noisy():
    print("SETUP-OUT")
    yield
    print("TEARDOWN-OUT")


def test_it_prints(noisy):
    print("CALL-OUT")
    sys.stderr.write("CALL-ERR\\n")


def test_plain_pass():
    print("hello stdout")
"""


def test_captured_output_is_stored_once(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Every line a test printed is stored exactly once, in setup -> call ->
    teardown order, the way `pytest -rA` shows it -- passing tests
    included."""
    pytester.makepyfile(test_output=_PRINTS_IN_EVERY_PHASE)

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    results = vantage_server.results()
    phases = _by_function_name(results, "test_it_prints")
    assert phases.captured.stdout == "SETUP-OUT\nCALL-OUT\nTEARDOWN-OUT\n"
    assert phases.captured.stderr == "CALL-ERR\n"
    assert _by_function_name(results, "test_plain_pass").captured.stdout == "hello stdout\n"


_UNREADABLE_TEARDOWN_SECTION = """
import pytest


@pytest.fixture
def unreadable(request):
    yield
    request.node.add_report_section("teardown", "stdout", b"not text")


def test_it_prints(unreadable):
    print("CALL-OUT")
"""


def test_one_unreadable_phase_costs_only_its_own_output(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """A plugin that adds a non-text section breaks the read of one phase's
    output. That phase contributes nothing; the phases read fine are still
    stored, rather than the whole field reading as never captured."""
    pytester.makepyfile(test_unreadable=_UNREADABLE_TEARDOWN_SECTION)

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result = _by_function_name(vantage_server.results(), "test_it_prints")
    assert result.captured.stdout == "CALL-OUT\n"


# --- identity storage, end to end -------------------------------------------

_TWO_TESTS_IN_ONE_FILE = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"


def test_filtering_by_file_path_returns_every_test_defined_in_that_file(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """`file_path` alone is enough to recover every test that file defines.
    Two files, two tests each, run in the same session -- filtering by one
    file's path must return exactly its own two tests, not the other file's,
    and not a subset of its own (an over-aggressive dedup filter would
    silently produce a subset here).
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


def test_module_level_test_stores_a_null_class_name(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """A module-level test's `class_name` is `None`, never `""` --
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


# --- subtests, end to end ---------------------------------------------------

_needs_builtin_subtests = pytest.mark.skipif(
    not hasattr(pytest, "Subtests"), reason="the subtests fixture is built into pytest from 9"
)

_UNITTEST_SUBTEST_FAILS = """
import unittest


class Case(unittest.TestCase):
    def test_numbers(self):
        for i in range(3):
            with self.subTest(i=i):
                self.assertNotEqual(i, 2)
"""


def test_a_failing_unittest_subtest_fails_the_test(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """pytest reports the failing `subTest` and exits 1 while leaving the
    test's own report passed. The record follows pytest's verdict, with the
    subtest's exception as the evidence."""
    pytester.makepyfile(test_ut=_UNITTEST_SUBTEST_FAILS)

    run = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    assert run.ret == pytest.ExitCode.TESTS_FAILED
    result = _by_function_name(vantage_server.results(), "test_numbers")
    assert result.outcome == "failed"
    assert result.failure is not None
    assert result.failure.failure_type == "AssertionError"


_SUBTESTS_FIXTURE = """
import pytest


def test_with_subtests(subtests):
    for i in range(3):
        with subtests.test(i=i):
            print(f"inside-subtest-{i}")
            assert i != 2, "i must not be two"


def test_with_a_skipped_subtest(subtests):
    with subtests.test():
        pytest.skip("not today")
    with subtests.test():
        pass
"""


@_needs_builtin_subtests
def test_a_failing_subtest_is_recorded_with_its_evidence_and_output(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """With the `subtests` fixture pytest fails the test as "contains 1
    failed subtest", with no exception of its own. The failing subtest's
    exception is the evidence, and the output printed inside the subtest
    blocks, captured on the subtest reports, is kept. A skipped subtest
    leaves a passing test passed."""
    pytester.makepyfile(test_fixture=_SUBTESTS_FIXTURE)

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    results = vantage_server.results()
    result = _by_function_name(results, "test_with_subtests")
    assert result.outcome == "failed"
    assert result.failure is not None
    assert result.failure.failure_type == "AssertionError"
    assert result.failure.failure_message is not None
    assert "i must not be two" in result.failure.failure_message
    assert result.captured.stdout == "inside-subtest-0\ninside-subtest-1\ninside-subtest-2\n"
    assert _by_function_name(results, "test_with_a_skipped_subtest").outcome == "passed"


# --- an item that is its own file, end to end -------------------------------

# A node that is both a File and an Item, as older lint plugins build them:
# pytest warns and runs it, and its node id is the bare file path.
_FILE_ITEM_CONFTEST = """
import pytest


class StyleItem(pytest.File, pytest.Item):
    def collect(self):
        return []

    def runtest(self):
        pass


def pytest_collect_file(file_path, parent):
    if file_path.name.startswith("style_") and file_path.suffix == ".txt":
        return StyleItem.from_parent(parent, path=file_path)
"""


def test_an_item_whose_node_id_has_no_double_colon_is_recorded(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """Such an item is recorded like any other, and it never costs the rest
    of the session its results."""
    pytester.makeconftest(_FILE_ITEM_CONFTEST)
    pytester.makefile(".txt", style_z="x = 1\n")
    pytester.makepyfile(test_ok="def test_ok():\n    assert True\n")

    run = pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    run.assert_outcomes(passed=2)
    assert "error while reporting" not in run.stdout.str() + run.stderr.str()
    recorded = {result.identity.node_id for result in vantage_server.results()}
    assert recorded == {"style_z.txt", "test_ok.py::test_ok"}
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None


# --- empty parameter id, end to end -----------------------------------------

_EMPTY_PARAM_ID = """
import pytest


@pytest.mark.parametrize("value", [""], ids=[""])
def test_empty_id(value):
    assert value == ""


def test_unparametrised():
    assert True
"""


def test_empty_param_id_survives_the_real_server_hop_end_to_end(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """A parametrised test whose parameter id is the empty string keeps
    `param_id == ""` through the WHOLE chain (pytest's own hooks, the plugin,
    HTTP, the server). Runs alongside an unparametrised test in the same
    session, so both ends of the absent-versus-empty distinction are proven
    together.
    """
    pytester.makepyfile(test_empty_param=_EMPTY_PARAM_ID)

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    results = vantage_server.results()
    empty_param = _by_function_name(results, "test_empty_id")
    unparametrised = _by_function_name(results, "test_unparametrised")
    assert empty_param.identity.node_id == "test_empty_param.py::test_empty_id[]"
    assert empty_param.identity.param_id == ""
    assert unparametrised.identity.param_id is None


# --- names that are not UTF-8, end to end ------------------------------------

_FAILS_NAMING_A_NON_UTF8_FILE = """
import os


def test_reads_the_report():
    raise AssertionError("cannot parse " + os.fsdecode(b"report-\\xff.csv"))
"""


def test_names_that_are_not_utf8_are_recorded_not_lost(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A POSIX file name need not be UTF-8. Python decodes one with
    `surrogateescape`, so a test under a directory named `caf\\xe9` carries a
    lone surrogate in its node id, and so does a failure message quoting
    such a name. JSON can carry a lone surrogate as an escape but UTF-8
    cannot, so the server stores U+FFFD in its place: the test is recorded
    with its failure text, rather than the one character losing the whole
    session.
    """
    directory = os.fsencode(pytester.path) + b"/caf\xe9"
    try:
        os.mkdir(directory)
    except OSError as exc:
        pytest.skip(f"this filesystem refuses a name that is not UTF-8: {exc}")
    with open(directory + b"/test_non_utf8.py", "w") as handle:
        handle.write(_FAILS_NAMING_A_NON_UTF8_FILE)

    # Pytester decodes the child's output as strict UTF-8. Under a C locale,
    # or in UTF-8 mode, the child prints the name's raw byte and the harness
    # fails before any assertion; escaped, the output decodes everywhere.
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8:backslashreplace")
    # pytest's own cache cannot write such a node id to disk and fails the
    # session in `pytest_sessionfinish`; that is pytest's to fix, not ours.
    run = pytester.runpytest_subprocess(
        "-p",
        "no:cacheprovider",
        "--vantage",
        f"--vantage-server={vantage_server.address}",
        "--vantage-failure-text",
    )

    run.assert_outcomes(failed=1)
    assert "VantageWarning" not in run.stdout.str() + run.stderr.str()
    (stored,) = vantage_server.results()
    assert stored.identity.node_id == "caf\ufffd/test_non_utf8.py::test_reads_the_report"
    assert stored.identity.file_path == "caf\ufffd/test_non_utf8.py"
    assert stored.failure is not None
    assert stored.failure.failure_message == "AssertionError: cannot parse report-\ufffd.csv"
    assert stored.failure.failure_path == "caf\ufffd/test_non_utf8.py"
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
