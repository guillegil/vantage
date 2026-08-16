"""`Recorder` -- the hook implementation registered by `plugin.py` only once
activation and the reachability preflight both succeed (design.md D2a, D6).
Assembles the session report and sends it exactly once, from
`pytest_sessionfinish`, never per test (RQ-25's shape).

Every hook is wrapped in `pytest_vantage.boundary.fault_isolated`: an error
raised anywhere in the reporting path -- assembling the report, sending it,
a server that never answers -- becomes one warning and never the suite's
exit status (RQ-21).

Never imports `pytest_vantage.plugin`: registration is the plugin's job, not
this module's (RQ-24 keeps every import here to stdlib and `pytest`, same as
the rest of this package).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from pytest_vantage.boundary import fault_isolated
from pytest_vantage.transport import send

# `pytest.ExitCode.INTERRUPTED` (2) and `pytest.ExitCode.INTERNAL_ERROR` (3):
# design.md D1 -- `finished_at` is null iff the session did not end in an
# orderly way, and these are the two exit statuses that mean it did not.
# Compared as plain ints, not `pytest.ExitCode` members, so this module has
# no reason to import anything but `exitstatus` itself off the hook call.
_NULL_FINISH_EXIT_STATUSES = frozenset({2, 3})
_INTERRUPTED_EXIT_STATUS = 2


def isoformat_utc(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC text: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`.

    `datetime.isoformat()` alone omits the microsecond component when it is
    exactly zero, which would make the width variable -- design.md D1
    requires fixed width so lexicographic order stays chronological order.
    `strftime("%f")` always emits six digits regardless of value, which
    `isoformat()` alone does not guarantee.
    """
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


class Recorder:
    """Registered by `plugin.py::pytest_configure` once activation and the
    reachability preflight both succeed.

    One request per session (RQ-25): the report is assembled in memory and
    sent exactly once, from `pytest_sessionfinish`. `_config` is kept only
    so a hook that fails can route its warning through the terminal
    reporter (`boundary._warn`); `_disabled` is the fault-isolation latch
    `fault_isolated` reads and sets -- once `True`, every further hook call
    on this instance is a silent no-op.
    """

    def __init__(self, config: pytest.Config, address: str, timeout: float) -> None:
        self._config = config
        self._address = address
        self._timeout = timeout
        self._run_id = uuid.uuid4().hex
        self._started_at = datetime.now(timezone.utc)
        self._disabled = False

    @fault_isolated
    def pytest_report_header(self) -> str:
        """Names the run id so a test harness -- or a curious human -- can
        correlate a session with the row it produced, without reaching into
        storage internals.
        """
        return f"vantage: recording run {self._run_id} to {self._address}"

    @fault_isolated
    def pytest_sessionfinish(self, exitstatus: int) -> None:
        exit_status = int(exitstatus)
        orderly = exit_status not in _NULL_FINISH_EXIT_STATUSES
        finished_at = datetime.now(timezone.utc) if orderly else None

        report: dict[str, object] = {
            "run": {
                "id": self._run_id,
                "started_at": isoformat_utc(self._started_at),
                "finished_at": isoformat_utc(finished_at) if finished_at else None,
                "exit_status": exit_status,
                "interrupted": exit_status == _INTERRUPTED_EXIT_STATUS,
                "interrupt_reason": None,
            }
        }
        send(self._address, report, timeout=self._timeout)


__all__ = ["Recorder", "isoformat_utc"]
