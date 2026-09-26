"""`Recorder`: the hook implementation `plugin.py` registers once activation
and the reachability preflight both succeed. It assembles the session report
in memory and sends it from `pytest_sessionfinish`, never per test: in one
report, or split over several for the same run when the results alone are
more than the server accepts in one body.

`pytest_sessionstart` sends a narrower start report before the first test
runs (the run's identity and start time, `finished_at: null`, no `results`)
so that a session which never finishes still leaves a row. It is wrapped in
`liveness_isolated`, never `fault_isolated`: a failed start-write must never
cost the session its results, and the finish-write inserts a complete row on
its own when no start row exists.

`pytest_runtest_logreport` accumulates every phase report, then calls
`_maybe_beat()`. It is wrapped in `accumulation_isolated`, never
`fault_isolated`: a report the plugin cannot record costs that report alone,
never the finish-write. The beat is a separately `liveness_isolated` helper
rather than a decorator on the hook, so a failing beat latches only the
liveness path. The start-write and the beats share `_liveness_disabled`, so
the whole liveness path warns at most once. `pytest_keyboard_interrupt` and
xdist's `pytest_testnodedown` only record what stopped the session, and
share `accumulation_isolated`.

`WorkerInterruptRelay` is the one piece that runs on an xdist worker: it
tells the controller that the worker was interrupted, and why.

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
from pytest_vantage.boundary import (
    accumulation_isolated,
    fault_isolated,
    liveness_isolated,
    warn,
)
from pytest_vantage.budget import encoded_cost, spend_failure_text_budget, split_results
from pytest_vantage.capture import (
    PendingResult,
    accumulate,
    assemble_results,
    isoformat_utc,
)
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
    anything that escapes it anyway, because an exception out of
    `Recorder.__init__` leaves the whole session unrecorded.
    """
    try:
        return vcs.capture(rootpath)
    except Exception:  # the same outer net vcs.py's own docstring names, never BaseException
        return vcs.VcsSnapshot(warning=_VCS_CAPTURE_ESCAPED_WARNING)


def _capture_metadata(config: pytest.Config, rootpath: Path) -> metadata.MetadataSection | None:
    """Capture the declared metadata, or `None` after one warning on any error.

    The same net as `_capture_vcs`: `metadata.capture_metadata` warns about
    every problem it expects and never raises on its own, and anything that
    escapes it costs the run its metadata, never its recording.
    """
    try:
        return metadata.capture_metadata(config, rootpath)
    except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
        warn(
            config, f"vantage: error while capturing metadata: {exc}, metadata will not be captured"
        )
        return None


# Beats are activity-driven, not a timer thread: one is only attempted from
# `pytest_runtest_logreport`, at most this often. A suite of 1,000 ~10 ms
# tests finishes well inside one interval and sends none.
_BEAT_INTERVAL_SECONDS = 30.0

# `pytest.ExitCode.INTERNAL_ERROR`: pytest itself broke, so the session has
# no orderly finish time. A plain int, compared against `int(exitstatus)`.
_INTERNAL_ERROR_EXIT_STATUS = 3

# A `pytest.exit()` message is arbitrary text; bounding the reason keeps it
# from crowding the results out of the report.
_MAX_INTERRUPT_REASON_CHARS = 1024

# The `workeroutput` key an interrupted xdist worker hands its controller the
# reason under ("" when there is none).
_WORKER_INTERRUPT_KEY = "vantage_interrupt_reason"


def _interrupted_by_a_person(stop: BaseException) -> bool:
    """Ctrl-C (a bare `KeyboardInterrupt`) or `pytest.exit()`, never one of
    the `KeyboardInterrupt` subclasses pytest and xdist raise to stop a
    session on purpose."""
    return type(stop) is KeyboardInterrupt or isinstance(stop, pytest.exit.Exception)


def _bounded_reason(text: str) -> str:
    return text[:_MAX_INTERRUPT_REASON_CHARS]


