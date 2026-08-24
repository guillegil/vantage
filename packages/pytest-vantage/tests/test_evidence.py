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
