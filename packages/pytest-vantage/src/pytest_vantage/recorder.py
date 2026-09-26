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

`WorkerInterruptRelay` and `WorkerMetadataRelay` are the pieces that run on
an xdist worker: they tell the controller that the worker was interrupted,
and why, and what metadata its session fixtures reported.

Every other hook is wrapped in `fault_isolated`: an error anywhere in the
reporting path becomes one warning and never changes the suite's exit status.

Where the finished run goes depends on the mode (`config.MODES`): to the
server, to a local SQLite database through `pytest_vantage.local`, or to
both, with the outbox (`pytest_vantage.outbox`) keeping what a server could
not take until a later session reaches it again.

Never imports `pytest_vantage.plugin`, which imports this module.
"""

from __future__ import annotations

import sys
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
from pytest_vantage.config import (
    LOCAL_MODE,
    MODES,
    SERVER_AND_LOCAL_MODE,
    SERVER_MODE,
    resolve_liveness_timeout,
)
from pytest_vantage.session_metadata import (
    ReportedValues,
    SessionMetadata,
    ValuesPlan,
    plan_values,
    plan_warnings,
    relay,
    unrelay,
)
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


def _capture_metadata(
    config: pytest.Config, rootpath: Path, *, read_files: bool
) -> metadata.MetadataSection | None:
    """Read the declaration, and the files it names if `read_files`, or
    `None` after one warning on any error.

    The same net as `_capture_vcs`: `metadata.capture_metadata` warns about
    every problem it expects and never raises on its own, and anything that
    escapes it costs the run its declaration, never its recording.
    """
    try:
        return metadata.capture_metadata(config, rootpath, read_files=read_files)
    except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
        warn(
            config,
            f"vantage: error while reading {metadata.DECLARATION_FILENAME}: {exc}, "
            "the declaration is ignored",
        )
        return None


# Beats are activity-driven, not a timer thread: one is only attempted from
# `pytest_runtest_logreport`, at most this often. A suite of 1,000 ~10 ms
# tests finishes well inside one interval and sends none. The limit that
# comes with it: nothing is sent while one test, fixture or collection runs,
# so a stretch without a report longer than the server's grace period reads
# as abandoned until the next report arrives.
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

# The `workeroutput` key an xdist worker hands its controller the metadata
# its session reported under.
_WORKER_METADATA_KEY = "vantage_metadata"

# The `workerinput` key a recording controller hands each xdist worker.
# `--vantage` reaches a worker whether or not its controller records, and
# only the controller probes the server.
_CONTROLLER_RECORDS_KEY = "vantage_records"


def controller_records(config: pytest.Config) -> bool:
    """Whether the controller of the xdist worker `config` belongs to
    records the session (`Recorder.pytest_configure_node`)."""
    workerinput = getattr(config, "workerinput", None)
    return isinstance(workerinput, dict) and workerinput.get(_CONTROLLER_RECORDS_KEY) is True


def _interrupted_by_a_person(stop: BaseException) -> bool:
    """Ctrl-C (a bare `KeyboardInterrupt`) or `pytest.exit()`, never one of
    the `KeyboardInterrupt` subclasses pytest and xdist raise to stop a
    session on purpose."""
    return type(stop) is KeyboardInterrupt or isinstance(stop, pytest.exit.Exception)


def _bounded_reason(text: str) -> str:
    return text[:_MAX_INTERRUPT_REASON_CHARS]


def _fixture_mapping(config: pytest.Config) -> SessionMetadata:
    """The mapping the `vantage_metadata` fixture hands out in a recorded
    session. A key it skips is the project's mistake, so the warning
    points at the project's line that set it."""
    return SessionMetadata(warn=lambda message: warn(config, message, at_project_line=True))


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


