"""The always-loaded half of the plugin, registered via the ``pytest11`` entry point.

pytest imports this module on every invocation, activated or not, so it
implements no hook capable of a side effect: only ``pytest_addoption`` and
``pytest_configure``, plus the ``vantage_metadata`` fixture, which runs only
when a test asks for it. The recorder that reports lives in
``pytest_vantage.recorder`` and is registered through
``config.pluginmanager.register(...)`` only once ``--vantage`` is given --
pytest fires every ``pytest_*`` hook it finds on a registered plugin, so a
reporting hook here would run in every session. For the same reason the
recording modules are imported inside ``pytest_configure``, after the
activation check: a session without ``--vantage`` never pays for them.

The plugin's own code never opens the vantage database. It reports sessions
over HTTP with ``urllib`` and ``json`` -- standard library only, so
installing it can conflict with nothing in the environment it lands in. The
local modes hand a run to ``vantage.local``, which runs the server's
ingestion in-process, through ``pytest_vantage.local``, the one module that
imports ``vantage``, and only when such a mode is configured.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import pytest

from pytest_vantage.boundary import WarningPhase, configuring, warn
from pytest_vantage.config import (
    LOCAL_MODE,
    SERVER_MODE,
    VantageConfigError,
    resolve_liveness_timeout,
    resolve_local_database,
    resolve_mode,
    resolve_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_vantage.session_metadata import SessionMetadata
    from pytest_vantage.transport import Capabilities

# The preflight probe waits at most min(2.0, report timeout): it must not
# itself wait as long as a full report is allowed to.
_MAX_CONNECT_TIMEOUT = 2.0
_DEFAULT_HTTP_PORT = 80
_DEFAULT_HTTPS_PORT = 443
# `local` mode sends nothing, so no request timeout is ever read.
_UNUSED_TIMEOUT = 0.0

# The flags that turn something on, keyed by their option names.
_OPT_IN_FLAGS = {
    "vantage": "--vantage",
    "vantage_failure_text": "--vantage-failure-text",
    "vantage_metadata": "--vantage-metadata",
}

# Invocations that execute no test. Recording one would store a run that
# looks like a clean exit-0 session with nothing in it (or, under
# --setup-only, one that never finishes), so none is recorded. `cacheshow`
# does not exist under `-p no:cacheprovider`, hence the default on lookup.
_NO_TEST_OPTIONS = (
    "collectonly",
    "setuponly",
    "setupplan",
    "showfixtures",
    "show_fixtures_per_test",
    "cacheshow",
    "markers",
    "help",
)

# pytest's native TOML table hands `getini` a number for `vantage_timeout =
# 5` and refuses one registered as a string. The float type exists from
# pytest 8.4; older versions read every ini value as text, which
# `resolve_report_timeout` accepts too.
_TIMEOUT_INI_TYPE: Literal["float"] | None = "float" if pytest.version_tuple[:2] >= (8, 4) else None


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register every CLI/ini surface. Registering an option is not activating
    it -- see ``_activation_requested`` for the one thing that does.
    """
    group = parser.getgroup("vantage")
    group.addoption(
        "--vantage",
        action="store_true",
        default=False,
        help=(
            "Record this session's run to a vantage server. "
            "This is the ONLY thing that activates recording, and only when typed "
            "on the command line: addopts, PYTEST_ADDOPTS and an @file cannot give it."
        ),
    )
    group.addoption(
        "--vantage-server",
        default=None,
        metavar="URL",
        help="Where to report the run. Configures WHERE, never activates recording.",
    )
    group.addoption(
        "--vantage-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Bound, in seconds, on each reporting request. Configures WHERE/HOW, never activates.",
    )
    group.addoption(
        "--vantage-mode",
        default=None,
        metavar="MODE",
        help=(
            "Where the run goes: server (the default), local (a local SQLite database, "
            "no network), server+backup (the server, and the local database when the "
            "server cannot take the run, queued to be sent later) or server+local (both, "
            "every run). Configures WHERE, never activates recording."
        ),
    )
    group.addoption(
        "--vantage-local-database",
        default=None,
        metavar="PATH",
        help=(
            "The SQLite database the local modes store runs in; by default the one the "
            "vantage command serves with no options. Configures WHERE, never activates."
        ),
    )
    group.addoption(
        "--vantage-failure-text",
        action="store_true",
        default=False,
        help=(
            "Enable failure-text capture (traceback, failure fields, captured output) "
            "for this session. Absent by default -- capture never happens "
            "unless this flag is typed on the command line, there is no ini equivalent, "
            "and the flag cannot activate recording on its own. Stored failure "
            "text is unredacted and may contain any value a test printed or "
            "asserted, including credentials."
        ),
    )
    group.addoption(
        "--vantage-metadata",
        action="store_true",
        default=False,
        help=(
            "Read the files named by vantage-metadata.json in the project root and "
            "record the declared keys with this session. Absent by default -- capture "
            "never happens unless this flag is typed on the command line, there is no "
            "ini equivalent, and the flag cannot activate recording on its own. Declared "
            "files are read from disk and their declared values are stored; a "
            "configuration file is where credentials live by convention."
        ),
    )
    parser.addini(
        "vantage_server",
        help="Same as --vantage-server. Configures WHERE; never activates recording.",
        default=None,
    )
    parser.addini(
        "vantage_timeout",
        help="Same as --vantage-timeout. Configures WHERE/HOW; never activates recording.",
        type=_TIMEOUT_INI_TYPE,
        default=None,
    )
    parser.addini(
        "vantage_mode",
        help="Same as --vantage-mode. Configures WHERE; never activates recording.",
        default=None,
    )
    parser.addini(
        "vantage_local_database",
        help=(
            "Same as --vantage-local-database, relative to the ini file. "
            "Configures WHERE; never activates recording."
        ),
        default=None,
    )


