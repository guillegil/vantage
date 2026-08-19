"""`Recorder` -- the hook implementation registered by `plugin.py` only once
activation and the reachability preflight both succeed (design.md D2a, D6).
Assembles the session report and sends it exactly once, from
`pytest_sessionfinish`, never per test (RQ-25's shape).

`pytest_sessionstart` (design.md D32) sends a second, narrower report before
the first test runs: the session's identity and start time, `finished_at:
null`, no `results`. It is wrapped in
`pytest_vantage.boundary.liveness_isolated`, never `fault_isolated` -- a
failed start-write must never cost the session its results. Its loss costs
only the observability of a session that never finishes; the finish-write's
own `INSERT` branch produces exactly today's complete row when no start row
exists, which is what makes degrading quietly past one warning acceptable
rather than a new half-state.

`pytest_runtest_logreport` accumulates every phase report in memory (design.md
D16) and, from Phase 4, calls `_maybe_beat()` afterwards -- a separately
`liveness_isolated`-wrapped helper, never a decorator on the hook itself,
because a beat that raised inside the already-`fault_isolated` hook body
would be caught by the OUTER decorator and latch `_disabled`, taking result
accumulation down with it (design.md D30). `accumulate` runs first, always,
so a beat failure can never precede the accumulation of the report that
triggered it. `_last_beat_at` is assigned before the send, not after, so a
failing or slow send is not retried on the very next report -- one stall per
interval, never one per test. `time.monotonic()` only, never wall clock, so
an NTP step or a DST transition cannot fire a beat storm or suppress beats
for an hour. `_maybe_beat` shares `liveness_isolated`'s `_liveness_disabled`
flag with `pytest_sessionstart` (design.md D29): a failed start-write already
latches the liveness path before the first beat opportunity, so a session
whose start-write fails never attempts a beat at all, and either way the
whole liveness path is at most one warning, never one per attempt.

Every other hook is wrapped in `pytest_vantage.boundary.fault_isolated`: an
error raised anywhere in the reporting path -- assembling the report,
sending it, a server that never answers -- becomes one warning and never the
suite's exit status (RQ-21).

Never imports `pytest_vantage.plugin`: registration is the plugin's job, not
this module's (RQ-24 keeps every import here to stdlib and `pytest`, same as
the rest of this package).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest

from pytest_vantage.boundary import _warn, fault_isolated, liveness_isolated
from pytest_vantage.capture import _Pending, accumulate, assemble_results
from pytest_vantage.config import resolve_liveness_timeout
from pytest_vantage.transport import send, send_heartbeat

# design.md D30: activity-driven, not a timer thread -- a beat is only ever
# attempted from inside `pytest_runtest_logreport`, at most this often.
# RQ-25's measured profile (1,000 tests at ~10 ms, a ~10-second suite) never
# reaches this interval, so it fires zero beats.
_BEAT_INTERVAL_SECONDS = 30.0

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

    One finish-write per session (RQ-25): the report is assembled in memory
    and sent exactly once, from `pytest_sessionfinish`. `_results`
    accumulates every phase report `pytest_runtest_logreport` receives
    (design.md D16). `_config` is kept only so a hook that fails can route
    its warning through the terminal reporter (`boundary._warn`); `_disabled`
    is the fault-isolation latch `fault_isolated` reads and sets -- once
    `True`, every further reporting hook call on this instance is a silent
    no-op. `_liveness_disabled` is the independent latch `liveness_isolated`
    reads and sets for `pytest_sessionstart` -- it never reads or sets
    `_disabled`, and `_disabled` never reads or sets it (design.md D29).

    `_started_at` is captured once, here, so the start-write and the
    finish-write report the identical value (design.md D32) -- a second
    `datetime.now()` call in `pytest_sessionstart` would make the two
    disagree. `_liveness_timeout` is `resolve_liveness_timeout(timeout)`
    (design.md D31): a fixed ~200-byte liveness request must not be bounded
    by the same, potentially much larger, timeout the full finish-write
    report is allowed. `_last_beat_at` is captured here too, at
    `time.monotonic()` -- the start-write already establishes freshness
    (`last_contact_at = received_at`, design.md D27), so the first beat
    opportunity does not fire until a full interval after construction, not
    immediately at the first test.

    `_lifecycle_available` is what `plugin.py`'s capability probe found
    (design decisions D38-D42, `plugin-server-compatibility`), defaulting to
    `True` for a caller that never passes it (this module's own direct-
    construction tests predate the probe). `False` means the server cannot
    finish the job a start-write or heartbeat would begin -- both
    `pytest_sessionstart` and `_maybe_beat` return immediately rather than
    attempting either, and `pytest_sessionstart` warns exactly once, on the
    liveness latch, before doing so (D41: the previous release's shape, not
    a new half-state). `pytest_sessionfinish` is untouched either way -- that
    is what makes "degraded" mean "record exactly as before", not a third
    state.
    """

    def __init__(
        self,
        config: pytest.Config,
        address: str,
        timeout: float,
        *,
        lifecycle_available: bool = True,
    ) -> None:
        self._config = config
        self._address = address
        self._timeout = timeout
        self._liveness_timeout = resolve_liveness_timeout(timeout)
        self._lifecycle_available = lifecycle_available
        self._run_id = uuid.uuid4().hex
        self._started_at = datetime.now(timezone.utc)
        self._disabled = False
        self._liveness_disabled = False
        self._results: dict[str, _Pending] = {}
        self._last_beat_at = time.monotonic()

    @liveness_isolated
    def pytest_sessionstart(self) -> None:
        """Reports the session's identity and start time before the first
        test runs (design.md D32). `finished_at: null`, no `results` --
        `_UPSERT_RUN`'s insert branch (design.md D25) takes this report's
        `exit_status: None` as "no finish yet", never as an error.

        Degrades instead when `_lifecycle_available` is `False` (design
        decisions D38-D41): the server cannot finish the job a start-write
        would begin, so none is sent. Warns once here, naming the address,
        and latches `_liveness_disabled` directly -- the same flag
        `liveness_isolated` itself sets on a raised exception -- so every
        later `_maybe_beat` call this session is a silent no-op via the
        decorator's own latch check, without a second warning.
        """
        if not self._lifecycle_available:
            self._liveness_disabled = True
            _warn(
                self._config,
                f"vantage: {self._address} predates the session lifecycle, "
                "this session's start and heartbeats will not be recorded",
            )
            return
        report: dict[str, object] = {
            "run": {
                "id": self._run_id,
                "started_at": isoformat_utc(self._started_at),
                "finished_at": None,
                "exit_status": None,
                "interrupted": False,
                "interrupt_reason": None,
            },
        }
        send(self._address, report, timeout=self._liveness_timeout)

    @fault_isolated
    def pytest_report_header(self) -> str:
        """Names the run id so a test harness -- or a curious human -- can
        correlate a session with the row it produced, without reaching into
        storage internals.
        """
        return f"vantage: recording run {self._run_id} to {self._address}"

    @fault_isolated
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """The one hook xdist forwards to the controller (design.md D16,
        D19 layer 1). Accumulates first, always -- resolution into a
        `results[]` entry happens later, in `pytest_sessionfinish` -- then
        offers a beat opportunity, wrapped separately so it cannot raise
        into this already-`fault_isolated` hook body (design.md D30).
        """
        accumulate(self._results, report)
        self._maybe_beat()

    @liveness_isolated
    def _maybe_beat(self) -> None:
        """Send a heartbeat if `_BEAT_INTERVAL_SECONDS` has elapsed since the
        last one (design.md D30). `_last_beat_at` is assigned before the
        send, not after -- a failing or slow send is not retried on the very
        next report, one stall per interval rather than one per test.

        Returns immediately when `_lifecycle_available` is `False` (design
        decisions D38-D41), same as `pytest_sessionstart` -- in the ordinary
        case `pytest_sessionstart` has already latched `_liveness_disabled`
        by the time any beat opportunity arises, so `liveness_isolated`'s own
        latch check already skips this body without reaching here at all.
        This check stays anyway rather than relying on that ordering: it is
        what makes "no heartbeat when the lifecycle is unavailable" true of
        this function by its own logic, not merely a side effect of when it
        happens to be called.
        """
        if not self._lifecycle_available:
            return
        now = time.monotonic()
        if now - self._last_beat_at < _BEAT_INTERVAL_SECONDS:
            return
        self._last_beat_at = now
        send_heartbeat(self._address, self._run_id, timeout=self._liveness_timeout)

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
            },
            "results": assemble_results(self._results),
        }
        send(self._address, report, timeout=self._timeout)


__all__ = ["Recorder", "isoformat_utc"]
