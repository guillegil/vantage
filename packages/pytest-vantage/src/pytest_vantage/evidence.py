"""`EvidenceCollector`: the second registered plugin object failure capture
needs (design.md D68).

`item` and `excinfo` exist only in the process that ran the test -- under
xdist that is a *worker*, and `plugin.py::pytest_configure`'s worker branch
returns before anything else runs. `EvidenceCollector` is therefore
registered on BOTH the controller and every worker, so
`pytest_runtest_makereport` fires wherever the test actually executed.

Standard library and `pytest` only (RQ-24) -- this module never opens a
socket and never imports `pytest_vantage.recorder`.
"""

from __future__ import annotations

from typing import Any

import pytest

from pytest_vantage.boundary import _warn


class EvidenceCollector:
    """One hookwrapper, no I/O, no state beyond two session-constant values
    (design.md D68).

    `_disabled` is this instance's OWN fault-isolation latch -- deliberately
    not `pytest_vantage.boundary.fault_isolated`, which wraps an ordinary
    hook, never a hookwrapper: a hookwrapper that returns instead of
    yielding breaks pluggy, so the `yield` here can never be inside a
    `try`, and the isolation is a bare `try/except Exception` around the
    post-yield body only (design.md D68, ADR-0014 condition 2's `vcs.py`
    shape).
    """

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._disabled = False
        self._capture_disabled = config.getoption("capture") == "no"  # design.md D71

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
        outcome = yield  # never inside try -- pluggy contract
        if self._disabled:
            return
        try:
            report = outcome.get_result()
            report.vantage_evidence = _extract(item, call, report, self._capture_disabled)
        except Exception as exc:  # deliberately broad, never BaseException -- RQ-21, RQ-31
            self._disabled = True
            _warn(self._config, f"vantage: error while capturing failure evidence: {exc}")


def _skip_reason(
    report: pytest.TestReport, excinfo: pytest.ExceptionInfo[BaseException]
) -> str | None:
    """`report.longrepr` is a `(path, lineno, reason)` tuple for a skip, not
    an exception repr (design.md D70) -- `longrepr[2]` behind a shape guard
    is the reason, stored VERBATIM, including pytest's own `"Skipped: "`
    prefix where present; stripping it would be a second parser of pytest's
    own display text. `str(excinfo.value)` is the guarded fallback for a
    shape this guard does not recognise.
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
    """The full D69 set. Every value is extracted in its OWN `try/except
    Exception`, so a single hostile object (a `__repr__` that raises, an
    unreadable source file) costs the one field it broke, never the rest
    (design.md D69) -- `EvidenceCollector`'s outer latch is the net for
    what escapes here, not the first one.

    `traceback`/`failure_path`/`failure_lineno` are rendered together via
    one call to `item._repr_failure_py(excinfo, style="long")` -- design.md's
    own snippet, `item.repr_failure(excinfo, style="long")`, no longer
    exists against the installed pytest (9.1.1): `Function.repr_failure`
    dropped the `style` keyword and instead reads `config.getoption
    ("tbstyle")` internally, which would silently reintroduce the exact
    `--tb` dependence Q1 exists to remove. `_repr_failure_py` is what BOTH
    `Node.repr_failure` (still accepts `style`) and `Function.repr_failure`
    (the override actually used for a test item) delegate to, and `Function`
    does not override it -- calling it directly is a private-underscore
    METHOD on a public class, not an import of a private MODULE, so "no
    private module is imported anywhere in this change" still holds.
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
    except Exception:  # deliberately broad -- the hostile-__repr__ case (D69)
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


def _extract(
    item: pytest.Item,
    call: pytest.CallInfo[None],
    report: pytest.TestReport,
    capture_disabled: bool,  # noqa: ARG001 -- wired in Phase 4 (design.md D71)
) -> dict[str, object]:
    """The field-extraction entry point: design.md D70's fixed four-row
    branch, driven by the report and `excinfo` -- NEVER by `.reprcrash`,
    which a skip's tuple `longrepr` does not have (that is exactly the
    `AttributeError` the *A skipped test does not crash the recorder*
    scenario falsifies).

    Row order matters: `hasattr(report, "wasxfail")` is checked BEFORE
    `report.outcome == "skipped"`, because a failing
    `@pytest.mark.xfail(reason=...)` arrives with BOTH `wasxfail` present
    and `outcome == "skipped"`, and `xfail_reason`/`skip_reason` are
    different columns. `hasattr`, never truthiness -- pytest sets the
    reason to `""` for a bare `@pytest.mark.xfail`, and `report.wasxfail or
    None` would erase that genuine empty string.
    """
    excinfo = call.excinfo
    if excinfo is None:
        return {}
    if hasattr(report, "wasxfail"):
        return {"xfail_reason": report.wasxfail}
    if report.outcome == "skipped":
        return {"skip_reason": _skip_reason(report, excinfo)}
    return _failure_fields(item, excinfo)


__all__ = ["EvidenceCollector"]