class WorkerInterruptRelay:
    """Registered on every xdist worker of a recorded session.

    Ctrl-C or `pytest.exit()` on a worker ends that worker's session, and
    the controller then stops the whole session with xdist's own
    `KeyboardInterrupt` subclass, the one it raises for `-x` too. Only the
    worker knows it was interrupted, and why, so it says so in
    `workeroutput`, which xdist hands the controller as the worker's session
    finishes (`Recorder.pytest_testnodedown`). pytest calls this hook before
    `pytest_sessionfinish`, so the reason is always in time.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._config = config

    def pytest_keyboard_interrupt(self, excinfo: pytest.ExceptionInfo[BaseException]) -> None:
        stop = excinfo.value
        workeroutput = getattr(self._config, "workeroutput", None)
        if _interrupted_by_a_person(stop) and isinstance(workeroutput, dict):
            workeroutput[_WORKER_INTERRUPT_KEY] = _bounded_reason(str(stop))


class Recorder:
    """Registered by `plugin.py::pytest_configure` once activation and the
    reachability preflight both succeed.

    - `_results` accumulates every phase report; it is resolved into
      `results[]` entries only in `pytest_sessionfinish`. `_stop` is the
      exception that ended the session early, if pytest reported one, and
      `_worker_interruption` the reason an interrupted xdist worker gave.
    - `_disabled` is the `fault_isolated` latch: once set, every reporting
      hook on this instance is a silent no-op. `_liveness_disabled` is the
      independent `liveness_isolated` latch for the start-write and the
      beats. `_accumulation_warned` only records that
      `accumulation_isolated` has warned; it never stops accumulation. No
      decorator reads or sets another's flag.
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
      Each capture warns at most once and never raises: `pytest_configure`
      leaves the session unrecorded when construction fails, and a failure
      in either capture must cost the run that section alone. No `Recorder`
      is constructed on an xdist worker, so each happens once per session.
      `_metadata` is `None` when `--vantage-metadata` was not passed, the
      declaration is missing or invalid, or capturing it failed; the
      `metadata` key is then omitted from both reports.
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
        self._accumulation_warned = False
        self._results: dict[str, PendingResult] = {}
        self._stop: BaseException | None = None
        self._worker_interruption: str | None = None
        self._last_beat_at = time.monotonic()
        self._vcs = _capture_vcs(Path(str(config.rootpath)))
        if self._vcs.warning is not None:
            warn(config, f"vantage: {self._vcs.warning}")
        self._metadata: metadata.MetadataSection | None = None
        if metadata_requested:
            self._metadata = _capture_metadata(config, Path(str(config.rootpath)))

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

    def _sections(self) -> dict[str, object]:
        """The report sections every report of the session carries alike:
        `vcs`, and `metadata` when it was captured.
        """
        sections: dict[str, object] = {"vcs": self._vcs_section()}
        metadata_section = self._metadata_section()
        if metadata_section is not None:
            sections["metadata"] = metadata_section
        return sections

    def _in_progress_run(self) -> dict[str, object]:
        """The `run` section of a report sent while the session is still
        open: the server records its start, and its results if it carries
        any, but never finishes the run from it.
        """
        return {
            "id": self._run_id,
            "started_at": isoformat_utc(self._started_at),
            "finished_at": None,
            "exit_status": None,
            "interrupted": False,
            "interrupt_reason": None,
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
            warn(
                self._config,
                f"vantage: {self._lifecycle_problem}, "
                "this session's start and heartbeats will not be recorded",
            )
            return
        report = {"run": self._in_progress_run(), **self._sections()}
        send(self._address, report, timeout=self._liveness_timeout)

    @fault_isolated
    def pytest_report_header(self) -> str:
        """Names the run id so a test harness -- or a curious human -- can
        correlate a session with the row it produced, without reaching into
        storage internals.
        """
        return f"vantage: recording run {self._run_id} to {self._address}"

    @accumulation_isolated
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """The one hook xdist forwards to the controller. Accumulates first,
        always, then offers a beat opportunity through `_maybe_beat`, which
        is isolated separately so a beat failure latches only liveness.
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

    @accumulation_isolated
    def pytest_keyboard_interrupt(self, excinfo: pytest.ExceptionInfo[BaseException]) -> None:
        """Records what stopped the session early, for `_how_it_ended`.

        pytest calls this for Ctrl-C and `pytest.exit()`, but also for its
        own deliberate stops, which it raises as `KeyboardInterrupt`
        subclasses: errors during collection, `--stepwise`, xdist's `-x`.
        All of them end with exit status 2, so the status alone cannot tell
        an interrupted session from one pytest ended on purpose.
        """
        self._stop = excinfo.value

    @pytest.hookimpl(optionalhook=True)
    @accumulation_isolated
    def pytest_testnodedown(self, node: object, error: object) -> None:
        """xdist's hook for a worker whose session has ended: keeps the
        reason an interrupted worker gave (`WorkerInterruptRelay`). Optional,
        so the plugin registers without xdist installed."""
        workeroutput = getattr(node, "workeroutput", None)
        if isinstance(workeroutput, dict) and _WORKER_INTERRUPT_KEY in workeroutput:
            self._worker_interruption = _bounded_reason(str(workeroutput[_WORKER_INTERRUPT_KEY]))

    def _how_it_ended(
        self, exit_status: int, early_stop: object = None
    ) -> tuple[datetime | None, bool, str | None]:
        """`(finished_at, interrupted, interrupt_reason)` for the finish report.

        - A bare `KeyboardInterrupt` (Ctrl-C) or `pytest.exit()` interrupted
          the session: no finish time, `interrupted` true. So did either on
          an xdist worker, which xdist turns into its own stop.
        - pytest's internal error: no finish time, not interrupted.
        - Anything else ran to an orderly end and gets its finish time,
          including pytest's own stops with exit status 2.

        The reason is the stop's message, when there is one: pytest's
        `1 error during collection`, or the text given to `pytest.exit()`.
        `-x` and `--maxfail` stop through pytest's failure path instead of
        an interrupt, so their reason is `early_stop`, the session's own
        `shouldfail` or `shouldstop`.
        """
        stop = self._stop
        if stop is not None and _interrupted_by_a_person(stop):
            return None, True, _bounded_reason(str(stop)) or None
        if stop is not None and self._worker_interruption is not None:
            return None, True, self._worker_interruption or None
        if exit_status == _INTERNAL_ERROR_EXIT_STATUS:
            return None, False, None
        if stop is not None:
            reason = _bounded_reason(str(stop)) or None
        elif isinstance(early_stop, str):
            reason = _bounded_reason(early_stop) or None
        else:
            reason = None
        return datetime.now(timezone.utc), False, reason

    @fault_isolated
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        exit_status = int(exitstatus)
        early_stop = session.shouldfail or session.shouldstop
        finished_at, interrupted, interrupt_reason = self._how_it_ended(exit_status, early_stop)

        results = assemble_results(self._results)
        if results.dropped:
            warn(self._config, f"vantage: {results.dropped} test result(s) could not be recorded")
        # The server rejects an oversized body whole, losing the entire
        # session: failure text is bounded first, then the results are split
        # over as many reports as they need.
        spend_failure_text_budget(results)

        finish_run: dict[str, object] = {
            "id": self._run_id,
            "started_at": isoformat_utc(self._started_at),
            "finished_at": isoformat_utc(finished_at) if finished_at else None,
            "exit_status": exit_status,
            "interrupted": interrupted,
            "interrupt_reason": interrupt_reason,
        }
        in_progress_run = self._in_progress_run()
        sections = self._sections()
        envelope_bytes = max(
            encoded_cost({"run": run, "results": [], **sections})
            for run in (finish_run, in_progress_run)
        )
        slices, left_out = split_results(results, envelope_bytes=envelope_bytes)
        if left_out:
            warn(
                self._config,
                f"vantage: {left_out} test result(s) too large for any report were left out",
            )
        # Every slice but the last goes out in an in-progress report, so the
        # server stores its results without finishing the run. The run reads
        # as finished only once the last report, carrying the rest, arrives:
        # a report lost on the way leaves an unfinished run, never a finished
        # one with results silently missing.
        for chunk in slices[:-1]:
            report = {"run": in_progress_run, "results": chunk, **sections}
            send(self._address, report, timeout=self._timeout)
        report = {"run": finish_run, "results": slices[-1], **sections}
        send(self._address, report, timeout=self._timeout)


__all__ = ["Recorder", "WorkerInterruptRelay"]
