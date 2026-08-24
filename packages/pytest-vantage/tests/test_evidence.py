"""`EvidenceCollector`: the second registered plugin object that runs
`pytest_runtest_makereport` on the process that actually ran the test --
under xdist that is a *worker*, never the controller (design.md D68).

`test_report_vantage_evidence_attribute_survives_the_xdist_wire` below is
the test that proves D68 rather than assuming it: it runs a failing test
under real `-n 2` and checks the SERIALIZED report the controller receives,
not the worker's own in-memory object -- if the `TestReport.__dict__`
round-trip reasoning in design.md were wrong, this is where it would
surface, not a user's CI. It was written and confirmed failing, for the
right reason (no `vantage_evidence` attribute on the controller's copy of
the report, because nothing yet registers on the worker), before
`pytest_vantage.evidence` existed at all.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from pytest_vantage.plugin import pytest_configure
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture


class _RegisterCallDouble:
    """Mirrors `test_xdist_guard.py`'s double of the same name (no shared
    import across test modules, by convention in this test suite)."""

    def __init__(self) -> None:
        self.registered: list[object] = []

    def register(self, plugin: object) -> None:
        self.registered.append(plugin)


class _ControllerConfigDouble:
    """A non-worker `pytest.Config` stand-in, carrying just enough surface
    for `pytest_configure`'s controller branch to run to completion: no
    `workerinput`, so `EvidenceCollector` registration and the preflight
    both run. The configured server address (a closed low port) fails the
    preflight immediately rather than waiting out a connect timeout --
    `EvidenceCollector` registers BEFORE that preflight runs either way
    (design.md D68), so its outcome is irrelevant to what this test checks.
    """

    def __init__(self) -> None:
        self.pluginmanager = _RegisterCallDouble()
        self._options: dict[str, Any] = {
            "vantage": True,
            "vantage_server": "http://127.0.0.1:9",
            "vantage_timeout": 0.1,
        }

    def getoption(self, name: str, default: object = None) -> object:
        return self._options.get(name, default)

    def getini(self, name: str) -> object:
        return None


@pytest.mark.req(id="RQ-27")
def test_report_vantage_evidence_attribute_survives_the_xdist_wire(
    pytester: pytest.Pytester,
) -> None:
    """design.md D68: `report.vantage_evidence` is a flat
    `dict[str, str | int | bool | None]`, set by `EvidenceCollector`'s
    hookwrapper on the worker that ran the test, and it must still be
    present on the report object the CONTROLLER's own
    `pytest_runtest_logreport` receives after xdist forwards it -- the
    mechanism `wasxfail` already relies on (`TestReport._to_json` copies
    `__dict__`; `TestReport.__init__(**extra)` restores it).

    No live server is needed: a worker's `EvidenceCollector` never
    preflights or opens a socket (design.md D68), so nothing here depends on
    one being reachable, only on `--vantage` activating registration.

    The conftest hook below distinguishes controller from worker via
    `hasattr(config, "workerinput")` and writes the marker file ONLY from
    the controller process -- proving the round trip, not merely that the
    worker's own local (pre-serialization) object still carries the
    attribute it just set on itself.

    RQ-27's "without xdist" CI matrix leg installs no `pytest-xdist` at all,
    so `-n 2` is not a recognised option there -- skip rather than fail, the
    same pattern `test_xdist_capture.py` already uses.
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
            raise AssertionError("synthetic failure for the D68 xdist-wire test")
        """
    )

    pytester.runpytest_subprocess("--vantage", "-n", "2")

    marker_path = pytester.path / "evidence_marker.json"
    assert marker_path.exists(), (
        "controller's pytest_runtest_logreport never fired for the failing test"
    )
    marker = json.loads(marker_path.read_text())
    assert marker["has_attr"] is True
    assert isinstance(marker["evidence"], dict)


def test_opt_out_flag_means_evidencecollector_is_never_registered(
    pytester: pytest.Pytester,
) -> None:
    """failure-evidence -> Capture opt-out under the opt-in rule -> The
    opt-out suppresses failure-text capture (design.md D72): with
    `--vantage-no-failure-text` given alongside `--vantage`, no
    `EvidenceCollector` is registered anywhere -- an opted-out session pays
    zero of the second-rendering cost, because the hookwrapper does not
    exist, not because a flag is checked per test.

    Currently a genuine RED: `--vantage-no-failure-text` is not yet a
    registered CLI option (task 2.12 registers it), so this subprocess
    invocation fails argument parsing before `pytest_configure` ever runs
    and `registered.json` is never written.
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
            raise AssertionError("synthetic failure for the opt-out test")
        """
    )

    pytester.runpytest_subprocess("--vantage", "--vantage-no-failure-text")

    registered = json.loads((pytester.path / "registered.json").read_text())
    assert registered == []


def test_opt_out_does_not_suppress_outcome_timings_or_identity(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """failure-evidence -> Capture opt-out under the opt-in rule -> The
    opt-out does not suppress the rest of the result (design.md D72):
    `Recorder` never consulted `EvidenceCollector` for outcome, timings or
    identity, so a session invoked with `--vantage-no-failure-text` still
    records those in full against a real, live server.

    Currently a genuine RED: `--vantage-no-failure-text` is not yet a
    registered CLI option (task 2.12 registers it), so this subprocess
    invocation fails argument parsing before anything is recorded and
    `vantage_server.results()` stays empty.
    """
    pytester.makepyfile(
        test_sample="""
        def test_it_fails():
            raise AssertionError("synthetic failure for the opt-out test")
        """
    )

    pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-no-failure-text"
    )

    results = vantage_server.results()
    assert len(results) == 1
    (result,) = results
    assert result.outcome == "failed"
    assert result.identity.function_name == "test_it_fails"
    assert result.duration is not None
    assert result.started_at is not None
    assert result.finished_at is not None


def test_evidencecollector_registers_on_the_controller_when_activated() -> None:
    """design.md D68: the non-xdist counterpart to the worker registration
    test in `test_xdist_guard.py` -- `EvidenceCollector` is registered on
    the controller too, since a session with no xdist workers at all still
    needs failure evidence collected somewhere."""
    from pytest_vantage.evidence import EvidenceCollector

    config = _ControllerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert any(isinstance(plugin, EvidenceCollector) for plugin in config.pluginmanager.registered)


# --- Phase 3: rendering and field extraction (design.md D69, D70) -----------


def _capture_evidence(pytester: pytest.Pytester, *args: str) -> dict[str, dict[str, object] | None]:
    """Runs one `--vantage` session in-process and returns every
    `report.vantage_evidence` dict `EvidenceCollector` attached, keyed by
    ``f"{nodeid}::{when}"`` -- the same marker-file mechanism
    `test_report_vantage_evidence_attribute_survives_the_xdist_wire` above
    already uses, generalised to every phase report rather than one.

    An unreachable `--vantage-server` is given deliberately: `EvidenceCollector`
    registers and runs regardless of reachability (`plugin.py::pytest_configure`
    registers it BEFORE the preflight, design.md D68), so no live server is
    needed to observe what it extracted.
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
    pytester.runpytest_inprocess("--vantage", "--vantage-server=http://127.0.0.1:9", *args)
    captured: dict[str, dict[str, object] | None] = json.loads(
        (pytester.path / "evidence_capture.json").read_text()
    )
    return captured


