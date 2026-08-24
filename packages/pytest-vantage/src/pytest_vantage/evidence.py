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


def _extract(
    item: pytest.Item,
    call: pytest.CallInfo[None],
    report: pytest.TestReport,
    capture_disabled: bool,  # noqa: ARG001 -- wired in Phase 4 (design.md D71)
) -> dict[str, object]:
    """The field-extraction entry point (design.md D69, D70).

    Stubbed to an empty dict in this phase -- Phase 3 replaces this body
    with the real branch table and field-by-field rendering. Registration
    and the xdist wire are what this phase proves; rendering is proven
    next.
    """
    return {}


__all__ = ["EvidenceCollector"]