class WorkerMetadataRelay:
    """Registered on every xdist worker whose controller records
    (`controller_records`); the fixture of any other worker records nothing
    and warns about nothing, as an unrecorded controller's does.

    Session fixtures run on each worker, not on the controller, so the
    `vantage_metadata` fixture there hands out this relay's
    `session_metadata`. As the worker's session finishes, the values go in
    `workeroutput`, which xdist hands the controller
    (`Recorder.pytest_testnodedown`). They already carry their wire status,
    so an over-long value never crosses the hop.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._disabled = False
        self.session_metadata = _fixture_mapping(config)

    @fault_isolated
    def pytest_sessionfinish(self) -> None:
        workeroutput = getattr(self._config, "workeroutput", None)
        if isinstance(workeroutput, dict) and self.session_metadata:
            workeroutput[_WORKER_METADATA_KEY] = relay(self.session_metadata.entries())


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
      every report describes the same repository state and the same
      declaration. Each capture warns at most once and never raises:
      `pytest_configure` leaves the session unrecorded when construction
      fails, and a failure in either capture must cost the run that section
      alone. No `Recorder` is constructed on an xdist worker, so each
      happens once per session. The declaration is read in every recorded
      session, the files it names only when `metadata_requested`
      (`--vantage-metadata`); `_metadata` is `None` when the declaration is
      missing or invalid, or reading it failed.
    - `session_metadata` is what the `vantage_metadata` fixture hands the
      tests this process runs; `_reported` collects what xdist workers
      relay. Both are sent only in the run's last report.
    - `_mode` says where the finished run goes, `_local_database` where a
      local mode stores it. `address` is `None` only in `local` mode, which
      never touches the network. `server_reachable` is `False` when a
      mode with a local copy found the server unreachable at the start:
      the run is then recorded all the same, with the lifecycle off and no
      warning about it until the one describing where the run went.
    """

    def __init__(
        self,
        config: pytest.Config,
        address: str | None,
        timeout: float,
        *,
        lifecycle_available: bool | Capabilities = False,
        metadata_requested: bool = False,
        mode: str = SERVER_MODE,
        local_database: Path | None = None,
        server_reachable: bool = True,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if (address is None) != (mode == LOCAL_MODE):
            raise ValueError(f"mode {mode} needs {'no' if mode == LOCAL_MODE else 'a'} server")
        if (local_database is None) != (mode == SERVER_MODE):
            raise ValueError(f"mode {mode} needs {'no' if mode == SERVER_MODE else 'a'} database")
        self._config = config
        self._address = address
        self._timeout = timeout
        self._mode = mode
        self._local_database = local_database
        self._server_reachable = server_reachable and address is not None
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
        self._metadata = _capture_metadata(
            config, Path(str(config.rootpath)), read_files=metadata_requested
        )
        self.session_metadata = _fixture_mapping(config)
        self._reported = ReportedValues()

    def _vcs_section(self) -> dict[str, object]:
        """Serialises the snapshot captured in `__init__`."""
        return {
            "commit": self._vcs.commit,
            "branch": self._vcs.branch,
            "commit_subject": self._vcs.commit_subject,
            "dirty": self._vcs.dirty,
            "root": self._vcs.root,
        }

    def _sections(self, plan: ValuesPlan | None = None) -> dict[str, object]:
        """The report sections: `vcs`, and `metadata` when it says anything.
        Every report carries the same declared keys and files; only the
        last carries `plan`'s values. The `metadata` key is omitted rather
        than sent as `null`.
        """
        sections: dict[str, object] = {"vcs": self._vcs_section()}
        metadata_section = metadata.wire_section(
            self._metadata,
            values=() if plan is None else plan.values,
            named_keys=() if plan is None else plan.named_keys,
        )
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
        Without a server that was reachable at the start there is nothing to
        warn about here: the run's one warning comes at the finish.
        """
        if not self._lifecycle_available or not self._server_reachable:
            self._liveness_disabled = True
            if self._server_reachable:
                warn(
                    self._config,
                    f"vantage: {self._lifecycle_problem}, "
                    "this session's start and heartbeats will not be recorded",
                )
            return
        report = {"run": self._in_progress_run(), **self._sections()}
        send(self._server(), report, timeout=self._liveness_timeout)

    def _server(self) -> str:
        if self._address is None:
            raise RuntimeError("local mode has no server to report to")
        return self._address

    @fault_isolated
    def pytest_report_header(self) -> str:
        """Names the run id so a test harness -- or a curious human -- can
        correlate a session with the row it produced, without reaching into
        storage internals.
        """
        if self._mode == LOCAL_MODE:
            where = str(self._local_database)
        elif self._mode == SERVER_AND_LOCAL_MODE:
            where = f"{self._address} and {self._local_database}"
        else:
            where = str(self._address)
        return f"vantage: recording run {self._run_id} to {where}"

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
        if not self._lifecycle_available or not self._server_reachable:
            return
        now = time.monotonic()
        if now - self._last_beat_at < _BEAT_INTERVAL_SECONDS:
            return
        self._last_beat_at = now
        send_heartbeat(self._server(), self._run_id, timeout=self._liveness_timeout)

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
    def pytest_configure_node(self, node: object) -> None:
        """xdist's hook for a worker about to start: tells it that this
        session is recorded (`controller_records`). Optional, so the plugin
        registers without xdist installed."""
        workerinput = getattr(node, "workerinput", None)
        if isinstance(workerinput, dict):
            workerinput[_CONTROLLER_RECORDS_KEY] = True

    @pytest.hookimpl(optionalhook=True)
    @accumulation_isolated
    def pytest_testnodedown(self, node: object, error: object) -> None:
        """xdist's hook for a worker whose session has ended: keeps the
        reason an interrupted worker gave (`WorkerInterruptRelay`) and merges
        the metadata it reported (`WorkerMetadataRelay`). Optional, so the
        plugin registers without xdist installed.

        xdist calls it twice for an interrupted worker; merging the same
        values again changes nothing."""
        workeroutput = getattr(node, "workeroutput", None)
        if not isinstance(workeroutput, dict):
            return
        if _WORKER_INTERRUPT_KEY in workeroutput:
            self._worker_interruption = _bounded_reason(str(workeroutput[_WORKER_INTERRUPT_KEY]))
        self._reported.add(unrelay(workeroutput.get(_WORKER_METADATA_KEY)))

    def _plan_values(self) -> ValuesPlan | None:
        """What the last report sends of the session's values, after the
        warnings the plan calls for; `None` after one warning on any error.

        The same net as `_capture_metadata`: a failure here costs the run
        its values, never its finish report. The values of this process's
        tests follow those xdist workers relayed; a session has one or the
        other.
        """
        try:
            self._reported.add(self.session_metadata.entries())
            plan = plan_values(self._reported.entries(), self._metadata)
            for message in plan_warnings(plan, self._reported.conflicting):
                warn(self._config, message)
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            warn(
                self._config,
                f"vantage: error while reporting metadata: {exc}, "
                "the session's values will not be sent",
            )
            return None
        return plan

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
        reports = self._finish_reports(session, int(exitstatus))
        if self._mode == SERVER_MODE:
            for report in reports:
                send(self._server(), report, timeout=self._timeout)
        elif self._mode == LOCAL_MODE:
            problem = self._store_locally(reports)
            if problem is not None:
                warn(
                    self._config,
                    f"vantage: could not store this run in {self._local_database}: {problem}; "
                    "the run is lost",
                )
        else:
            self._deliver_with_local_copy(reports)

    def _finish_reports(self, session: pytest.Session, exit_status: int) -> list[dict[str, object]]:
        """The session's finish reports, in send order: every one but the
        last is an in-progress report carrying a slice of the results."""
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
        last_sections = self._sections(self._plan_values())
        envelope_bytes = max(
            encoded_cost({"run": run, "results": [], **sections})
            for run in (finish_run, in_progress_run)
        )
        last_extra_bytes = encoded_cost(
            {"run": finish_run, "results": [], **last_sections}
        ) - encoded_cost({"run": finish_run, "results": [], **sections})
        slices, left_out = split_results(
            results, envelope_bytes=envelope_bytes, last_extra_bytes=last_extra_bytes
        )
        if left_out:
            warn(
                self._config,
                f"vantage: {left_out} test result(s) too large for any report were left out",
            )
        # Every slice but the last goes out in an in-progress report, so the
        # server stores its results without finishing the run. The run reads
        # as finished only once the last report, carrying the rest, arrives:
        # a report lost on the way leaves an unfinished run, never a finished
        # one with results silently missing. The session's values are sent
        # once, in the last report. A local store is handed the same reports,
        # so it holds exactly what a server would.
        reports: list[dict[str, object]] = [
            {"run": in_progress_run, "results": chunk, **sections} for chunk in slices[:-1]
        ]
        reports.append({"run": finish_run, "results": slices[-1], **last_sections})
        return reports

    def _store_locally(self, reports: list[dict[str, object]]) -> str | None:
        """Store the run in the local database; what went wrong, or `None`.
        A failure here costs the local copy alone, never the server path."""
        from pytest_vantage import local

        try:
            local.store_reports(self._local_database_path(), reports)
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            return str(exc) or type(exc).__name__
        return None

    def _local_database_path(self) -> Path:
        if self._local_database is None:
            raise RuntimeError("server mode has no local database")
        return self._local_database

    def _deliver_with_local_copy(self, reports: list[dict[str, object]]) -> None:
        """`server+backup` and `server+local`: report to the server, store
        locally when the mode or a failure calls for it, queue what a later
        session could still deliver, and say what happened in one warning.

        Only a failure a retry can fix is queued: no answer, a 5xx, or a
        408 or 429 asking for the report again later. Any other 4xx, a
        redirect or an answer that does not acknowledge the run would fail
        the same way every time. Reports the server has acknowledged are not
        queued again.
        """
        from pytest_vantage.outbox import unreachable, worth_retrying

        address = self._server()
        queue_from: int | None = None
        failure = f"{address} is unreachable"
        rejection: Exception | None = None
        if not self._server_reachable:
            queue_from = 0
        else:
            for index, report in enumerate(reports):
                try:
                    send(address, report, timeout=self._timeout)
                except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
                    if worth_retrying(exc):
                        queue_from = index
                        failure = (
                            f"{address} is unreachable ({exc})"
                            if unreachable(exc)
                            else f"{address} did not take this run ({exc})"
                        )
                    else:
                        rejection = exc
                    break
        delivered = queue_from is None and rejection is None
        stored: str | None = None
        store_problem: str | None = None
        if self._mode == SERVER_AND_LOCAL_MODE or not delivered:
            store_problem = self._store_locally(reports)
            if store_problem is None:
                stored = str(self._local_database)

        if queue_from is not None:
            self._queue(failure, reports[queue_from:], stored, store_problem)
        elif rejection is not None:
            where = (
                f"this run was stored in {stored} only"
                if stored is not None
                else f"it could not be stored in {self._local_database} either "
                f"({store_problem}), so the run is lost"
            )
            warn(self._config, f"vantage: error while reporting: {rejection}; {where}")
        elif store_problem is not None:
            warn(
                self._config,
                f"vantage: could not store this run in {self._local_database}: {store_problem}",
            )
        if delivered:
            self._send_queued()

    def _queue(
        self,
        failure: str,
        reports: list[dict[str, object]],
        stored: str | None,
        store_problem: str | None,
    ) -> None:
        """Put `reports` in the outbox and warn once, saying where the run is."""
        from pytest_vantage.outbox import Outbox, outbox_path

        database = self._local_database_path()
        path = outbox_path(database)
        waiting: int | None = None
        queue_problem: str | None = None
        evicted: list[str] = []
        try:
            with Outbox(path) as outbox:
                waiting = outbox.enqueue(self._server(), self._run_id, reports)
                evicted = outbox.evicted
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            queue_problem = str(exc) or type(exc).__name__
        if evicted:
            noun = "run" if len(evicted) == 1 else f"{len(evicted)} runs"
            warn(
                self._config,
                f"vantage: the outbox {path} is full; dropped the oldest queued {noun} "
                f"to make room: {', '.join(evicted)}",
            )
        if waiting is not None:
            queued = f"queued ({_runs(waiting)} waiting to be sent)"
            outcome = (
                f"this run was stored in {stored} and {queued}"
                if stored is not None
                else f"this run could not be stored in {database} ({store_problem}) "
                f"but was {queued}"
            )
        elif stored is not None:
            outcome = f"this run was stored in {stored} but could not be queued ({queue_problem})"
        else:
            outcome = (
                f"this run could not be stored in {database} ({store_problem}) "
                f"or queued ({queue_problem}), so it is lost"
            )
        warn(self._config, f"vantage: {failure}; {outcome}")

    def _send_queued(self) -> None:
        """Once this session's own run has reached the server, send the runs
        queued for it, within the report timeout. Never creates an outbox
        only to find it empty."""
        from pytest_vantage.outbox import Outbox, outbox_path, send_queued

        address = self._server()
        path = outbox_path(self._local_database_path())
        if not path.exists():
            return
        try:
            with Outbox(path) as outbox:
                if not outbox.waiting(address):
                    return
                summary = send_queued(outbox, address, timeout=self._timeout, budget=self._timeout)
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            warn(self._config, f"vantage: could not send the runs queued in {path}: {exc}")
            return
        for run_id in summary.dropped:
            warn(
                self._config,
                f"vantage: {address} rejected queued run {run_id}, which can never be sent; "
                f"it was dropped from {path}",
            )
        noun = "run" if summary.sent == 1 else "runs"
        line = (
            f"vantage: sent {summary.sent} queued {noun} to {address} ({summary.waiting} waiting)"
        )
        if summary.stopped is not None:
            line = f"{line}; stopped: {summary.stopped}"
        _say(self._config, line)


def _runs(count: int) -> str:
    return f"{count} run" if count == 1 else f"{count} runs"


def _say(config: pytest.Config, line: str) -> None:
    """One line of information, not a warning: on the terminal, or on
    stderr when no terminal reporter is registered."""
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    write_line = getattr(reporter, "write_line", None)
    if callable(write_line):
        write_line(line)
    else:
        print(line, file=sys.stderr)


__all__ = ["Recorder", "WorkerInterruptRelay", "WorkerMetadataRelay", "controller_records"]
