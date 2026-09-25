"""`EvidenceCollector`: the second registered plugin object failure capture
needs.

`item` and `excinfo` exist only in the process that ran the test -- under
xdist that is a *worker*, and `plugin.py::pytest_configure`'s worker branch
returns before anything else runs. `EvidenceCollector` is therefore
registered on BOTH the controller and every worker, so
`pytest_runtest_makereport` fires wherever the test actually executed.

Standard library and `pytest` only -- this module never opens a socket and
never imports `pytest_vantage.recorder`.
"""

from __future__ import annotations

from typing import Any

import pytest

from pytest_vantage.boundary import _warn
from pytest_vantage.capture import is_subtest


class EvidenceCollector:
    """Two hooks, no I/O, no state beyond two session-constant values.

    `_disabled` is this instance's OWN fault-isolation latch -- deliberately
    not `pytest_vantage.boundary.fault_isolated`, which wraps an ordinary
    hook, never a hookwrapper: a hookwrapper that returns instead of
    yielding breaks pluggy, so the `yield` here can never be inside a
    `try`, and the isolation is a bare `try/except Exception` around the
    post-yield body only.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._disabled = False
        # `--capture` exists only while pytest's capture plugin is loaded. With
        # it blocked (`-p no:capture`) nothing is captured, which is the same
        # fact as `-s`.
        self._capture_disabled = config.getoption("capture", "no") == "no"

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
        outcome = yield  # never inside try -- pluggy contract
        if self._disabled:
            return
        try:
            report = outcome.get_result()
            report.vantage_evidence = _extract(item, call, report, self._capture_disabled)
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            self._disabled = True
            _warn(self._config, f"vantage: error while capturing failure evidence: {exc}")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Reads a subtest's captured output again, from the finished report.

        pytest's `subtests` fixture captures each subtest block on its own
        and attaches that output to the subtest report only after
        `pytest_runtest_makereport` has returned, so the value read there is
        always empty. `tryfirst` so a worker updates the report before xdist
        serialises it for the controller.
        """
        if self._disabled or not is_subtest(report):
            return
        try:
            evidence = getattr(report, "vantage_evidence", None)
            if isinstance(evidence, dict):
                evidence.update(_captured_fields(report, self._capture_disabled))
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            self._disabled = True
            _warn(self._config, f"vantage: error while capturing failure evidence: {exc}")


def _skip_reason(
    report: pytest.TestReport, excinfo: pytest.ExceptionInfo[BaseException]
) -> str | None:
    """`report.longrepr` is a `(path, lineno, reason)` tuple for a skip, not
    an exception repr -- `longrepr[2]` behind a shape guard is the reason,
    stored VERBATIM, including pytest's own `"Skipped: "` prefix where
    present; stripping it would be a second parser of pytest's own display
    text. `str(excinfo.value)` is the guarded fallback for a shape this
    guard does not recognise.
    """
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        try:
            return str(longrepr[2])
        except Exception:  # deliberately broad -- one hostile value, one None field
            return None
    try:
        return str(excinfo.value)
    except Exception:  # deliberately broad, same reason
        return None


def _failure_fields(
    item: pytest.Item, excinfo: pytest.ExceptionInfo[BaseException]
) -> dict[str, object]:
    """Every failure field. Each value is extracted in its OWN `try/except
    Exception`, so a single hostile object (a `__repr__` that raises, an
    unreadable source file) costs the one field it broke, never the rest --
    `EvidenceCollector`'s outer latch is the net for what escapes here.

    `traceback`/`failure_path`/`failure_lineno` are rendered together via
    one call to `item._repr_failure_py(excinfo, style="long")`. The public
    `Function.repr_failure` takes no `style` and reads `--tb` instead, which
    would make the stored traceback depend on the user's display flag.
    `_repr_failure_py` is what both `Node.repr_failure` and
    `Function.repr_failure` delegate to; it is a private method on a public
    class, so no private pytest module is imported.
    """
    fields: dict[str, object] = {}
    try:
        fields["failure_type"] = excinfo.typename
    except Exception:  # deliberately broad -- one field lost, not the rest
        fields["failure_type"] = None
    try:
        fields["failure_message"] = excinfo.exconly()
    except Exception:  # deliberately broad, same reason
        fields["failure_message"] = None
    try:
        fields["failure_repr"] = repr(excinfo.value)
    except Exception:  # deliberately broad -- the hostile-__repr__ case
        fields["failure_repr"] = None

    try:
        repr_obj: object = item._repr_failure_py(excinfo, style="long")  # noqa: SLF001
    except Exception:  # deliberately broad, same reason
        repr_obj = None
    try:
        fields["traceback"] = str(repr_obj) if repr_obj is not None else None
    except Exception:  # deliberately broad, same reason
        fields["traceback"] = None
    try:
        reprcrash = getattr(repr_obj, "reprcrash", None)
        fields["failure_path"] = reprcrash.path if reprcrash is not None else None
        fields["failure_lineno"] = reprcrash.lineno if reprcrash is not None else None
    except Exception:  # deliberately broad, same reason
        fields["failure_path"] = None
        fields["failure_lineno"] = None
    return fields