def _preflight_reachable(address: str, timeout: float) -> bool:
    """A bare TCP connect, not an HTTP request: it sends no bytes and proves
    only that something is listening at `address`, never anything about the
    protocol or route.

    Bounded as a whole, like every request (`transport.run_within`): the
    socket timeout does not cover resolving the host name, and a resolver
    that never answers must not hold session start for its own timeout.

    `ConnectionRefusedError` (nothing listening), `socket.gaierror` (the
    host does not resolve) and the deadline's `TimeoutError` are all
    `OSError` subclasses, and the caller's response is the same either way.
    `ValueError` covers what validation already refuses -- a bad port, a
    host the socket layer cannot encode -- should anything slip past it: the
    answer is still "cannot reach", never an exception out of
    `pytest_configure`.
    """
    from pytest_vantage.transport import run_within

    try:
        parsed = urlparse(address)
        default_port = _DEFAULT_HTTPS_PORT if parsed.scheme == "https" else _DEFAULT_HTTP_PORT
        target = (parsed.hostname, parsed.port or default_port)
        run_within(timeout, lambda: socket.create_connection(target, timeout=timeout).close())
    except (OSError, ValueError):
        return False
    return True


def _typed_on_command_line(config: pytest.Config, flag: str) -> bool:
    """Whether ``flag`` itself was typed on the command line.

    pytest folds the ini ``addopts`` value and the ``PYTEST_ADDOPTS``
    environment variable into the parsed options, so ``getoption`` cannot
    tell a committed or inherited flag from a typed one;
    ``invocation_params.args`` holds only what was typed. xdist starts each
    worker from its controller's typed arguments, so a worker reaches the
    same answer. Everything after a bare ``--`` is a positional argument.
    A store-true flag has exactly one spelling; anything else that sets it,
    such as an ``@file`` of arguments, does not match and so enables
    nothing.
    """
    args = list(config.invocation_params.args)
    if "--" in args:
        args = args[: args.index("--")]
    return flag in args


def _opt_in(config: pytest.Config, name: str) -> bool:
    """Whether the opt-in flag registered as option ``name`` was given, and
    given by typing it."""
    return bool(config.getoption(name)) and _typed_on_command_line(config, _OPT_IN_FLAGS[name])


def _activation_requested(config: pytest.Config) -> bool:
    """Whether recording was activated for this session.

    ``--vantage`` is the ACTIVATION switch and the only one. The
    ``--vantage-server``/``--vantage-timeout`` options, the ``vantage_server``/
    ``vantage_timeout`` ini values and the ``VANTAGE_SERVER`` environment
    variable configure WHERE a report would go -- none of them may turn
    recording on by themselves. A config file committed by one person must
    never silently enable recording for everyone who checks the project out,
    which is why ``--vantage`` in ``addopts`` does not count either.
    """
    return _opt_in(config, "vantage")


def _failure_text_capture_requested(config: pytest.Config) -> bool:
    """Whether `EvidenceCollector` should be registered for this session:
    only when both `--vantage` and `--vantage-failure-text` are typed, with
    no ini or environment equivalent, so a committed configuration file can
    never enable capture of unredacted failure text.

    Called on both the worker and controller branches of `pytest_configure`,
    since the opt-in is session-wide. An unactivated session short-circuits
    and reads `"vantage"` alone.
    """
    return _activation_requested(config) and _opt_in(config, "vantage_failure_text")


