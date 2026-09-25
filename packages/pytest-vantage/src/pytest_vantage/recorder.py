"""`Recorder`: the hook implementation `plugin.py` registers once activation
and the reachability preflight both succeed. It assembles the session report
in memory and sends it once, from `pytest_sessionfinish`, never per test.

`pytest_sessionstart` sends a narrower start report before the first test
runs (the run's identity and start time, `finished_at: null`, no `results`)
so that a session which never finishes still leaves a row. It is wrapped in
`liveness_isolated`, never `fault_isolated`: a failed start-write must never
cost the session its results, and the finish-write inserts a complete row on
its own when no start row exists.

`pytest_runtest_logreport` accumulates every phase report, then calls
`_maybe_beat()`. The beat is a separately `liveness_isolated` helper rather
than a decorator on the hook: a beat raising inside the `fault_isolated` hook
body would latch `_disabled` and stop result accumulation with it. The
start-write and the beats share `_liveness_disabled`, so the whole liveness
path warns at most once.

Every other hook is wrapped in `fault_isolated`: an error anywhere in the
reporting path becomes one warning and never changes the suite's exit status.

Never imports `pytest_vantage.plugin`, which imports this module.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pytest_vantage import metadata, vcs
from pytest_vantage.boundary import _warn, fault_isolated, liveness_isolated
from pytest_vantage.budget import spend_failure_text_budget
from pytest_vantage.capture import _Pending, accumulate, assemble_results
from pytest_vantage.config import resolve_liveness_timeout
from pytest_vantage.transport import Capabilities, send, send_heartbeat

# Used only if something escapes `vcs.capture`, which handles its own failures
# and never raises. Same wording `vcs.py` uses when it cannot read the
# repository, so the user sees one phrase whichever layer caught the failure.
_VCS_CAPTURE_ESCAPED_WARNING = "could not read the git repository"


def _capture_vcs(rootpath: Path) -> vcs.VcsSnapshot:
    """Capture the VCS snapshot, degrading to an empty one on any error.

    Deliberately neither `fault_isolated` nor `liveness_isolated`: both latch,
    and a git failure must record the run with null VCS fields, not stop
    recording. `vcs.capture` never raises on its own; this net catches
    anything that escapes it anyway, because `Recorder.__init__` runs inside
    `pytest_configure`, which is not fault-isolated, and an exception there
    would surface as a pytest INTERNALERROR.
    """
    try:
        return vcs.capture(rootpath)
    except Exception:  # the same outer net vcs.py's own docstring names, never BaseException
        return vcs.VcsSnapshot(warning=_VCS_CAPTURE_ESCAPED_WARNING)


# Beats are activity-driven, not a timer thread: one is only attempted from
# `pytest_runtest_logreport`, at most this often. A suite of 1,000 ~10 ms
# tests finishes well inside one interval and sends none.
_BEAT_INTERVAL_SECONDS = 30.0

# `pytest.ExitCode.INTERRUPTED` (2) and `pytest.ExitCode.INTERNAL_ERROR` (3):
# the session did not end in an orderly way, so `finished_at` is sent as null.
# Plain ints, compared against `int(exitstatus)`.
_NULL_FINISH_EXIT_STATUSES = frozenset({2, 3})
_INTERRUPTED_EXIT_STATUS = 2


def isoformat_utc(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC text: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`.

    `datetime.isoformat()` omits the microseconds when they are exactly zero;
    `strftime("%f")` always emits six digits. Fixed width keeps lexicographic
    order equal to chronological order.
    """
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