def _phase_output(report: pytest.TestReport, stream: str) -> str:
    """What this phase alone printed on `stream` ("stdout"/"stderr").

    Not `report.capstdout`/`.capstderr`: pytest copies every section
    captured so far for the item onto each phase report, so the call report
    also carries the setup output and the teardown report all three. Only the
    section named for this report's own phase is this phase's output.
    """
    name = f"Captured {stream} {report.when}"
    return "".join(content for section, content in report.sections if section == name)


def _captured_fields(report: pytest.TestReport, capture_disabled: bool) -> dict[str, object]:
    """`captured_stdout`/`captured_stderr` for THIS phase report alone.
    `capture_disabled` is one session-constant flag, read once at
    `EvidenceCollector.__init__` and passed down unchanged -- when set, BOTH
    fields are `None` throughout the session: capture mode is the only thing
    that can distinguish "empty" from "never observed" (`report.sections`
    reads back empty in both cases, since pytest only adds a section `if
    out:`). When capture is enabled, `""` means this phase printed nothing,
    never coerced through `text or None`, which would erase that genuine
    empty string.

    Each field keeps its own `try/except`, the same per-field isolation
    `_failure_fields` uses -- a section a plugin filled with something other
    than text costs this phase's field, not the whole phase.

    Known limit: a test that consumes its own buffer via the `capsys`/`capfd`
    fixture's `readouterr()` leaves nothing in `report.sections` for this
    phase, so it reads back as `""` here too -- indistinguishable from a
    genuinely silent phase through pytest's public surface.
    """
    if capture_disabled:
        return {"captured_stdout": None, "captured_stderr": None}
    fields: dict[str, object] = {}
    try:
        fields["captured_stdout"] = _phase_output(report, "stdout")
    except Exception:  # deliberately broad -- one field lost, not the rest
        fields["captured_stdout"] = None
    try:
        fields["captured_stderr"] = _phase_output(report, "stderr")
    except Exception:  # deliberately broad, same reason
        fields["captured_stderr"] = None
    return fields


def _extract(
    item: pytest.Item,
    call: pytest.CallInfo[None],
    report: pytest.TestReport,
    capture_disabled: bool,
) -> dict[str, object]:
    """The field-extraction entry point: a fixed four-way branch driven by
    the report and `excinfo` -- never by `.reprcrash`, which a skip's tuple
    `longrepr` does not have and would raise `AttributeError` on. Every
    branch also gets `captured_stdout`/`captured_stderr`: captured output is
    a record of what ran, not of what went wrong, so it is never gated by
    the failure branch. `capture.py::_captured_output` concatenates this
    per-phase value across setup/call/teardown.

    Branch order matters: `hasattr(report, "wasxfail")` is checked BEFORE
    `report.outcome == "skipped"`, because a failing
    `@pytest.mark.xfail(reason=...)` arrives with BOTH `wasxfail` present
    and `outcome == "skipped"`, and `xfail_reason`/`skip_reason` are
    different columns. `hasattr`, never truthiness -- pytest sets the
    reason to `""` for a bare `@pytest.mark.xfail`, and `report.wasxfail or
    None` would erase that genuine empty string.
    """
    excinfo = call.excinfo
    if excinfo is None:
        fields: dict[str, object] = {}
        if report.failed:
            # Failed with no exception: a strict xfail whose body passed.
            # pytest's own text (`[XPASS(strict)] <reason>`) is the only
            # statement of why; no exception type is invented for it.
            try:
                fields["failure_message"] = report.longreprtext or None
            except Exception:  # deliberately broad -- one field lost, not the rest
                fields["failure_message"] = None
    elif hasattr(report, "wasxfail"):
        fields = {"xfail_reason": report.wasxfail}
    elif report.outcome == "skipped":
        fields = {"skip_reason": _skip_reason(report, excinfo)}
    else:
        fields = _failure_fields(item, excinfo)
    fields.update(_captured_fields(report, capture_disabled))
    return fields


__all__ = ["EvidenceCollector"]