def _metadata_capture_requested(config: pytest.Config) -> bool:
    """Whether `Recorder` should read the files the metadata declaration
    names: the same gate as `_failure_text_capture_requested`, for the
    `--vantage-metadata` flag, so a committed configuration file can never
    enable reading them. The declaration itself is read in any recorded
    session, for the keys it declares.

    Called only on the controller: no `Recorder` is ever constructed on a
    worker, so the files are read once per session regardless of worker
    count.
    """
    return _activation_requested(config) and _opt_in(config, "vantage_metadata")


def _warn_about_untyped_flags(config: pytest.Config) -> None:
    """One warning naming any opt-in flag that parsed as set but was not
    typed, so a committed ``addopts`` is visibly ignored rather than
    silently doing nothing. It names every place such a flag can come from,
    not one: which of them gave it is not known here."""
    ignored = [
        flag
        for name, flag in _OPT_IN_FLAGS.items()
        if config.getoption(name) and not _typed_on_command_line(config, flag)
    ]
    if ignored:
        warn(
            config,
            f"vantage: ignoring {', '.join(ignored)}: not typed on the command line "
            "(addopts, PYTEST_ADDOPTS or an @file); "
            "recording and capture are enabled only by flags typed there",
        )


def _session_runs_no_tests(config: pytest.Config) -> bool:
    return any(config.getoption(name, default=False) for name in _NO_TEST_OPTIONS)


@pytest.fixture(scope="session")
def vantage_metadata(pytestconfig: pytest.Config) -> SessionMetadata:
    """Run metadata this session reports: what it ran against, known only
    once a fixture has looked (a firmware version, a board revision).

    A mapping of flat dotted keys to text. Setting a dict flattens it, so
    ``vantage_metadata["fpga"] = {"firmware": "1.1.0"}`` sets
    ``fpga.firmware``. Values become text as they are set: pass a string
    when the spelling matters, since ``1.10`` becomes ``"1.1"``. A key or
    value that cannot be recorded is skipped with a warning; nothing here
    raises.

    The values go out with the run's last report only: a session killed
    before it finishes loses them. Set them in a fixture's setup rather than
    its teardown. Without ``--vantage`` the mapping records nothing.
    """
    from pytest_vantage.session_metadata import SESSION_METADATA, SessionMetadata

    recorded = pytestconfig.stash.get(SESSION_METADATA, None)
    return SessionMetadata() if recorded is None else recorded


def pytest_configure(config: pytest.Config) -> None:
    """The always-imported hook: decides what, if anything, to register.

    1. Under xdist every worker re-runs this hook as a full pytest session
       of its own, so the FIRST statement branches on ``workerinput``.
    2. **Worker branch.** ``pytest_runtest_makereport`` fires only in the
       process that ran the test, so a worker registers `EvidenceCollector`
       if failure text was requested, and returns. If recording was
       requested it also registers `WorkerInterruptRelay`, since only the
       worker knows whether Ctrl-C or ``pytest.exit()`` interrupted it, and,
       when the controller says it records, `WorkerMetadataRelay`, whose
       mapping the ``vantage_metadata`` fixture hands the worker's session
       fixtures. It never resolves an
       address, reads a timeout, probes the server or constructs a
       `Recorder`: a `Recorder` per worker would record one session as
       several runs.
    3. **Controller branch.** An invocation that runs no test is never
       recorded. Otherwise an opt-in flag that was not typed is reported
       once, and absent a typed ``--vantage`` nothing further happens: no
       plugin is registered, no socket is opened.
    4. The mode, and what it uses of the address, timeout and local
       database, are resolved and validated. An invalid value stops the
       session with a usage error naming the option: recording was asked
       for, and a value that can never work is a mistake to fix, not one to
       run past with a warning. So does a mode that stores locally without
       the ``vantage`` package to store with.
    5. If failure text was requested, the controller registers
       `EvidenceCollector` too -- a session with no xdist workers still
       needs evidence collected -- before anything that could fail or block.
       In ``local`` mode the `Recorder` is then registered, and nothing
       below happens: that mode never touches the network.
    6. A bare TCP preflight (``_preflight_reachable``) checks that something
       is listening at the resolved address. If not (connection refused,
       host unresolvable), ``server`` mode warns once, naming the address,
       and returns without a `Recorder`; the suite still runs to
       completion. The modes with a local copy record the session all the
       same, with no probe and no lifecycle, and keep the run locally and
       in the outbox at the finish.
    7. A capability probe (``transport.fetch_capabilities``) asks whether
       the server advertises ``session_lifecycle``, bounded by the liveness
       timeout so a hanging server cannot put the full report timeout in
       front of every session. Anything but an explicit yes, including the
       ``404`` of a server without that route, makes the `Recorder` send
       only the finish report -- no start-write or heartbeats -- rather
       than half-record against a server that cannot finish the job, and
       warn once, naming the probe's reason.
    8. This hook has no fault-isolation boundary of its own, so a failure
       constructing the `Recorder` warns once and leaves the session
       unrecorded rather than ending it as an INTERNALERROR. Once
       registered, its mapping is the one the ``vantage_metadata`` fixture
       hands out; any session left unrecorded gets one nobody reads.

    A server that passes the preflight and later disappears or fails is
    handled by ``pytest_vantage.boundary``'s fault-isolation decorator on
    every ``Recorder`` hook, not by this function.

    pytest records no warning raised here, so the controller branch runs
    under ``configuring``, and ``warn`` puts its warnings in the warnings
    summary; a registered `Recorder` brings a `WarningPhase` that does the
    same for the rest of the session.
    """
    if hasattr(config, "workerinput"):
        if _activation_requested(config):
            from pytest_vantage.recorder import (
                WorkerInterruptRelay,
                WorkerMetadataRelay,
                controller_records,
            )
            from pytest_vantage.session_metadata import SESSION_METADATA

            config.pluginmanager.register(WorkerInterruptRelay(config))
            if controller_records(config):
                relay = WorkerMetadataRelay(config)
                config.pluginmanager.register(relay)
                config.stash[SESSION_METADATA] = relay.session_metadata
        if _failure_text_capture_requested(config):
            from pytest_vantage.evidence import EvidenceCollector

            config.pluginmanager.register(EvidenceCollector(config))
        return
    with configuring():
        _configure_controller(config)


