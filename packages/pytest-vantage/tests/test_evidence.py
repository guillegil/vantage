"""`EvidenceCollector`: the second registered plugin object that runs
`pytest_runtest_makereport` on the process that actually ran the test --
under xdist that is a *worker*, never the controller.

`test_report_vantage_evidence_attribute_survives_the_xdist_wire` runs a
failing test under real `-n 2` and checks the SERIALIZED report the
controller receives, not the worker's own in-memory object -- if the
`TestReport.__dict__` round trip ever stopped carrying the attribute, this
is where it would surface, not a user's CI.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from pytest_vantage.boundary import VantageWarning
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture


def _closed_port_address() -> str:
    """An address where nothing listens: bind an ephemeral loopback port and
    close it again. Sessions pointed here fail their preflight, record
    nothing, and never reach a real server a developer may be running on the
    default address.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


def test_report_vantage_evidence_attribute_survives_the_xdist_wire(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`report.vantage_evidence` is a flat `dict[str, str | int | bool | None]`,
    set by `EvidenceCollector`'s hookwrapper on the worker that ran the
    test, and it must still be present on the report object the
    CONTROLLER's own `pytest_runtest_logreport` receives after xdist
    forwards it -- the mechanism `wasxfail` already relies on
    (`TestReport._to_json` copies `__dict__`; `TestReport.__init__(**extra)`
    restores it).

    No live server is needed: a worker's `EvidenceCollector` never
    preflights or opens a socket, so nothing here depends on one being
    reachable, only on `--vantage` activating recording and
    `--vantage-failure-text` opting into capture.

    The conftest hook below distinguishes controller from worker via
    `hasattr(config, "workerinput")` and writes the marker file ONLY from
    the controller process -- proving the round trip, not merely that the
    worker's own local (pre-serialization) object still carries the
    attribute it just set on itself.

    The CI leg that runs without xdist installs no `pytest-xdist` at all,
    so `-n 2` is not a recognised option there -- skip rather than fail, the
    same pattern `test_xdist_capture.py` uses.
    """
    pytest.importorskip("xdist")
    pytester.makeconftest(
        """
        import json

        _is_worker = False


        def pytest_configure(config):
            global _is_worker
            _is_worker = hasattr(config, "workerinput")


        def pytest_runtest_logreport(report):
            if _is_worker:
                return
            if report.when == "call" and "test_the_failure" in report.nodeid:
                with open("evidence_marker.json", "w") as fh:
                    json.dump(
                        {
                            "has_attr": hasattr(report, "vantage_evidence"),
                            "evidence": getattr(report, "vantage_evidence", None),
                        },
                        fh,
                    )
        """
    )
    pytester.makepyfile(
        test_wire="""
        def test_the_failure():
            raise AssertionError("synthetic failure for the xdist-wire test")
        """
    )

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    pytester.runpytest_subprocess(
        "--vantage",
        f"--vantage-server={_closed_port_address()}",
        "--vantage-failure-text",
        "-n",
        "2",
    )

    marker_path = pytester.path / "evidence_marker.json"
    assert marker_path.exists(), (
        "controller's pytest_runtest_logreport never fired for the failing test"
    )
    marker = json.loads(marker_path.read_text())
    assert marker["has_attr"] is True
    assert isinstance(marker["evidence"], dict)


def test_absent_flag_means_evidencecollector_is_never_registered(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture is opt-in: with `--vantage` alone and no
    `--vantage-failure-text`, no `EvidenceCollector` is registered anywhere
    -- a default session pays none of the second-rendering cost, because the
    hookwrapper does not exist, not because a flag is checked per test.
    """
    pytester.makeconftest(
        """
        import json


        def pytest_sessionstart(session):
            from pytest_vantage.evidence import EvidenceCollector

            registered = [
                type(plugin).__name__
                for plugin in session.config.pluginmanager.get_plugins()
                if isinstance(plugin, EvidenceCollector)
            ]
            with open("registered.json", "w") as fh:
                json.dump(registered, fh)
        """
    )
    pytester.makepyfile(
        test_sample="""
        def test_it_fails():
            raise AssertionError("synthetic failure for the absent-flag default test")
        """
    )

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    pytester.runpytest_subprocess("--vantage", f"--vantage-server={_closed_port_address()}")

    registered = json.loads((pytester.path / "registered.json").read_text())
    assert registered == []


def test_opt_in_flag_means_evidencecollector_is_registered(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `--vantage-failure-text` given alongside `--vantage`, exactly
    one `EvidenceCollector` is registered.

    This is the tamper-proof counterpart to the absent-flag test above --
    together they prove the flag actually flips the outcome rather than
    both branches accidentally landing on the same registration state.
    """
    pytester.makeconftest(
        """
        import json


        def pytest_sessionstart(session):
            from pytest_vantage.evidence import EvidenceCollector

            registered = [
                type(plugin).__name__
                for plugin in session.config.pluginmanager.get_plugins()
                if isinstance(plugin, EvidenceCollector)
            ]
            with open("registered.json", "w") as fh:
                json.dump(registered, fh)
        """
    )
    pytester.makepyfile(
        test_sample="""
        def test_it_fails():
            raise AssertionError("synthetic failure for the opt-in test")
        """
    )

    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={_closed_port_address()}", "--vantage-failure-text"
    )

    registered = json.loads((pytester.path / "registered.json").read_text())
    assert registered == ["EvidenceCollector"]


def test_absent_flag_does_not_suppress_outcome_timings_or_identity(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """Without failure capture the rest of the result is still recorded:
    `Recorder` never consults `EvidenceCollector` for outcome, timings or
    identity, so a session without `--vantage-failure-text` still records
    those in full against a real, live server.
    """
    pytester.makepyfile(
        test_sample="""
        def test_it_fails():
            raise AssertionError("synthetic failure for the absent-flag default test")
        """
    )

    pytester.runpytest_subprocess("--vantage", f"--vantage-server={vantage_server.address}")

    results = vantage_server.results()
    assert len(results) == 1
    (result,) = results
    assert result.outcome == "failed"
    assert result.identity.function_name == "test_it_fails"
    assert result.duration is not None
    assert result.started_at is not None
    assert result.finished_at is not None


# --- rendering and field extraction -----------------------------------------


def _capture_evidence(pytester: pytest.Pytester, *args: str) -> dict[str, dict[str, object] | None]:
    """Runs one `--vantage` session in-process and returns every
    `report.vantage_evidence` dict `EvidenceCollector` attached, keyed by
    ``f"{nodeid}::{when}"`` -- the same marker-file mechanism
    `test_report_vantage_evidence_attribute_survives_the_xdist_wire` above
    already uses, generalised to every phase report rather than one.

    An unreachable `--vantage-server` is given deliberately: `EvidenceCollector`
    registers and runs regardless of reachability (`plugin.py::pytest_configure`
    registers it BEFORE the preflight), so no live server is needed to
    observe what it extracted. The preflight's warning is raised in the
    outer process, before the inner session captures warnings, so it is
    asserted here rather than left to leak into the calling test.
    `--vantage-failure-text` is given unconditionally -- rendering and field
    extraction only run with it.
    """
    pytester.makeconftest(
        """
        import json

        _captured = {}


        def pytest_runtest_logreport(report):
            _captured[f"{report.nodeid}::{report.when}"] = getattr(
                report, "vantage_evidence", None
            )


        def pytest_sessionfinish(session):
            with open("evidence_capture.json", "w") as fh:
                json.dump(_captured, fh)
        """
    )
    with pytest.warns(VantageWarning, match="cannot reach"):
        pytester.runpytest_inprocess(
            "--vantage",
            f"--vantage-server={_closed_port_address()}",
            "--vantage-failure-text",
            *args,
        )
    captured: dict[str, dict[str, object] | None] = json.loads(
        (pytester.path / "evidence_capture.json").read_text()
    )
    return captured


def test_traceback_is_complete_under_tb_no(pytester: pytest.Pytester) -> None:
    """The traceback is complete under `--tb=no`: the stored traceback is
    rendered independently of the session's display flag, so it still names
    every frame even when nothing was shown on the terminal.
    """
    pytester.makepyfile(
        test_tb_no="""
        def level_three():
            raise AssertionError("synthetic failure at the bottom of three frames")


        def level_two():
            level_three()


        def test_three_frames():
            level_two()
        """
    )

    evidence = _capture_evidence(pytester, "--tb=no")

    call_evidence = evidence["test_tb_no.py::test_three_frames::call"]
    assert call_evidence is not None
    traceback = call_evidence["traceback"]
    assert isinstance(traceback, str)
    assert "level_three" in traceback
    assert "level_two" in traceback
    assert "test_three_frames" in traceback


def test_traceback_is_complete_under_tb_line(pytester: pytest.Pytester) -> None:
    """The traceback is complete under `--tb=line`, the other display flag
    that renders nothing close to a full traceback on the terminal.
    """
    pytester.makepyfile(
        test_tb_line="""
        def level_three():
            raise AssertionError("synthetic failure at the bottom of three frames")


        def level_two():
            level_three()


        def test_three_frames():
            level_two()
        """
    )

    evidence = _capture_evidence(pytester, "--tb=line")

    call_evidence = evidence["test_tb_line.py::test_three_frames::call"]
    assert call_evidence is not None
    traceback = call_evidence["traceback"]
    assert isinstance(traceback, str)
    assert "level_three" in traceback
    assert "level_two" in traceback
    assert "test_three_frames" in traceback


def test_failure_type_message_repr_come_from_excinfo(pytester: pytest.Pytester) -> None:
    """`failure_type` is `excinfo.typename`, `failure_message` is
    `excinfo.exconly()`, `failure_repr` is `repr(excinfo.value)` -- three
    genuinely different granularities, none derived from another.
    """
    pytester.makepyfile(
        test_fields="""
        def test_it_fails():
            raise ValueError("synthetic value error for field extraction")
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_fields.py::test_it_fails::call"]
    assert call_evidence is not None
    assert call_evidence["failure_type"] == "ValueError"
    assert call_evidence["failure_message"] == (
        "ValueError: synthetic value error for field extraction"
    )
    assert call_evidence["failure_repr"] == (
        "ValueError('synthetic value error for field extraction')"
    )


def test_twenty_tests_failing_at_one_line_group_as_one(pytester: pytest.Pytester) -> None:
    """Twenty tests failing at one source line group as one: the recorded
    `(failure_path, failure_lineno)` pair is the same for every test that
    raises from the identical helper line.
    """
    lines = ["def _raise():", '    raise AssertionError("synthetic shared failure")', ""]
    for index in range(20):
        lines.append(f"def test_case_{index}():")
        lines.append("    _raise()")
    pytester.makepyfile(test_shared_line="\n".join(lines))

    evidence = _capture_evidence(pytester)

    locations = set()
    for index in range(20):
        call_evidence = evidence[f"test_shared_line.py::test_case_{index}::call"]
        assert call_evidence is not None
        locations.add((call_evidence["failure_path"], call_evidence["failure_lineno"]))
    assert len(locations) == 1


def test_recorded_location_is_the_raising_helper_not_the_test_function(
    pytester: pytest.Pytester,
) -> None:
    """The recorded location is the raising site: the helper's raising
    line, never the test function's first line.
    """
    pytester.makepyfile(
        test_helper_location="""
        def helper():
            raise AssertionError("synthetic failure inside the helper")


        def test_calls_helper():
            helper()
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_helper_location.py::test_calls_helper::call"]
    assert call_evidence is not None
    assert call_evidence["failure_lineno"] == 2  # the `raise` line inside `helper`, not test line 6
    path = call_evidence["failure_path"]
    assert isinstance(path, str)
    assert path.endswith("test_helper_location.py")


def test_recorded_path_is_relative_to_the_rootdir(pytester: pytest.Pytester) -> None:
    """Inside the rootdir, the recorded path is relative to it -- the base
    node ids already use -- so the same failing line has the same location
    on every machine, and the checkout's absolute path (often a home
    directory) is never sent."""
    pytester.makepyfile(
        **{
            "tests/helpers": """
            def boom():
                raise AssertionError("synthetic failure inside a helper module")
            """,
            "tests/test_uses_helper": """
            from helpers import boom


            def test_calls_helper():
                boom()
            """,
        }
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["tests/test_uses_helper.py::test_calls_helper::call"]
    assert call_evidence is not None
    assert call_evidence["failure_path"] == "tests/helpers.py"
    assert call_evidence["failure_lineno"] == 2


def test_a_failure_outside_the_rootdir_keeps_its_absolute_path(pytester: pytest.Pytester) -> None:
    """A crash site outside the rootdir (here the standard library) has no
    relative form, so its path stays absolute."""
    pytester.makepyfile(
        test_outside="""
        import json


        def test_parses_garbage():
            json.loads("{")
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_outside.py::test_parses_garbage::call"]
    assert call_evidence is not None
    path = call_evidence["failure_path"]
    assert isinstance(path, str)
    assert Path(path).is_absolute()
    assert Path(path).parts[-2:] == ("json", "decoder.py")


_TWO_FAILING_DOCTESTS = """
def f():
    \"\"\"
    >>> 1 + 1
    3
    \"\"\"


def g():
    \"\"\"
    >>> 2 + 2
    5
    \"\"\"
"""


@pytest.mark.parametrize("continue_on_failure", [False, True])
def test_doctest_failures_are_located_at_their_example(
    pytester: pytest.Pytester, continue_on_failure: bool
) -> None:
    """A doctest failure is raised inside pytest's doctest runner, the same
    line for every doctest in every project. The recorded location is the
    failing example's, as pytest reports it, and the traceback is pytest's
    doctest rendering with the Expected/Got diff."""
    pytester.makepyfile(mod=_TWO_FAILING_DOCTESTS)
    extra = ("--doctest-continue-on-failure",) if continue_on_failure else ()

    evidence = _capture_evidence(pytester, "--doctest-modules", *extra)

    locations = []
    for name in ("f", "g"):
        call_evidence = evidence[f"mod.py::mod.{name}::call"]
        assert call_evidence is not None
        locations.append((call_evidence["failure_path"], call_evidence["failure_lineno"]))
        traceback = call_evidence["traceback"]
        assert isinstance(traceback, str)
        assert "Expected:" in traceback
        assert "Got:" in traceback
    assert locations == [("mod.py", 3), ("mod.py", 10)]


def test_an_item_with_its_own_failure_rendering_keeps_it(pytester: pytest.Pytester) -> None:
    """A non-Python test item that overrides `repr_failure` (the pattern
    pytest documents for them) is recorded with its own rendering, located
    at the item, not at the line in its `runtest` that raised."""
    pytester.makepyfile(
        **{
            "specs/conftest": """
            import pytest


            class SpecError(Exception):
                pass


            class SpecItem(pytest.Item):
                def runtest(self):
                    raise SpecError("beta")

                def repr_failure(self, excinfo):
                    return f"SPEC FAILED: {excinfo.value} expected ok, got broken"

                def reportinfo(self):
                    return self.path, 0, f"spec: {self.name}"


            class SpecFile(pytest.File):
                def collect(self):
                    yield SpecItem.from_parent(self, name="beta")


            def pytest_collect_file(file_path, parent):
                if file_path.suffix == ".spec":
                    return SpecFile.from_parent(parent, path=file_path)
            """,
        }
    )
    pytester.makefile(".spec", **{"specs/beta": "beta: ok\n"})

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["specs/beta.spec::beta::call"]
    assert call_evidence is not None
    assert call_evidence["traceback"] == "SPEC FAILED: beta expected ok, got broken"
    assert call_evidence["failure_path"] == "specs/beta.spec"
    assert call_evidence["failure_lineno"] == 1


def test_skipped_test_records_skip_reason_not_failure_fields(pytester: pytest.Pytester) -> None:
    """A skipped test does not crash the recorder: `skip_reason` is
    recorded verbatim, including pytest's own prefix; the failure fields and
    traceback are absent, and recording itself does not raise.
    """
    pytester.makepyfile(
        test_skip="""
        import pytest


        @pytest.mark.skip(reason="synthetic skip reason for evidence capture")
        def test_it_is_skipped():
            pass
        """
    )

    evidence = _capture_evidence(pytester)

    setup_evidence = evidence["test_skip.py::test_it_is_skipped::setup"]
    assert setup_evidence is not None
    assert setup_evidence["skip_reason"] == ("Skipped: synthetic skip reason for evidence capture")
    assert "failure_type" not in setup_evidence
    assert "traceback" not in setup_evidence


def test_bare_xfail_records_empty_reason_not_none(pytester: pytest.Pytester) -> None:
    """`@pytest.mark.xfail` with no `reason=` records `xfail_reason == ""`,
    never absent -- the `hasattr` check, never truthiness.
    """
    pytester.makepyfile(
        test_bare_xfail="""
        import pytest


        @pytest.mark.xfail
        def test_it_is_expected_to_fail():
            raise AssertionError("synthetic xfail")
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_bare_xfail.py::test_it_is_expected_to_fail::call"]
    assert call_evidence is not None
    assert call_evidence["xfail_reason"] == ""


def test_xfail_precedes_skip_when_both_shapes_are_present(pytester: pytest.Pytester) -> None:
    """A failing `@pytest.mark.xfail(reason=...)` arrives with
    `report.outcome == "skipped"` AND `wasxfail` both present --
    `xfail_reason` must win over `skip_reason`.
    """
    pytester.makepyfile(
        test_xfail_and_skip_shape="""
        import pytest


        @pytest.mark.xfail(reason="synthetic xfail reason for precedence test")
        def test_it_fails_as_expected():
            raise AssertionError("synthetic expected failure")
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_xfail_and_skip_shape.py::test_it_fails_as_expected::call"]
    assert call_evidence is not None
    assert call_evidence["xfail_reason"] == "synthetic xfail reason for precedence test"
    assert "skip_reason" not in call_evidence


def test_a_repr_that_raises_costs_only_that_field(pytester: pytest.Pytester) -> None:
    """An exception whose `__repr__` raises costs only `failure_repr` --
    type, message, and traceback (all built from `str`, never `repr`) are
    still recorded.
    """
    pytester.makepyfile(
        test_bad_repr="""
        class _HostileError(Exception):
            def __repr__(self):
                raise RuntimeError("synthetic hostile __repr__")


        def test_it_raises_a_hostile_exception():
            raise _HostileError("synthetic message")
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_bad_repr.py::test_it_raises_a_hostile_exception::call"]
    assert call_evidence is not None
    assert call_evidence["failure_repr"] is None
    assert call_evidence["failure_type"] == "_HostileError"
    failure_message = call_evidence["failure_message"]
    assert isinstance(failure_message, str)
    assert "synthetic message" in failure_message
    assert call_evidence["traceback"] is not None


# --- captured output, empty distinct from absent ---------------------------


def test_silent_test_has_empty_captured_output_not_absent(pytester: pytest.Pytester) -> None:
    """A test that prints nothing, run under the default (enabled) capture,
    records `captured_stdout == ""` -- captured AND empty, never absent.
    """
    pytester.makepyfile(
        test_silent="""
        def test_it_prints_nothing():
            pass
        """
    )

    evidence = _capture_evidence(pytester)

    call_evidence = evidence["test_silent.py::test_it_prints_nothing::call"]
    assert call_evidence is not None
    assert call_evidence["captured_stdout"] == ""
    assert call_evidence["captured_stderr"] == ""


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
"""


@pytest.mark.parametrize("capture", ["fd", "sys", "tee-sys"])
def test_each_phase_carries_only_its_own_output(pytester: pytest.Pytester, capture: str) -> None:
    """pytest copies every section captured so far onto each phase report, so
    `report.capstdout` on the call report also holds the setup output, and on
    the teardown report all three. Each phase's evidence must hold only what
    that phase printed, or the concatenation across phases repeats it.
    """
    pytester.makepyfile(test_phases=_PRINTS_IN_EVERY_PHASE)

    evidence = _capture_evidence(pytester, f"--capture={capture}")

    stdout = {
        when: (evidence[f"test_phases.py::test_it_prints::{when}"] or {})["captured_stdout"]
        for when in ("setup", "call", "teardown")
    }
    stderr = {
        when: (evidence[f"test_phases.py::test_it_prints::{when}"] or {})["captured_stderr"]
        for when in ("setup", "call", "teardown")
    }
    assert stdout == {"setup": "SETUP-OUT\n", "call": "CALL-OUT\n", "teardown": "TEARDOWN-OUT\n"}
    assert stderr == {"setup": "", "call": "CALL-ERR\n", "teardown": ""}


@pytest.mark.parametrize(
    "disable_capture",
    [
        pytest.param(("-s",), id="capture-no"),
        pytest.param(("-p", "no:capture"), id="capture-plugin-blocked"),
    ],
)
def test_capture_disabled_leaves_output_absent(
    pytester: pytest.Pytester, disable_capture: tuple[str, ...]
) -> None:
    """A session run with `-s` / `--capture=no`, or with pytest's capture
    plugin blocked, never observes output at all, so
    `captured_stdout`/`captured_stderr` are `None`, not `""` -- the
    distinguisher is the session's capture mode, never `text or None`.
    """
    pytester.makepyfile(
        test_capture_off="""
        def test_it_prints_something():
            print("this line is never observed with capture disabled")
        """
    )

    evidence = _capture_evidence(pytester, *disable_capture)

    call_evidence = evidence["test_capture_off.py::test_it_prints_something::call"]
    assert call_evidence is not None
    assert call_evidence["captured_stdout"] is None
    assert call_evidence["captured_stderr"] is None


@pytest.mark.parametrize("distributed", [False, True], ids=["serial", "xdist"])
def test_blocking_the_capture_plugin_keeps_the_suite_outcome(
    pytester: pytest.Pytester, distributed: bool
) -> None:
    """With `-p no:capture` pytest never registers the `--capture` option.
    Reading it must not raise, or `pytest_configure` turns a run whose tests
    merely failed into an INTERNALERROR in which no test runs."""
    extra = ("-n", "2") if distributed else ()
    if distributed:
        pytest.importorskip("xdist")
    pytester.makepyfile(
        test_sample="""
        def test_passes():
            pass


        def test_fails():
            raise AssertionError("synthetic failure")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:capture",
        "--vantage",
        f"--vantage-server={_closed_port_address()}",
        "--vantage-failure-text",
        *extra,
    )

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(passed=1, failed=1)
    assert "INTERNALERROR" not in result.stdout.str() + result.stderr.str()