class Recorder:
    """Registered by `plugin.py::pytest_configure` once activation and the
    reachability preflight both succeed.

    - `_results` accumulates every phase report; it is resolved into
      `results[]` entries only in `pytest_sessionfinish`.
    - `_disabled` is the `fault_isolated` latch: once set, every reporting
      hook on this instance is a silent no-op. `_liveness_disabled` is the
      independent `liveness_isolated` latch for the start-write and the
      beats. Neither decorator reads or sets the other's flag.
    - `_started_at` is captured once, so the start report and the finish
      report carry the identical value.
    - `_liveness_timeout` bounds the small start-write and heartbeat
      requests, which must not wait as long as the full finish-write report
      is allowed to.
    - `_last_beat_at` starts at construction: the start-write already
      refreshes the server's last-contact time, so the first beat is due one
      full interval later, not at the first test.
    - `_lifecycle_available` is what the plugin's capability probe found.
      When `False`, the server cannot accept a start-write or heartbeat, so
      neither is sent; `pytest_sessionfinish` records exactly as it would
      otherwise. `_lifecycle_problem` is the probe's reason, for the one
      warning that says so.
    - `_vcs` and `_metadata` are captured once here and never re-read, so
      both reports describe the same repository state and the same metadata.
      Each capture warns at most once and never raises, which is what makes
      calling them from `__init__` (inside the unwrapped `pytest_configure`)
      safe. No `Recorder` is constructed on an xdist worker, so each happens
      once per session. `_metadata` is `None` when `--vantage-metadata` was
      not passed or the declaration is missing or invalid; the `metadata`
      key is then omitted from both reports.
    """

    def __init__(
        self,
        config: pytest.Config,
        address: str,
        timeout: float,
        *,
        lifecycle_available: bool | Capabilities = False,
        metadata_requested: bool = False,
    ) -> None:
        self._config = config
        self._address = address
        self._timeout = timeout
        self._liveness_timeout = resolve_liveness_timeout(timeout)
        # The probe's `Capabilities` carries why the lifecycle is off; a plain
        # bool, from a caller with no probe, reads as a negative answer.
        self._lifecycle_available = bool(lifecycle_available)
        problem = (
            lifecycle_available.problem if isinstance(lifecycle_available, Capabilities) else None
        )
        self._lifecycle_problem = problem or f"{address} does not advertise the session lifecycle"
        self._run_id = uuid.uuid4().hex
        self._started_at = datetime.now(timezone.utc)
        self._disabled = False
        self._liveness_disabled = False
        self._results: dict[str, _Pending] = {}
        self._last_beat_at = time.monotonic()
        self._vcs = _capture_vcs(Path(str(config.rootpath)))
        if self._vcs.warning is not None:
            _warn(config, f"vantage: {self._vcs.warning}")
        self._metadata: metadata.MetadataSection | None = None
        if metadata_requested:
            self._metadata = metadata.capture_metadata(config, Path(str(config.rootpath)))

    def _vcs_section(self) -> dict[str, object]:
        """Serialises the snapshot captured in `__init__`."""
        return {
            "commit": self._vcs.commit,
            "branch": self._vcs.branch,
            "commit_subject": self._vcs.commit_subject,
            "dirty": self._vcs.dirty,
            "root": self._vcs.root,
        }

    def _metadata_section(self) -> dict[str, object] | None:
        """Serialises the metadata captured in `__init__`, or `None` when there
        is none -- the caller then omits the `metadata` key rather than
        sending it as `null`.
        """
        if self._metadata is None:
            return None
        return {
            "declaration": self._metadata.declaration,
            "files": [
                {
                    "path": entry.path,
                    "format": entry.format,
                    "status": entry.status,
                    "keys": list(entry.keys),
                    "content": entry.content,
                }
                for entry in self._metadata.files
            ],
        }

    @liveness_isolated
    def pytest_sessionstart(self) -> None:
        """Reports the session's identity and start time before the first
        test runs: `finished_at: null`, `exit_status: null`, no `results`.

        When `_lifecycle_available` is `False`, sends nothing: warns once,
        with the probe's reason, and latches `_liveness_disabled` directly so
        every later `_maybe_beat` is a silent no-op without a second warning.
        """
        if not self._lifecycle_available:
            self._liveness_disabled = True
            _warn(
                self._config,
                f"vantage: {self._lifecycle_problem}, "
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
            "vcs": self._vcs_section(),
        }
        metadata_section = self._metadata_section()
        if metadata_section is not None:
            report["metadata"] = metadata_section
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
        """The one hook xdist forwards to the controller. Accumulates first,
        always, then offers a beat opportunity through `_maybe_beat`, which
        is isolated separately so a beat failure cannot latch `_disabled`.
        """
        accumulate(self._results, report)
        self._maybe_beat()

    @liveness_isolated
    def _maybe_beat(self) -> None:
        """Send a heartbeat if `_BEAT_INTERVAL_SECONDS` has elapsed since the
        last one.

        `_last_beat_at` is assigned before the send, so a failing or slow send
        costs one stall per interval, not one per test. `time.monotonic()`,
        never wall clock, so a clock step cannot cause a burst of beats or
        suppress them. The `_lifecycle_available` check is normally redundant
        (`pytest_sessionstart` has already latched `_liveness_disabled`) but
        keeps this method correct on its own regardless of call order.
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

        results = assemble_results(self._results)
        # Applied before the report is built: the server rejects an oversized
        # body whole, losing the entire session, so failure text has to be
        # bounded client-side.
        spend_failure_text_budget(results)

        report: dict[str, object] = {
            "run": {
                "id": self._run_id,
                "started_at": isoformat_utc(self._started_at),
                "finished_at": isoformat_utc(finished_at) if finished_at else None,
                "exit_status": exit_status,
                "interrupted": exit_status == _INTERRUPTED_EXIT_STATUS,
                "interrupt_reason": None,
            },
            "results": results,
            "vcs": self._vcs_section(),
        }
        metadata_section = self._metadata_section()
        if metadata_section is not None:
            report["metadata"] = metadata_section
        send(self._address, report, timeout=self._timeout)


__all__ = ["Recorder", "isoformat_utc"]