def _configure_controller(config: pytest.Config) -> None:
    """`pytest_configure` on the controller, from step 3 on."""
    if _session_runs_no_tests(config):
        return
    _warn_about_untyped_flags(config)
    if not _activation_requested(config):
        return
    try:
        mode = resolve_mode(config)
        # Only what the mode uses is read: `local` never reads the address,
        # `server` never the local database.
        settings = None if mode == LOCAL_MODE else resolve_settings(config)
        database = None if mode == SERVER_MODE else _local_database(config, mode)
    except VantageConfigError as exc:
        raise pytest.UsageError(str(exc)) from None
    if _failure_text_capture_requested(config):
        from pytest_vantage.evidence import EvidenceCollector

        config.pluginmanager.register(EvidenceCollector(config))
    if settings is None:
        # No network at all: no preflight, no probe, no start report.
        _register_recorder(config, None, _UNUSED_TIMEOUT, mode=mode, database=database)
        return
    connect_timeout = min(_MAX_CONNECT_TIMEOUT, settings.timeout)
    reachable = _preflight_reachable(settings.address, connect_timeout)
    if not reachable and database is None:
        warn(config, f"vantage: cannot reach {settings.address}, this session will not be recorded")
        return
    capabilities: bool | Capabilities = False
    if reachable:
        from pytest_vantage.transport import fetch_capabilities

        liveness_timeout = resolve_liveness_timeout(settings.timeout)
        capabilities = fetch_capabilities(settings.address, timeout=liveness_timeout)
    _register_recorder(
        config,
        settings.address,
        settings.timeout,
        mode=mode,
        database=database,
        capabilities=capabilities,
        reachable=reachable,
        token=settings.token,
    )


def _local_database(config: pytest.Config, mode: str) -> Path:
    """The local database a local mode stores in, once `vantage` is known
    to be importable: a mode that stores locally cannot work without it."""
    from pytest_vantage import local

    try:
        local.require(mode)
    except local.LocalStorageUnavailableError as exc:
        raise VantageConfigError(str(exc)) from None

    def default() -> Path:
        try:
            return local.default_database_path()
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            raise VantageConfigError(
                f"vantage: cannot find the default local database ({exc}); "
                "pass --vantage-local-database"
            ) from None

    return resolve_local_database(config, default=default)


def _register_recorder(
    config: pytest.Config,
    address: str | None,
    timeout: float,
    *,
    mode: str,
    database: Path | None,
    capabilities: bool | Capabilities = False,
    reachable: bool = True,
    token: str | None = None,
) -> None:
    """`pytest_configure` has no fault-isolation boundary of its own, so a
    failure constructing the `Recorder` warns once and leaves the session
    unrecorded rather than ending it as an INTERNALERROR."""
    from pytest_vantage.recorder import Recorder
    from pytest_vantage.session_metadata import SESSION_METADATA

    try:
        recorder = Recorder(
            config,
            address,
            timeout,
            lifecycle_available=capabilities,
            metadata_requested=_metadata_capture_requested(config),
            mode=mode,
            local_database=database,
            server_reachable=reachable,
            token=token,
        )
    except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
        warn(
            config, f"vantage: could not start recording: {exc}, this session will not be recorded"
        )
        return
    config.pluginmanager.register(WarningPhase())
    config.pluginmanager.register(recorder)
    config.stash[SESSION_METADATA] = recorder.session_metadata