def test_traceback_is_complete_under_tb_no(pytester: pytest.Pytester) -> None:
    """failure-evidence -> Traceback capture invariant to display flags ->
    The traceback is complete under `--tb=no` (design.md D69, Q1): the
    stored traceback is rendered independently of the session's display
    flag, so it still names every frame even when nothing was shown on the
    terminal.
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
    """failure-evidence -> Traceback capture invariant to display flags ->
    The traceback is complete under `--tb=line` (design.md D69): identical
    obligation to the `--tb=no` case above, under the other display flag
    that also renders nothing close to a full traceback for the terminal.
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
    """failure-evidence -> Failure location, type and message (design.md
    D69): `failure_type` is `excinfo.typename`, `failure_message` is
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
    """failure-evidence -> Failure location, type and message -> Twenty
    tests failing at one source line group as one (design.md D69): the
    recorded `(failure_path, failure_lineno)` pair is the same for every
    test that raises from the identical helper line.
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
    """failure-evidence -> Failure location, type and message -> The
    recorded location is the raising site (design.md D69): the helper's
    raising line, never the test function's first line.
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


def test_skipped_test_records_skip_reason_not_failure_fields(pytester: pytest.Pytester) -> None:
    """failure-evidence -> Failure location, type and message -> A skipped
    test does not crash the recorder (design.md D70, row 3): `skip_reason`
    is recorded verbatim, including pytest's own prefix; the failure
    fields and traceback are absent, and recording itself does not raise.
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
    """failure-evidence -> Failure location, type and message (design.md
    D70): `@pytest.mark.xfail` with no `reason=` records `xfail_reason ==
    ""`, never absent -- the `hasattr` check, never truthiness.
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
    """failure-evidence -> Failure location, type and message (design.md
    D70): a failing `@pytest.mark.xfail(reason=...)` arrives with
    `report.outcome == "skipped"` AND `wasxfail` both present -- row 2
    (`xfail_reason`) must win over row 3 (`skip_reason`).
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
    """failure-evidence -> Failure location, type and message (design.md
    D69): an exception whose `__repr__` raises costs only `failure_repr`
    -- type, message, and traceback (all built from `str`, never `repr`)
    are still recorded.
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


@pytest.mark.req(id="RQ-24")
def test_the_private_rendering_method_this_change_depends_on_still_exists() -> None:
    """`_failure_fields` renders the traceback through
    `item._repr_failure_py(excinfo, style="long")`, a private-by-underscore
    method, because the public `Function.repr_failure` dropped its `style`
    keyword and now derives the style from `--tb` -- the exact dependence
    decision Q1 exists to eliminate. `Node._repr_failure_py` is the shared
    implementation both public overloads delegate to, and `Function` does
    not override it.

    Without this test a pytest release that renames or removes that method
    degrades **silently**: the `AttributeError` lands in `_failure_fields`'
    deliberately broad per-field `except`, `traceback`, `failure_path` and
    `failure_lineno` all become `None`, the session still records, and the
    database looks healthy while holding no failure evidence at all. That is
    Q1's failure mode arriving through a different door, and a per-field
    guard that swallows it is exactly why the dependency has to be asserted
    somewhere that goes red instead.

    Asserted against the public `pytest.Item`, never by importing
    `_pytest.nodes`: RQ-24's constraint is that no private *module* is
    imported, and this keeps that true.
    """
    method = getattr(pytest.Item, "_repr_failure_py", None)
    assert method is not None, (
        "pytest.Item._repr_failure_py is gone; evidence.py renders the traceback"
        " through it and the loss would be silent -- see this test's docstring"
    )
    parameters = inspect.signature(method).parameters
    assert "style" in parameters, (
        "pytest.Item._repr_failure_py no longer accepts `style`; without it the"
        " stored traceback follows the user's --tb flag, which decision Q1 forbids"
    )
